import time
import random
import re
import cv2
import numpy as np
from PIL import ImageGrab
import win32gui
import win32con
import win32api


_active_stop_event = None


try:
    import pytesseract
except ImportError as exc:
    raise ImportError(
        "Thiếu pytesseract. Chạy: python -m pip install pytesseract"
    ) from exc

TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


# ============================================================
# INTERRUPTIBLE SLEEP
# ============================================================

def interruptible_sleep(seconds):
    """Wait briefly, but wake up immediately when Stop is requested."""
    if _active_stop_event is not None:
        return _active_stop_event.wait(seconds)

    time.sleep(seconds)
    return False


def random_sleep(min_seconds, max_seconds):
    """
    Sleep với thời gian ngẫu nhiên trong khoảng rất nhỏ.
    """
    return interruptible_sleep(
        random.uniform(
            min_seconds,
            max_seconds
        )
    )


# ============================================================
# TEMPLATE PATH
# ============================================================

import os
import ctypes
import sys


def resource_path(relative_path):
    """
    Lấy đường dẫn resource khi chạy:
    - Python bình thường
    - PyInstaller --onedir
    - PyInstaller --onefile
    """

    if getattr(sys, "frozen", False):
        # PyInstaller
        base_path = getattr(
            sys,
            "_MEIPASS",
            os.path.dirname(sys.executable)
        )
    else:
        # Chạy bằng Python
        base_path = os.path.dirname(
            os.path.abspath(__file__)
        )

    return os.path.join(
        base_path,
        relative_path
    )


TEMPLATE_DIR = resource_path("templates")

BUY_BULK = resource_path(
    os.path.join("templates", "buy_bulk.png")
)

CONFIRM_BULK = resource_path(
    os.path.join("templates", "confirm_bulk.png")
)

# Pattern thứ 2 cho nút "Xác nhận" khi popup Mua thất bại
# hiển thị nút đậm hơn.
CONFIRM_BULK_BOLD = resource_path(
    os.path.join("templates", "confirm_bulk_bold.png")
)

FAILURE = resource_path(
    os.path.join("templates", "fco_failure_title.png")
)

RECEIVE_NOW = resource_path(
    os.path.join("templates", "receive_now.png")
)

CONFIRM_RECEIVED = resource_path(
    os.path.join("templates", "confirm_received.png")
)

NOTIFY_SOUND = resource_path("notify.mp3")
NOTIFY_SOUND_ALIAS = "auto_fco_notify"



# ============================================================
# CAPTURE FC ONLINE
# ============================================================

def capture_fco(hwnd):

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)

    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        raise RuntimeError(
            f"Kích thước cửa sổ không hợp lệ: {width}x{height}"
        )

    img = ImageGrab.grab(
        bbox=(
            left,
            top,
            right,
            bottom
        )
    )

    arr = np.array(img)

    # RGB -> BGR
    screen = arr[:, :, ::-1].copy()

    return screen


def wait_for_valid_window(hwnd, timeout=10):
    deadline = time.time() + timeout

    while time.time() < deadline:
        if not win32gui.IsWindow(hwnd):
            raise RuntimeError("Cửa sổ FC ONLINE không còn tồn tại.")

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)

        if right > left and bottom > top:
            return

        interruptible_sleep(0.2)

    raise RuntimeError(
        "Cửa sổ FC ONLINE chưa sẵn sàng (kích thước 0x0)."
    )


# ============================================================
# LOAD TEMPLATE
# ============================================================

def load_template(path):

    template = cv2.imread(
        path,
        cv2.IMREAD_COLOR
    )

    if template is None:

        raise FileNotFoundError(
            f"Không load được template: {path}"
        )

    return template


# ============================================================
# FIND TEMPLATE
# ============================================================

def find_template(screen, template):

    if screen is None:
        return 0.0, 0, 0

    if template is None:
        return 0.0, 0, 0

    screen_h, screen_w = screen.shape[:2]

    template_h, template_w = template.shape[:2]

    # --------------------------------------------------------
    # Template phải nhỏ hơn hoặc bằng screenshot
    # --------------------------------------------------------

    if (
        template_h > screen_h
        or template_w > screen_w
    ):

        print(
            "⚠️ Template lớn hơn screenshot:"
        )

        print(
            f"   Screenshot: {screen_w}x{screen_h}"
        )

        print(
            f"   Template:  {template_w}x{template_h}"
        )

        return 0.0, 0, 0


    # --------------------------------------------------------
    # TEMPLATE MATCHING
    # --------------------------------------------------------

    result = cv2.matchTemplate(
        screen,
        template,
        cv2.TM_CCOEFF_NORMED
    )

    _, confidence, _, location = cv2.minMaxLoc(
        result
    )

    h, w = template.shape[:2]

    center_x = location[0] + w // 2
    center_y = location[1] + h // 2

    return (
        confidence,
        center_x,
        center_y
    )


def create_bold_template(template):
    gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    text_mask = cv2.inRange(gray, 0, 105)
    text_mask[:8, :] = 0
    text_mask[32:, :] = 0
    text_mask[:, :18] = 0
    text_mask[:, 122:] = 0

    expanded_mask = cv2.dilate(
        text_mask,
        np.ones((2, 2), dtype=np.uint8),
        iterations=1
    )

    bold_template = template.copy()
    bold_template[expanded_mask > 0] = (20, 20, 20)
    return bold_template


def find_buy_bulk_template(screen, templates):
    best_result = (0.0, 0, 0, "normal")

    for template, variant in templates:
        confidence, x, y = find_template(screen, template)

        if confidence > best_result[0]:
            best_result = (
                confidence,
                x,
                y,
                variant
            )

    return best_result


# ============================================================
# LOG
# ============================================================

def log_message(
    message,
    log_callback=None
):

    print(message)

    if log_callback is not None:

        log_callback(message)


# ============================================================
# CLICK CLIENT
# ============================================================

def click_client(
    hwnd,
    image_x,
    image_y,
    method="send"
):
    client_x, client_y = image_to_client(
        hwnd,
        image_x,
        image_y
    )

    lparam = win32api.MAKELONG(
        int(client_x),
        int(client_y)
    )

    print(
        f"    Click image=({image_x},{image_y}) "
        f"client=({client_x},{client_y})"
    )

    message = (
        win32gui.SendMessage
        if method == "send"
        else win32gui.PostMessage
    )

    message(
        hwnd,
        win32con.WM_MOUSEMOVE,
        0,
        lparam
    )
    random_sleep(0.025, 0.035)
    message(
        hwnd,
        win32con.WM_LBUTTONDOWN,
        win32con.MK_LBUTTON,
        lparam
    )
    random_sleep(0.025, 0.035)
    message(
        hwnd,
        win32con.WM_LBUTTONUP,
        0,
        lparam
    )
    random_sleep(0.045, 0.065)


# ============================================================
# TYPE VALUE INTO FC ONLINE INPUT
# ============================================================


# ============================================================
# KEYBOARD INPUT / FILTER SETUP
# ============================================================

def _key_lparam(vk, key_up=False):
    """
    Tạo lParam đầy đủ cho WM_KEYDOWN/WM_KEYUP:
    repeat count + scan code + extended flag + key state.
    """
    scan = win32api.MapVirtualKey(vk, 0)

    # Delete (0x2E) và Page Down (0x22) là phím extended.
    extended = 1 if vk in (
        win32con.VK_DELETE,
        win32con.VK_NEXT
    ) else 0

    value = (
        1
        | (scan << 16)
        | (extended << 24)
    )

    if key_up:
        value |= 0xC0000000

    return ctypes.c_long(value).value


def key_press(hwnd, vk, char=None):
    """
    Gửi phím với scan-code/lParam đầy đủ.
    Quan trọng với FC Online vì chỉ truyền VK + lParam=0
    có thể khiến Delete/Page Down bị game bỏ qua.
    """
    down_lparam = _key_lparam(
        vk,
        key_up=False
    )

    up_lparam = _key_lparam(
        vk,
        key_up=True
    )

    win32gui.SendMessage(
        hwnd,
        win32con.WM_KEYDOWN,
        vk,
        down_lparam
    )

    random_sleep(
        0.03,
        0.05
    )

    if char is not None:
        win32gui.SendMessage(
            hwnd,
            win32con.WM_CHAR,
            ord(char),
            0
        )

    random_sleep(
        0.02,
        0.04
    )

    win32gui.SendMessage(
        hwnd,
        win32con.WM_KEYUP,
        vk,
        up_lparam
    )


def key_release(hwnd, vk):
    """Nhả phím modifier mà không gửi thêm ký tự."""
    win32gui.SendMessage(
        hwnd,
        win32con.WM_KEYUP,
        vk,
        _key_lparam(vk, key_up=True)
    )


def select_all_input(hwnd):
    """Chọn toàn bộ nội dung của ô đang được focus."""
    win32gui.SendMessage(
        hwnd,
        win32con.WM_KEYDOWN,
        win32con.VK_CONTROL,
        _key_lparam(win32con.VK_CONTROL)
    )
    random_sleep(0.03, 0.05)
    win32gui.SendMessage(
        hwnd,
        win32con.WM_KEYDOWN,
        ord("A"),
        _key_lparam(ord("A"))
    )
    win32gui.SendMessage(
        hwnd,
        win32con.WM_KEYUP,
        ord("A"),
        _key_lparam(ord("A"), key_up=True)
    )
    key_release(hwnd, win32con.VK_CONTROL)


def clear_input(hwnd):
    """Chọn toàn bộ rồi xóa, lặp lại để tránh game bỏ sót phím nền."""
    for _ in range(2):
        select_all_input(hwnd)
        key_press(hwnd, win32con.VK_DELETE)
        key_press(hwnd, win32con.VK_BACK)
        random_sleep(0.06, 0.10)


def type_text(hwnd, text):
    """Nhập chuỗi ký tự vào ô đang focus."""
    for char in str(text):
        if char.isdigit():
            key_press(hwnd, ord(char), char)
        elif char == ".":
            key_press(hwnd, 0xBE, char)
        elif char == ",":
            key_press(hwnd, 0xBC, char)
        else:
            key_press(hwnd, ord(char.upper()), char)

        random_sleep(0.02, 0.035)


# Tọa độ theo ảnh FC Online 1280x752 hiện tại.
# Đây là tọa độ trên screenshot full window, giống các template.
REFERENCE_WIDTH = 1280
REFERENCE_HEIGHT = 752

STAT_MIN_X = 960
STAT_MIN_Y = 326

STAT_MAX_X = 1055
STAT_MAX_Y = 326

MAX_CARD_PRICE_X = 986
MAX_CARD_PRICE_Y = 449

# Số lượng mua mỗi lần (ô Số lượng trên FC Online)
QUANTITY_X = 1058
QUANTITY_Y = 509

CONFIRM_WAIT_TIMEOUT = 7
RESULT_WAIT_TIMEOUT = 7


def image_to_client(hwnd, image_x, image_y):
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    window_width = right - left
    window_height = bottom - top
    scale_x = window_width / REFERENCE_WIDTH
    scale_y = window_height / REFERENCE_HEIGHT
    scaled_x = int(image_x * scale_x)
    scaled_y = int(image_y * scale_y)

    client_left, client_top = win32gui.ClientToScreen(
        hwnd,
        (0, 0)
    )

    return (
        scaled_x - (client_left - left),
        scaled_y - (client_top - top)
    )


def double_click_input(hwnd, image_x, image_y):
    """
    Double-click vào ô nhập bằng SendMessage.
    Gửi double-click nền vào ô nhập, không di chuyển chuột thật.
    """
    client_x, client_y = image_to_client(
        hwnd,
        image_x,
        image_y
    )

    lparam = win32api.MAKELONG(
        int(client_x),
        int(client_y)
    )

    win32gui.SendMessage(
        hwnd,
        win32con.WM_MOUSEMOVE,
        0,
        lparam
    )
    random_sleep(0.05, 0.08)
    win32gui.SendMessage(
        hwnd,
        win32con.WM_LBUTTONDOWN,
        win32con.MK_LBUTTON,
        lparam
    )
    win32gui.SendMessage(
        hwnd,
        win32con.WM_LBUTTONUP,
        0,
        lparam
    )
    random_sleep(0.05, 0.08)
    win32gui.SendMessage(
        hwnd,
        win32con.WM_LBUTTONDOWN,
        win32con.MK_LBUTTON,
        lparam
    )
    win32gui.SendMessage(
        hwnd,
        win32con.WM_LBUTTONDBLCLK,
        win32con.MK_LBUTTON,
        lparam
    )
    random_sleep(0.05, 0.08)
    win32gui.SendMessage(
        hwnd,
        win32con.WM_LBUTTONUP,
        0,
        lparam
    )


def set_filter_by_double_click(
    hwnd,
    image_x,
    image_y,
    value,
    label,
    log_callback=None,
    clear_before_input=False
):
    """
    Click đúp vào ô -> nhập giá trị -> Enter.
    Không Page Down / không Delete.
    """
    log_message(
        f"    🖱️ Click đúp ô '{label}'...",
        log_callback
    )

    double_click_input(
        hwnd,
        image_x,
        image_y
    )

    random_sleep(
        0.08,
        0.12
    )

    if clear_before_input:
        clear_input(hwnd)

    log_message(
        f"    ⌨️ Nhập '{value}' vào '{label}'...",
        log_callback
    )

    type_text(
        hwnd,
        str(value)
    )

    random_sleep(
        0.05,
        0.08
    )

    key_press(
        hwnd,
        win32con.VK_RETURN,
        "\r"
    )

    random_sleep(
        0.10,
        0.15
    )


def normalize_numeric_text(value):
    """Bỏ ký tự không phải số để so sánh OCR ổn định hơn."""
    if value is None:
        return ""
    return "".join(char for char in str(value) if char.isdigit())


def numeric_match(actual, expected):
    """So khớp số OCR theo kiểu khinh khích, tránh lỗi viền/extra digit."""
    actual_text = normalize_numeric_text(actual)
    expected_text = normalize_numeric_text(expected)

    if not actual_text or not expected_text:
        return False

    if actual_text == expected_text:
        return True

    # Tesseract đôi khi đọc nhầm 124 thành 1240/1242 hoặc ghép thêm 0 ở cuối.
    # Nếu giá trị nghe có vẻ cùng mang một chuỗi số cốt lõi, coi là khớp.
    if len(actual_text) >= len(expected_text) and actual_text.startswith(expected_text):
        return True

    if len(expected_text) >= len(actual_text) and expected_text.startswith(actual_text):
        return True

    return False


def read_filter_value(
    hwnd,
    image_x,
    image_y,
    expected=None,
    roi_half_width=42
):
    """Đọc giá trị đang hiển thị trong ô lọc bằng OCR."""
    candidates = []

    for _ in range(5):
        screen = capture_fco(hwnd)
        screen_height, screen_width = screen.shape[:2]

        # Chỉ lấy vùng số, tránh OCR nhầm nhãn và ô lọc kế bên.
        left = max(0, image_x - roi_half_width)
        top = max(0, image_y - 16)
        right = min(screen_width, image_x + roi_half_width)
        bottom = min(screen_height, image_y + 16)
        roi = screen[top:bottom, left:right]

        if roi.size == 0:
            continue

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        enlarged = cv2.resize(
            gray,
            None,
            fx=3,
            fy=3,
            interpolation=cv2.INTER_CUBIC
        )
        images = [
            enlarged,
            cv2.threshold(
                enlarged,
                0,
                255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )[1]
        ]

        for image in images:
            text = pytesseract.image_to_string(
                image,
                config="--psm 7 -c tessedit_char_whitelist=0123456789",
                lang="eng"
            )
            value = normalize_numeric_text(text)
            if value:
                if expected is not None and numeric_match(value, expected):
                    return value
                candidates.append(value)

        random_sleep(0.03, 0.05)

    if not candidates:
        return ""

    return max(
        set(candidates),
        key=candidates.count
    )


def set_filter_with_verification(
    hwnd,
    image_x,
    image_y,
    value,
    label,
    log_callback=None,
    max_attempts=3,
    verification_value=None,
    verification_roi_half_width=42,
    clear_before_input=False
):
    """Nhập một ô lọc và xác minh lại giá trị bằng OCR."""
    expected_value = (
        value
        if verification_value is None
        else verification_value
    )
    expected = "".join(
        char for char in str(expected_value)
        if char.isdigit()
    )

    for attempt in range(1, max_attempts + 1):
        set_filter_by_double_click(
            hwnd,
            image_x,
            image_y,
            value,
            label,
            log_callback,
            clear_before_input=clear_before_input
        )

        actual = read_filter_value(
            hwnd,
            image_x,
            image_y,
            expected=expected,
            roi_half_width=verification_roi_half_width
        )

        if numeric_match(actual, expected):
            log_message(
                f"    ✅ Đã xác minh '{label}': {actual}",
                log_callback
            )
            return

        # OCR không đọc được thì không được kết luận ô nhập sai.
        # Giá trị đã được nhập và Enter; tiếp tục tránh dừng bot oan.
        if not actual:
            log_message(
                f"    ⚠️ Không đọc được '{label}' bằng OCR; "
                "giữ giá trị vừa nhập.",
                log_callback
            )
            return

        # Xác nhận lại một lần trước khi nhập lại để tránh OCR đọc nhầm.
        confirmed_actual = read_filter_value(
            hwnd,
            image_x,
            image_y,
            expected=actual,
            roi_half_width=verification_roi_half_width
        )
        if not numeric_match(confirmed_actual, actual):
            log_message(
                f"    ⚠️ OCR chưa ổn định cho '{label}'; "
                "giữ giá trị vừa nhập.",
                log_callback
            )
            return

        log_message(
            f"    ⚠️ '{label}' chưa đúng "
            f"(đọc được '{actual}', cần '{expected}') "
            f"- nhập lại lần {attempt}/{max_attempts}.",
            log_callback
        )

    raise RuntimeError(
        f"{label} không khớp sau {max_attempts} lần "
        f"(cần {expected})."
    )


def set_purchase_filters(
    hwnd,
    stat_min,
    stat_max,
    max_card_price,
    quantity,
    log_callback=None
):
    """
    Flow:
      1. Đọc MIN/MAX hiện tại để chọn thứ tự nhập an toàn
      2. Nhập hai chỉ số theo thứ tự phù hợp
      3. Nhập giá tối đa mỗi thẻ
      4. Quay về flow Mua hàng loạt
    """

    log_message(
        "⚙️ Đang thiết lập bộ lọc mua hàng...",
        log_callback
    )

    current_min = read_filter_value(
        hwnd,
        STAT_MIN_X,
        STAT_MIN_Y,
        roi_half_width=42
    )
    current_max = read_filter_value(
        hwnd,
        STAT_MAX_X,
        STAT_MAX_Y,
        roi_half_width=42
    )

    if current_min.isdigit() and current_max.isdigit():
        current_min_value = int(current_min)
        current_max_value = int(current_max)

        if stat_min > current_max_value:
            filter_order = ("max", "min")
        elif stat_max < current_min_value:
            filter_order = ("min", "max")
        else:
            filter_order = ("max", "min")

        log_message(
            f"    🔁 Giá trị hiện tại: MIN={current_min_value} | "
            f"MAX={current_max_value} -> nhập "
            f"{filter_order[0].upper()} trước.",
            log_callback
        )
    else:
        filter_order = ("max", "min")
        log_message(
            "    ⚠️ Không đọc được MIN/MAX hiện tại; "
            "giữ thứ tự MAX trước MIN.",
            log_callback
        )

    filter_configs = {
        "min": (
            STAT_MIN_X,
            STAT_MIN_Y,
            stat_min,
            "Chỉ số MIN"
        ),
        "max": (
            STAT_MAX_X,
            STAT_MAX_Y,
            stat_max,
            "Chỉ số MAX"
        )
    }

    for filter_name in filter_order:
        image_x, image_y, value, label = filter_configs[filter_name]
        set_filter_with_verification(
            hwnd,
            image_x,
            image_y,
            value,
            label,
            log_callback,
            clear_before_input=True
        )

    # 3. Giá tối đa mỗi thẻ
    set_filter_with_verification(
        hwnd,
        MAX_CARD_PRICE_X,
        MAX_CARD_PRICE_Y,
        max_card_price,
        "Giá tối đa mỗi thẻ",
        log_callback,
        verification_value=max_card_price * 10000,
        verification_roi_half_width=140
    )

    # 4. Số lượng mỗi lần mua - KHÔNG nằm trong profile.
    set_filter_with_verification(
        hwnd,
        QUANTITY_X,
        QUANTITY_Y,
        quantity,
        "Số lượng",
        log_callback
    )

    log_message(
        f"✅ Đã điền filter: MIN={stat_min} | "
        f"MAX={stat_max} | "
        f"Giá/thẻ={max_card_price:,} | "
        f"Số lượng={quantity}",
        log_callback
    )

# ============================================================
# FAILURE CONFIRM BUTTON
# ============================================================

def click_failure_confirm(
    hwnd,
    confirm_template,
    confirm_template_bold,
    threshold,
    stop_event=None,
    log_callback=None
):
    """
    Xử lý nút 'Xác nhận' trên popup Mua thất bại.

    Check đồng thời 2 pattern:
      1. confirm_bulk.png
      2. confirm_bulk_bold.png

    Chỉ cần một pattern đạt threshold là xem như tìm thấy nút.
    """

    # --------------------------------------------------------
    # Check 2 template liên tục trong thời gian ngắn.
    # --------------------------------------------------------

    end_time = time.time() + 1.5

    while time.time() < end_time:

        if (
            stop_event is not None
            and stop_event.is_set()
        ):
            return False

        try:
            screen = capture_fco(hwnd)

            # Pattern 1 - nút bình thường
            normal_conf, normal_x, normal_y = find_template(
                screen,
                confirm_template
            )

            # Pattern 2 - nút đậm
            bold_conf = 0.0
            bold_x = 0
            bold_y = 0

            if confirm_template_bold is not None:
                bold_conf, bold_x, bold_y = find_template(
                    screen,
                    confirm_template_bold
                )

            # Chọn pattern tốt nhất.
            if bold_conf > normal_conf:
                best_conf = bold_conf
                best_x = bold_x
                best_y = bold_y
                best_pattern = "confirm_bulk_bold.png"
            else:
                best_conf = normal_conf
                best_x = normal_x
                best_y = normal_y
                best_pattern = "confirm_bulk.png"

            if best_conf >= threshold:

                log_message(
                    f"✅ Xác nhận thất bại "
                    f"(pattern={best_pattern}, "
                    f"confidence={best_conf:.4f})",
                    log_callback
                )

                click_client(
                    hwnd,
                    best_x,
                    best_y,
                    method="send"
                )

                return True

        except Exception as exc:

            log_message(
                f"⚠️ Lỗi check 2 pattern Xác nhận: {exc}",
                log_callback
            )

        random_sleep(
            0.04,
            0.06
        )

    # --------------------------------------------------------
    # Fallback nếu cả 2 template đều không match.
    # Popup thất bại đã được nhận diện ở Step 3,
    # nên nút Xác nhận nằm ở vị trí tương đối ổn định.
    # --------------------------------------------------------

    try:

        left, top, right, bottom = win32gui.GetClientRect(hwnd)

        width = right - left
        height = bottom - top

        candidate_positions = [
            (int(width * 0.561), int(height * 0.604)),
            (int(width * 0.546), int(height * 0.598)),
            (int(width * 0.571), int(height * 0.619)),
            (int(width * 0.535), int(height * 0.610)),
            (int(width * 0.585), int(height * 0.591))
        ]

        for x, y in candidate_positions:
            try:
                log_message(
                    "⚠️ Cả 2 pattern Xác nhận không match "
                    f"→ fallback click ({x},{y})",
                    log_callback
                )

                click_client(
                    hwnd,
                    x,
                    y,
                    method="send"
                )

                random_sleep(
                    0.08,
                    0.12
                )

                log_message(
                    "✅ Đã gửi click Xác nhận fallback.",
                    log_callback
                )

                return True
            except Exception as exc:
                log_message(
                    f"❌ Fallback click Xác nhận thất bại ở ({x},{y}): {exc}",
                    log_callback
                )

        return False

    except Exception as exc:

        log_message(
            f"❌ Fallback click Xác nhận thất bại lỗi: {exc}",
            log_callback
        )

        return False


# ============================================================
# WAIT FOR TEMPLATE
# ============================================================

def wait_for_template(
    hwnd,
    template,
    timeout=5,
    threshold=0.8,
    stop_event=None,
    template_variants=None
):

    start = time.time()

    while time.time() - start < timeout:

        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        if (
            stop_event is not None
            and stop_event.is_set()
        ):

            return None


        # ----------------------------------------------------
        # WINDOW CHECK
        # ----------------------------------------------------

        if not win32gui.IsWindow(hwnd):

            return None


        # ----------------------------------------------------
        # CAPTURE
        # ----------------------------------------------------

        try:

            screen = capture_fco(hwnd)

        except Exception as e:

            print(
                f"⚠️ Capture lỗi: {e}"
            )

            random_sleep(0.09, 0.12)

            continue


        # ----------------------------------------------------
        # FIND
        # ----------------------------------------------------

        if template_variants is None:
            confidence, x, y = find_template(
                screen,
                template
            )
        else:
            confidence, x, y, _ = find_buy_bulk_template(
                screen,
                template_variants
            )


        if confidence >= threshold:

            return {
                "confidence": confidence,
                "x": x,
                "y": y
            }


        random_sleep(0.045, 0.065)


    return None


# ============================================================
# WAIT FOR RESULT
# ============================================================

def wait_for_result(
    hwnd,
    failure_template,
    receive_template,
    timeout=3,
    threshold=0.8,
    stop_event=None
):

    start = time.time()


    while time.time() - start < timeout:

        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        if (
            stop_event is not None
            and stop_event.is_set()
        ):

            return None


        # ----------------------------------------------------
        # WINDOW
        # ----------------------------------------------------

        if not win32gui.IsWindow(hwnd):

            return None


        # ----------------------------------------------------
        # CAPTURE
        # ----------------------------------------------------

        try:

            screen = capture_fco(hwnd)

        except Exception as e:

            print(
                f"⚠️ Capture lỗi: {e}"
            )

            random_sleep(0.09, 0.12)

            continue


        # ----------------------------------------------------
        # FAILURE
        # ----------------------------------------------------

        failure_conf, failure_x, failure_y = find_template(
            screen,
            failure_template
        )


        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        receive_conf, receive_x, receive_y = find_template(
            screen,
            receive_template
        )


        # ----------------------------------------------------
        # FAILURE FOUND
        # ----------------------------------------------------

        if failure_conf >= threshold:

            return (
                "failure",
                failure_conf,
                failure_x,
                failure_y
            )


        # ----------------------------------------------------
        # SUCCESS FOUND
        # ----------------------------------------------------

        if receive_conf >= threshold:

            return (
                "success",
                receive_conf,
                receive_x,
                receive_y
            )


        random_sleep(0.045, 0.065)


    return None


# ============================================================
# CLICK UNTIL DISAPPEAR
# ============================================================

def click_until_disappear(
    hwnd,
    template,
    x,
    y,
    method="send",
    threshold=0.8,
    max_attempts=2,
    wait_after_click=0.2,
    stop_event=None,
    log_callback=None
):

    for attempt in range(
        1,
        max_attempts + 1
    ):

        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        if (
            stop_event is not None
            and stop_event.is_set()
        ):

            return False


        log_message(
            f"    Click lần {attempt}/{max_attempts}",
            log_callback
        )


        # ----------------------------------------------------
        # CLICK
        # ----------------------------------------------------

        click_client(
            hwnd,
            x,
            y,
            method=method
        )


        interruptible_sleep(
            wait_after_click
        )


        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        if (
            stop_event is not None
            and stop_event.is_set()
        ):

            return False


        # ----------------------------------------------------
        # CAPTURE
        # ----------------------------------------------------

        try:

            screen = capture_fco(hwnd)

        except Exception as e:

            log_message(
                f"⚠️ Capture lỗi: {e}",
                log_callback
            )

            continue


        # ----------------------------------------------------
        # CHECK TEMPLATE
        # ----------------------------------------------------

        confidence, new_x, new_y = find_template(
            screen,
            template
        )


        # ----------------------------------------------------
        # DISAPPEARED
        # ----------------------------------------------------

        if confidence < threshold:

            log_message(
                f"    ✅ Nút đã biến mất "
                f"(confidence={confidence:.4f})",
                log_callback
            )

            return True


        # ----------------------------------------------------
        # STILL THERE
        # ----------------------------------------------------

        log_message(
            f"    ⚠️ Nút vẫn còn "
            f"(confidence={confidence:.4f})",
            log_callback
        )


        x = new_x
        y = new_y

        interruptible_sleep(
            0.15
        )


    log_message(
        "    ❌ Click quá số lần cho phép.",
        log_callback
    )

    return False


# ============================================================
# READ PURCHASE COUNT FROM SUCCESS POPUP
# ============================================================

# Relative to the full FC Online window screenshot (1296x759).
COUNT_ROI = (480, 535, 950, 660)


def read_purchase_count(hwnd, log_callback=None, retries=2):
    """
    Đọc số X trong câu:
        "Bạn đã mua được tổng cộng X cầu thủ"

    Chỉ gọi ở CASE SUCCESS.
    Tối ưu tốc độ: ưu tiên binary + PSM 6, sau đó mới fallback grayscale.
    """

    left, top, right, bottom = COUNT_ROI

    for attempt in range(1, retries + 1):

        try:
            screen = capture_fco(hwnd)

            h, w = screen.shape[:2]

            l = max(0, min(left, w))
            t = max(0, min(top, h))
            r = max(0, min(right, w))
            b = max(0, min(bottom, h))

            if r <= l or b <= t:
                return 0

            crop = screen[t:b, l:r]

            gray = cv2.cvtColor(
                crop,
                cv2.COLOR_BGR2GRAY
            )

            gray = cv2.resize(
                gray,
                None,
                fx=4,
                fy=4,
                interpolation=cv2.INTER_CUBIC
            )

            gray = cv2.GaussianBlur(
                gray,
                (3, 3),
                0
            )

            _, binary = cv2.threshold(
                gray,
                0,
                255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )

            # Theo bài test V2, binary + PSM 6 đã đọc đúng số.
            images = [
                ("BINARY", binary, "--psm 6"),
                ("GRAY", gray, "--psm 6"),
            ]

            for image_name, image, config in images:

                text = pytesseract.image_to_string(
                    image,
                    config=config,
                    lang="eng"
                ).strip()

                if log_callback and text:
                    log_callback(
                        f"🔎 OCR {image_name}: {text!r}"
                    )

                normalized = text.lower()

                normalized = (
                    normalized
                    .replace("đ", "d")
                    .replace("é", "e")
                    .replace("è", "e")
                    .replace("ê", "e")
                    .replace("ư", "u")
                    .replace("ơ", "o")
                )

                normalized = re.sub(
                    r"\s+",
                    " ",
                    normalized
                )

                # Ví dụ OCR:
                # "Ban da mua duge tong cong 2 cau tha"
                match = re.search(
                    r"tong\s+cong\s*(\d+)\D*cau",
                    normalized
                )

                if match:
                    value = int(match.group(1))

                    if log_callback:
                        log_callback(
                            f"🔢 OCR số cầu thủ: {value}"
                        )

                    return value

                # OCR có thể nhận số 1 thành | / I / l.
                match = re.search(
                    r"tong\s+cong\s*([|ilI])\s*cau",
                    normalized
                )

                if match:
                    if log_callback:
                        log_callback(
                            "🔢 OCR số cầu thủ: 1 "
                            "(OCR nhận dạng 1 thành ký tự lạ)"
                        )

                    return 1

            if log_callback:
                log_callback(
                    f"⚠️ Chưa đọc được số cầu thủ "
                    f"(lần {attempt}/{retries})"
                )

        except Exception as exc:

            if log_callback:
                log_callback(
                    f"⚠️ OCR lỗi lần {attempt}/{retries}: {exc}"
                )

        random_sleep(0.08, 0.12)

    if log_callback:
        log_callback(
            "⚠️ Không đọc được số cầu thủ → +0"
        )

    return 0


# ============================================================
# ============================================================
# PLAY NOTIFICATION SOUND
# ============================================================

def play_notification(log_callback=None):
    if not os.path.exists(NOTIFY_SOUND):
        log_message(
            f"⚠️ Không tìm thấy file nhạc: {NOTIFY_SOUND}",
            log_callback
        )
        return

    winmm = ctypes.windll.winmm
    close_command = f"close {NOTIFY_SOUND_ALIAS}"
    open_command = (
        f'open "{NOTIFY_SOUND}" type mpegvideo '
        f"alias {NOTIFY_SOUND_ALIAS}"
    )

    winmm.mciSendStringW(
        close_command,
        None,
        0,
        0
    )

    open_error = winmm.mciSendStringW(
        open_command,
        None,
        0,
        0
    )

    if open_error != 0:
        log_message(
            f"⚠️ Không mở được nhạc MP3 (mã lỗi {open_error}).",
            log_callback
        )
        return

    play_error = winmm.mciSendStringW(
        f"play {NOTIFY_SOUND_ALIAS} from 0",
        None,
        0,
        0
    )

    if play_error != 0:
        log_message(
            f"⚠️ Không phát được nhạc MP3 (mã lỗi {play_error}).",
            log_callback
        )
        return

    log_message(
        f"🔊 Đã phát nhạc thông báo: {os.path.basename(NOTIFY_SOUND)}",
        log_callback
    )


# MAIN AUTO BUY
# ============================================================

def run_auto_buy(
    hwnd,
    target_players,
    threshold,
    stat_min,
    stat_max,
    max_card_price,
    quantity,
    stop_event=None,
    log_callback=None,
    player_count_callback=None
):
    global _active_stop_event

    _active_stop_event = stop_event


    # ========================================================
    # VALIDATE HWND
    # ========================================================

    if not win32gui.IsWindow(hwnd):

        log_message(
            "❌ HWND không hợp lệ.",
            log_callback
        )

        return


    # ========================================================
    # LOAD TEMPLATES
    # ========================================================

    try:

        log_message(
            "Loading templates...",
            log_callback
        )


        buy_bulk = load_template(
            BUY_BULK
        )
        buy_bulk_templates = [
            (buy_bulk, "normal"),
            (create_bold_template(buy_bulk), "bold")
        ]

        confirm_bulk = load_template(
            CONFIRM_BULK
        )

        try:
            confirm_bulk_bold = load_template(
                CONFIRM_BULK_BOLD
            )

            log_message(
                "✅ Đã load pattern Xác nhận đậm.",
                log_callback
            )

        except Exception:
            confirm_bulk_bold = None

            log_message(
                "⚠️ Không có confirm_bulk_bold.png "
                "→ chỉ dùng pattern thường.",
                log_callback
            )

        failure = load_template(
            FAILURE
        )

        receive_now = load_template(
            RECEIVE_NOW
        )

        confirm_received = load_template(
            CONFIRM_RECEIVED
        )


    except Exception as e:

        log_message(
            f"❌ Lỗi template: {e}",
            log_callback
        )

        return


    log_message(
        "✅ Đã load toàn bộ template.",
        log_callback
    )

    log_message(
        "🔎 Xác nhận thất bại: check 2 pattern.",
        log_callback
    )

    # ========================================================
    # STEP 0
    # SET PURCHASE FILTERS
    # ========================================================

    try:
        wait_for_valid_window(hwnd)

        set_purchase_filters(
            hwnd,
            stat_min,
            stat_max,
            max_card_price,
            quantity,
            log_callback=log_callback
        )
    except Exception as exc:
        log_message(
            f"❌ Không thể thiết lập bộ lọc mua hàng: {exc}",
            log_callback
        )
        return


    # ========================================================
    # PLAYER COUNTER
    # ========================================================

    purchased_players = 0

    # --------------------------------------------------------
    if player_count_callback is not None:

        player_count_callback(
            purchased_players,
            target_players
        )


    # ========================================================
    # MAIN LOOP
    # ========================================================

    while purchased_players < target_players:

        # ----------------------------------------------------
        # STOP
        # ----------------------------------------------------

        if (
            stop_event is not None
            and stop_event.is_set()
        ):

            log_message(
                "⛔ Đã nhận yêu cầu STOP.",
                log_callback
            )

            break


        # ----------------------------------------------------
        # WINDOW CHECK
        # ----------------------------------------------------

        if not win32gui.IsWindow(hwnd):

            log_message(
                "❌ Cửa sổ FC Online không còn tồn tại.",
                log_callback
            )

            break


        # ====================================================
        # PROGRESS
        # ====================================================

        log_message(
            "",
            log_callback
        )

        log_message(
            "=" * 50,
            log_callback
        )

        log_message(
            f"👤 Đã mua: "
            f"{purchased_players}/{target_players}",
            log_callback
        )

        log_message(
            "=" * 50,
            log_callback
        )


        # ====================================================
        # STEP 1
        # MUA HÀNG LOẠT
        # ====================================================

        log_message(
            "[1] Đang tìm 'Mua hàng loạt'...",
            log_callback
        )


        # Không dùng timeout để quyết định dừng.
        # Chụp màn hình liên tục cho đến khi thấy "Mua hàng loạt".
        result = None

        while result is None:

            if (
                stop_event is not None
                and stop_event.is_set()
            ):
                log_message(
                    "⛔ Đã dừng.",
                    log_callback
                )
                break

            try:
                screen = capture_fco(hwnd)

                confidence, x, y, variant = find_buy_bulk_template(
                    screen,
                    buy_bulk_templates
                )

                if confidence >= threshold:
                    result = {
                        "confidence": confidence,
                        "x": x,
                        "y": y,
                        "variant": variant
                    }
                    break

            except Exception as exc:
                log_message(
                    f"⚠️ Lỗi kiểm tra 'Mua hàng loạt': {exc}",
                    log_callback
                )

            random_sleep(0.045, 0.065)

        if result is None:
            break


        log_message(
            f"✅ Mua hàng loạt "
            f"(variant={result['variant']}, "
            f"confidence={result['confidence']:.4f})",
            log_callback
        )


        click_client(
            hwnd,
            result["x"],
            result["y"],
            method="send"
        )


        interruptible_sleep(
            0.15
        )


        # ====================================================
        # STEP 3
        # XÁC NHẬN MUA
        # ====================================================

        if (
            stop_event is not None
            and stop_event.is_set()
        ):

            break


        log_message(
            "[2] Đang tìm 'Xác nhận' mua...",
            log_callback
        )


        # Chờ nút xác nhận trong thời gian giới hạn.
        result = None
        confirm_wait_started = time.time()
        confirm_timed_out = False

        while result is None:

            if (
                stop_event is not None
                and stop_event.is_set()
            ):
                log_message(
                    "⛔ Đã dừng.",
                    log_callback
                )
                break

            try:
                screen = capture_fco(hwnd)

                confidence, x, y = find_template(
                    screen,
                    confirm_bulk
                )

                if confidence >= threshold:
                    result = {
                        "confidence": confidence,
                        "x": x,
                        "y": y
                    }
                    break

            except Exception as exc:
                log_message(
                    f"⚠️ Lỗi kiểm tra xác nhận mua: {exc}",
                    log_callback
                )

            if time.time() - confirm_wait_started >= CONFIRM_WAIT_TIMEOUT:
                log_message(
                    "⚠️ Không tìm thấy 'Xác nhận' sau "
                    f"{CONFIRM_WAIT_TIMEOUT} giây; sẽ bấm lại "
                    "'Mua hàng loạt'.",
                    log_callback
                )
                confirm_timed_out = True
                break

            random_sleep(0.045, 0.065)

        if result is None:
            if confirm_timed_out:
                continue
            break


        log_message(
            f"✅ Tìm thấy xác nhận "
            f"(confidence={result['confidence']:.4f})",
            log_callback
        )


        click_client(
            hwnd,
            result["x"],
            result["y"],
            method="send"
        )


        interruptible_sleep(
            0.15
        )


        # ====================================================
        # ====================================================
        # ====================================================
        # STEP 4
        # CHỜ KẾT QUẢ
        # ====================================================

        log_message(
            "⏳ Đang chờ kết quả mua...",
            log_callback
        )

        # Chỉ dùng hình ảnh để xác định trạng thái.
        # Nếu game không chuyển trạng thái, quay lại bước "Mua hàng loạt".
        result = None
        last_debug = time.time()
        success_hits = 0
        result_wait_started = time.time()

        while result is None:

            if (
                stop_event is not None
                and stop_event.is_set()
            ):
                log_message(
                    "⛔ Đã dừng.",
                    log_callback
                )
                break

            try:
                screen = capture_fco(hwnd)

                failure_conf, failure_x, failure_y = find_template(
                    screen,
                    failure
                )

                receive_conf, receive_x, receive_y = find_template(
                    screen,
                    receive_now
                )

                # ------------------------------------------------
                # FAILURE
                # ------------------------------------------------

                if failure_conf >= threshold:
                    log_message(
                        f"✅ Phát hiện Mua thất bại "
                        f"(confidence={failure_conf:.4f})",
                        log_callback
                    )

                    result = (
                        "failure",
                        failure_conf,
                        failure_x,
                        failure_y
                    )
                    break

                # ------------------------------------------------
                # SUCCESS
                # ------------------------------------------------
                # Không hạ ngưỡng cho "Nhận ngay": popup thất bại có
                # thể tạo confidence giả với mẫu này.
                if receive_conf >= threshold:
                    success_hits += 1
                else:
                    success_hits = 0

                if success_hits >= 2:
                    log_message(
                        f"✅ Phát hiện Mua thành công "
                        f"(confidence={receive_conf:.4f}, "
                        f"threshold={threshold:.2f}, ổn định=2)",
                        log_callback
                    )

                    result = (
                        "success",
                        receive_conf,
                        receive_x,
                        receive_y
                    )
                    break

                # ------------------------------------------------
                # DEBUG ĐỊNH KỲ
                # ------------------------------------------------
                # Không spam log, nhưng giúp biết bot đang nhìn thấy gì.
                if time.time() - last_debug >= 2.0:

                    log_message(
                        f"🔎 Đang chờ kết quả | "
                        f"Failure={failure_conf:.4f} | "
                        f"Receive={receive_conf:.4f}",
                        log_callback
                    )

                    last_debug = time.time()

                if time.time() - result_wait_started >= RESULT_WAIT_TIMEOUT:
                    log_message(
                        "⚠️ Không thấy popup kết quả sau "
                        f"{RESULT_WAIT_TIMEOUT} giây; quay lại bấm "
                        "'Mua hàng loạt'.",
                        log_callback
                    )
                    break

            except Exception as exc:

                log_message(
                    f"⚠️ Lỗi kiểm tra kết quả mua: {exc}",
                    log_callback
                )

                random_sleep(
                    0.10,
                    0.15
                )
                continue

            random_sleep(
                0.04,
                0.07
            )

        if result is None:
            if (
                stop_event is not None
                and stop_event.is_set()
            ):
                break

            log_message(
                "🔄 Đang tìm lại 'Mua hàng loạt'...",
                log_callback
            )
            continue

        result_type = result[0]

        confidence = result[1]

        x = result[2]

        y = result[3]


        # ====================================================
        # FAILURE
        # ====================================================

        if result_type == "failure":

            log_message(
                "❌ MUA THẤT BẠI",
                log_callback
            )

            log_message(
                f"Confidence: {confidence:.4f}",
                log_callback
            )


            # ------------------------------------------------
            # CONFIRM FAILURE
            # ------------------------------------------------

            log_message(
                "[3] Đang tìm "
                "'Xác nhận' sau thất bại...",
                log_callback
            )


            confirmed = click_failure_confirm(
                hwnd,
                confirm_bulk,
                confirm_bulk_bold,
                threshold,
                stop_event=stop_event,
                log_callback=log_callback
            )

            if not confirmed:
                log_message(
                    "❌ Không thể xác nhận popup thất bại.",
                    log_callback
                )
                break


            log_message(
                "↩️ Mua thất bại → tiếp tục.",
                log_callback
            )


            interruptible_sleep(
                0.15
            )

            continue


        # ====================================================
        # SUCCESS
        # ====================================================

        if result_type == "success":

            log_message(
                "🎉 MUA THÀNH CÔNG",
                log_callback
            )

            log_message(
                f"Confidence: {confidence:.4f}",
                log_callback
            )


            # ------------------------------------------------
            # STEP 4
            # NHẬN NGAY
            # ------------------------------------------------

            log_message(
                "[3] Đang chờ 'Nhận ngay'...",
                log_callback
            )


            result = wait_for_template(
                hwnd,
                receive_now,
                timeout=5,
                threshold=threshold,
                stop_event=stop_event
            )


            if result is None:

                log_message(
                    "❌ Không tìm thấy "
                    "'Nhận ngay'.",
                    log_callback
                )

                break


            log_message(
                f"✅ Tìm thấy 'Nhận ngay' "
                f"(confidence={result['confidence']:.4f})",
                log_callback
            )


            interruptible_sleep(
                0.4
            )


            # ------------------------------------------------
            # CLICK NHẬN NGAY
            # ------------------------------------------------

            log_message(
                "🖱️ Click 'Nhận ngay'...",
                log_callback
            )


            receive_clicked = click_until_disappear(
                hwnd,
                receive_now,
                result["x"],
                result["y"],
                method="send",
                threshold=threshold,
                max_attempts=2,
                wait_after_click=0.2,
                stop_event=stop_event,
                log_callback=log_callback
            )


            if not receive_clicked:

                log_message(
                    "❌ Không click được "
                    "'Nhận ngay'.",
                    log_callback
                )

                break


            # ------------------------------------------------
            # STEP 5
            # ĐỌC SỐ CẦU THỦ + XÁC NHẬN CUỐI
            # ------------------------------------------------

            log_message(
                "[4] Đang chờ popup xác nhận cuối...",
                log_callback
            )

            # Sau khi click "Nhận ngay", popup xác nhận cuối
            # có thể xuất hiện chậm hơn template vài nhịp.
            # Vì câu "tổng cộng X cầu thủ" cũng xuất hiện trên
            # chính popup này, ưu tiên đọc OCR trong lúc chờ.
            final_confirm_result = None
            round_count = 0

            # Chờ bằng trạng thái hình ảnh.
            # Không dùng timeout để quyết định kết thúc.
            while final_confirm_result is None:

                if (
                    stop_event is not None
                    and stop_event.is_set()
                ):
                    break

                if not win32gui.IsWindow(hwnd):
                    break

                try:
                    screen = capture_fco(hwnd)

                    confirm_conf, confirm_x, confirm_y = find_template(
                        screen,
                        confirm_received
                    )

                    if confirm_conf >= threshold:
                        final_confirm_result = {
                            "confidence": confirm_conf,
                            "x": confirm_x,
                            "y": confirm_y
                        }
                        break

                    # Nếu game đã quay thẳng về màn mua thì
                    # popup cuối không còn cần xử lý.
                    buy_conf, _, _, _ = find_buy_bulk_template(
                        screen,
                        buy_bulk_templates
                    )

                    if buy_conf >= threshold:
                        log_message(
                            "✅ Đã quay lại màn mua → "
                            "tiếp tục round.",
                            log_callback
                        )
                        final_confirm_result = "back_to_buy"
                        break

                except Exception as exc:
                    log_message(
                        f"⚠️ Capture xác nhận cuối lỗi: {exc}",
                        log_callback
                    )

                random_sleep(0.045, 0.065)

            if final_confirm_result == "back_to_buy":
                continue

            if final_confirm_result is None:
                break

            log_message(
                f"✅ Tìm thấy 'Xác nhận' cuối "
                f"(confidence={final_confirm_result['confidence']:.4f})",
                log_callback
            )

            log_message(
                "🔎 Đang đọc số cầu thủ bằng OCR...",
                log_callback
            )

            # Đọc số cầu thủ đúng 1 lần khi popup xác nhận cuối
            # đang hiện rõ. Nếu OCR không đọc được thì +0.
            round_count = read_purchase_count(
                hwnd,
                log_callback=log_callback,
                retries=3
            )

            log_message(
                f"📦 Round này mua được: {round_count} cầu thủ",
                log_callback
            )

            # ------------------------------------------------
            # CLICK XÁC NHẬN CUỐI
            # ------------------------------------------------

            log_message(
                "🖱️ Click 'Xác nhận' cuối...",
                log_callback
            )


            final_confirmed = click_until_disappear(
                hwnd,
                confirm_received,
                final_confirm_result["x"],
                final_confirm_result["y"],
                method="post",
                threshold=threshold,
                max_attempts=2,
                wait_after_click=0.25,
                stop_event=stop_event,
                log_callback=log_callback
            )


            if not final_confirmed:

                log_message(
                    "❌ Không đóng được "
                    "'Xác nhận' cuối.",
                    log_callback
                )

                break


            # =================================================
            # COUNT ACTUAL PLAYERS
            # =================================================

            purchased_players += round_count

            log_message(
                f"👤 Tổng đã mua: "
                f"{purchased_players}/{target_players}",
                log_callback
            )


            if player_count_callback is not None:

                player_count_callback(
                    purchased_players,
                    target_players
                )


            # =================================================
            # TARGET REACHED
            # =================================================

            if purchased_players >= target_players:

                log_message(
                    "",
                    log_callback
                )

                log_message(
                    "🎯 ĐÃ ĐỦ SỐ LƯỢNG CẦU THỦ!",
                    log_callback
                )

                log_message(
                    f"🎉 Tổng cộng: "
                    f"{purchased_players}/{target_players}",
                    log_callback
                )

                play_notification(log_callback)

                break


            log_message(
                "✅ Đã xác nhận nhận cầu thủ.",
                log_callback
            )


            # ------------------------------------------------
            # STEP 6
            # QUAY LẠI MÀN MUA
            # ------------------------------------------------

            log_message(
                "[5] Đang chờ quay lại "
                "màn hình mua...",
                log_callback
            )


            result = wait_for_template(
                hwnd,
                buy_bulk,
                timeout=5,
                threshold=threshold,
                stop_event=stop_event,
                template_variants=buy_bulk_templates
            )


            if result is None:

                log_message(
                    "⚠️ Chưa thấy lại 'Mua hàng loạt' → "
                    "tiếp tục chờ...",
                    log_callback
                )

                random_sleep(0.45, 0.55)
                continue


            log_message(
                "✅ Đã quay lại màn hình mua.",
                log_callback
            )


            # Delay giữa các lần mua
            interruptible_sleep(
                0.15
            )


    # ========================================================
    # FINISHED
    # ========================================================

    if purchased_players >= target_players:

        log_message(
            "",
            log_callback
        )

        log_message(
            "🎯 AUTO BUY DỪNG - ĐÃ ĐỦ MỤC TIÊU",
            log_callback
        )

        log_message(
            f"👤 Tổng số cầu thủ: "
            f"{purchased_players}/{target_players}",
            log_callback
        )


    elif (
        stop_event is not None
        and stop_event.is_set()
    ):

        log_message(
            "",
            log_callback
        )

        log_message(
            "⛔ AUTO BUY ĐÃ DỪNG.",
            log_callback
        )

        log_message(
            f"👤 Đã mua: "
            f"{purchased_players}/{target_players}",
            log_callback
        )


    else:

        log_message(
            "",
            log_callback
        )

        log_message(
            "⚠️ AUTO BUY ĐÃ DỪNG.",
            log_callback
        )

        log_message(
            f"👤 Đã mua: "
            f"{purchased_players}/{target_players}",
            log_callback
        )