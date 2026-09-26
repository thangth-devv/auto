import re
import time
import os
from collections import Counter
from datetime import datetime, timedelta

import cv2
import numpy as np
import pytesseract
import win32con
from PIL import Image

from auto_buy import (
    capture_fco,
    click_client,
    double_click_input,
    interruptible_sleep,
    key_press,
    play_notification,
    random_sleep,
    type_text,
)


REFERENCE_WIDTH = 1280
REFERENCE_HEIGHT = 752

# Coordinates are based on the 1280x752 full-window layout in the supplied images.
PLAYER_ROW_X = 430
PLAYER_ROW_FIRST_Y = 230
PLAYER_ROW_STEP_Y = 32
BUY_PLAYER_X = 875
BUY_PLAYER_Y = 672
# The maximum price is shown in the "Giá mua cao nhất" table after BUY.
MAX_PRICE_ROIS = (
    (950, 312, 1070, 347),
    (935, 305, 1090, 350),
)
# Click the maximum-price row shown below the minimum-price row.
# The max-price label/value row is above the editable price field.
MAX_PRICE_X = 1000
MAX_PRICE_Y = 332
ORDER_BUY_X = 826
ORDER_BUY_Y = 604
ORDER_CANCEL_X = 968
ORDER_CANCEL_Y = 604
# Quantity field in the player purchase dialog shown at 1280x752.
QUANTITY_X = 995
QUANTITY_Y = 460
PLAYER_SELECTION_WAIT = 0.25
POPUP_CLOSE_BEFORE_SELECTION_TIMEOUT = 1.0
PRICE_DIALOG_TIMEOUT = 0.8
BUY_POPUP_REGION = (180, 90, 1100, 660)
MAX_PRICE_INCREASE_RATIO = 1.30
GIF_CAPTURE_INTERVAL = 0.20
POST_POPUP_CAPTURE_DURATION = 2.0
POPUP_CLOSE_TIMEOUT = 10.0
POPUP_CLOSE_STABLE_READS = 2
GIF_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "purchase_gifs",
)
OCR_DEBUG_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "ocr_debug",
)


def _format_price(price):
    if price is None:
        return "N/A"
    if price >= 1_000_000_000:
        return f"{price / 1_000_000_000:.1f}".rstrip("0").rstrip(".") + "B"
    if price >= 1_000_000:
        return f"{price / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    return f"{price:,}"


def _add_gif_frame(frames, hwnd):
    frame = capture_fco(hwnd)
    frames.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))


def _save_purchase_gif(frames, position, reset_code):
    if not frames:
        return None

    os.makedirs(GIF_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(
        GIF_DIR,
        f"position_{position}_{reset_code}_{timestamp}.gif",
    )
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=int(GIF_CAPTURE_INTERVAL * 1000),
        loop=0,
        optimize=False,
    )
    return path


def _save_price_debug(screen, position, reset_code, stage):
    os.makedirs(OCR_DEBUG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    prefix = os.path.join(
        OCR_DEBUG_DIR,
        f"{stage}_position_{position}_{reset_code}_{timestamp}",
    )
    screen_path = f"{prefix}_screen.png"
    cv2.imwrite(screen_path, screen)
    marked = screen.copy()

    height, width = screen.shape[:2]
    scale_x = width / REFERENCE_WIDTH
    scale_y = height / REFERENCE_HEIGHT
    for index, (left, top, right, bottom) in enumerate(MAX_PRICE_ROIS, 1):
        scaled_left = int(left * scale_x)
        scaled_top = int(top * scale_y)
        scaled_right = int(right * scale_x)
        scaled_bottom = int(bottom * scale_y)
        cv2.rectangle(
            marked,
            (scaled_left, scaled_top),
            (scaled_right, scaled_bottom),
            (0, 0, 255),
            2,
        )
        roi = screen[
            scaled_top:scaled_bottom,
            scaled_left:scaled_right,
        ]
        if roi.size:
            cv2.imwrite(f"{prefix}_roi_{index}.png", roi)
    marked_path = f"{prefix}_marked.png"
    cv2.imwrite(marked_path, marked)
    return marked_path


def _save_max_price_click_debug(
    screen,
    position,
    reset_code,
    attempt,
    click_x,
    click_y,
    expected_price,
    selected_price,
):
    """Save the area around a max-price click for post-run verification."""
    os.makedirs(OCR_DEBUG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    price_label = (
        "none"
        if selected_price is None
        else str(selected_price)
    )
    prefix = os.path.join(
        OCR_DEBUG_DIR,
        (
            f"max_price_click_position_{position}_{reset_code}_"
            f"attempt_{attempt}_expected_{expected_price}_"
            f"observed_{price_label}_{timestamp}"
        ),
    )

    height, width = screen.shape[:2]
    scale_x = width / REFERENCE_WIDTH
    scale_y = height / REFERENCE_HEIGHT
    scaled_x = int(click_x * scale_x)
    scaled_y = int(click_y * scale_y)
    crop_half_width = int(150 * scale_x)
    crop_half_height = int(90 * scale_y)
    left = max(0, scaled_x - crop_half_width)
    top = max(0, scaled_y - crop_half_height)
    right = min(width, scaled_x + crop_half_width)
    bottom = min(height, scaled_y + crop_half_height)
    crop = screen[top:bottom, left:right]
    if crop.size == 0:
        return None

    marked = crop.copy()
    relative_x = scaled_x - left
    relative_y = scaled_y - top
    cv2.drawMarker(
        marked,
        (relative_x, relative_y),
        (0, 0, 255),
        cv2.MARKER_CROSS,
        24,
        2,
    )
    cv2.rectangle(
        marked,
        (0, 0),
        (marked.shape[1] - 1, marked.shape[0] - 1),
        (0, 255, 255),
        2,
    )
    path = f"{prefix}_click_area.png"
    if not cv2.imwrite(path, marked):
        raise OSError(f"Không thể lưu ảnh debug click giá max: {path}")
    return path


def normalize_reset_code(reset_code):
    code = reset_code.strip().lower()
    if re.fullmatch(r"[cl]\d", code):
        code = f"{code[0]}0{code[1:]}"
    if not re.fullmatch(r"[cl]\d{2}", code):
        raise ValueError(
            "Mã reset phải có dạng c00 hoặc l00 (ví dụ c50 = giờ chẵn phút 50)."
        )

    minute = int(code[1:])
    if minute > 59:
        raise ValueError("Phút reset phải từ 00 đến 59.")
    return code


def _reset_matches_hour(reset_code, hour):
    code = normalize_reset_code(reset_code)
    minute = int(code[1:])
    parity = 0 if code[0] == "c" else 1
    return hour % 2 == parity, minute


def _next_reset_datetime(reset_code, now):
    code = normalize_reset_code(reset_code)
    minute = int(code[1:])
    parity = 0 if code[0] == "c" else 1
    candidate = now.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )

    for _ in range(48):
        if candidate > now and candidate.hour % 2 == parity:
            return candidate
        candidate += timedelta(hours=1)

    raise RuntimeError(f"Không xác định được lần reset tiếp theo: {reset_code}")


def _nearest_reset_datetime(reset_code, now):
    code = normalize_reset_code(reset_code)
    minute = int(code[1:])
    parity = 0 if code[0] == "c" else 1
    candidate = now.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )

    if candidate > now:
        candidate -= timedelta(hours=1)

    for _ in range(48):
        if (
            candidate.hour % 2 == parity
            and candidate <= now
            and now < candidate + timedelta(minutes=20)
        ):
            return candidate
        if candidate > now and candidate.hour % 2 == parity:
            return candidate
        candidate += timedelta(hours=1)

    raise RuntimeError(f"Không xác định được reset gần nhất: {reset_code}")


def sort_entries_by_reset(entries, now=None):
    if now is None:
        now = datetime.now()
    return sorted(
        entries,
        key=lambda item: _nearest_reset_datetime(item[1]["reset"], now),
    )


def _read_price(screen):
    height, width = screen.shape[:2]
    scale_x = width / REFERENCE_WIDTH
    scale_y = height / REFERENCE_HEIGHT
    unit_values = []
    for roi_bounds in MAX_PRICE_ROIS:
        left, top, right, bottom = roi_bounds
        left = int(left * scale_x)
        right = int(right * scale_x)
        top = int(top * scale_y)
        bottom = int(bottom * scale_y)
        roi = screen[top:bottom, left:right]
        if roi.size == 0:
            continue

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.copyMakeBorder(
            gray,
            8,
            8,
            8,
            8,
            cv2.BORDER_CONSTANT,
            value=255,
        )
        resized = cv2.resize(
            gray,
            None,
            fx=5,
            fy=5,
            interpolation=cv2.INTER_CUBIC,
        )
        images = (resized, cv2.threshold(
            resized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )[1])
        for image in images:
            text = pytesseract.image_to_string(
                image,
                config=(
                    "--psm 7 "
                    "-c tessedit_char_whitelist=0123456789.,MB"
                ),
                lang="eng",
            )
            normalized = text.upper().replace(" ", "")
            unit_match = re.search(r"(\d+(?:[.,]\d+)?)([MB])", normalized)
            if unit_match:
                number_text = unit_match.group(1).replace(",", ".")
                unit = unit_match.group(2)
                if "." in number_text:
                    integer_part, fraction_part = number_text.split(".", 1)
                    max_fraction_digits = 2 if unit == "B" else 1
                    if (
                        len(integer_part) > 2
                        or len(fraction_part) > max_fraction_digits
                    ):
                        continue
                value = float(number_text)
                multiplier = 1_000_000 if unit == "M" else 1_000_000_000
                parsed_value = int(value * multiplier)
                if parsed_value >= 1_000_000:
                    unit_values.append(parsed_value)

    if unit_values:
        # Different OCR passes can disagree on a small decimal separator.
        # Use the value with the strongest OCR consensus, not the largest
        # numeric result (which would turn 24.2M into 242M).
        return Counter(unit_values).most_common(1)[0][0]
    return None


def _click_purchase_button(hwnd, stop_event, log_callback):
    if stop_event.is_set():
        return False

    click_client(
        hwnd,
        BUY_PLAYER_X,
        BUY_PLAYER_Y,
        method="message",
        fast=True,
    )
    return True


def _click_max_price_row(
    hwnd,
    queue_row,
    position,
    reset_code,
    expected_price,
    stop_event,
    log_callback,
):
    """Click the fixed center of the max-price value after a reset."""
    if stop_event.is_set():
        return False

    click_client(
        hwnd,
        MAX_PRICE_X,
        MAX_PRICE_Y,
        method="message",
        fast=True,
    )
    random_sleep(0.05, 0.08)
    clicked_screen = capture_fco(hwnd)
    debug_path = _save_max_price_click_debug(
        clicked_screen,
        position,
        reset_code,
        1,
        MAX_PRICE_X,
        MAX_PRICE_Y,
        expected_price,
        expected_price,
    )
    if debug_path:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            f"📸 Đã lưu vùng click giá max: {debug_path}"
        )
    return True


def _buy_popup_visible(screen):
    height, width = screen.shape[:2]
    scale_x = width / REFERENCE_WIDTH
    scale_y = height / REFERENCE_HEIGHT
    left, top, right, bottom = BUY_POPUP_REGION
    roi = screen[
        int(top * scale_y):int(bottom * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return False
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bright_ratio = float(np.mean(gray > 220))
    return bright_ratio >= 0.35


def _capture_buy_popup(hwnd, stop_event):
    deadline = time.time() + PRICE_DIALOG_TIMEOUT
    last_screen = None
    while time.time() < deadline:
        if stop_event.is_set():
            return last_screen
        last_screen = capture_fco(hwnd)
        if _buy_popup_visible(last_screen):
            return last_screen
        random_sleep(0.005, 0.01)
    return None


def _capture_buy_popup_price(hwnd, stop_event):
    screen = _capture_buy_popup(hwnd, stop_event)
    if screen is None:
        return None, None
    price = _read_price(screen)
    if price is not None:
        return price, screen
    return None, screen


def _wait_for_buy_popup_close(hwnd, stop_event, gif_frames):
    """Wait until the purchase popup has closed after submitting the order."""
    deadline = time.time() + POPUP_CLOSE_TIMEOUT
    closed_reads = 0

    while time.time() < deadline:
        if stop_event.is_set():
            return False

        screen = capture_fco(hwnd)
        gif_frames.append(
            Image.fromarray(cv2.cvtColor(screen, cv2.COLOR_BGR2RGB))
        )
        if _buy_popup_visible(screen):
            closed_reads = 0
        else:
            closed_reads += 1
            if closed_reads >= POPUP_CLOSE_STABLE_READS:
                return True
        random_sleep(GIF_CAPTURE_INTERVAL, GIF_CAPTURE_INTERVAL)

    return False


def _capture_after_popup_close(hwnd, stop_event, gif_frames):
    """Keep recording the result after the purchase popup has disappeared."""
    deadline = time.time() + POST_POPUP_CAPTURE_DURATION
    while time.time() < deadline:
        if stop_event.is_set():
            return False
        screen = capture_fco(hwnd)
        gif_frames.append(
            Image.fromarray(cv2.cvtColor(screen, cv2.COLOR_BGR2RGB))
        )
        remaining = deadline - time.time()
        if remaining > 0:
            random_sleep(
                min(GIF_CAPTURE_INTERVAL, remaining),
                min(GIF_CAPTURE_INTERVAL, remaining),
            )
    return True


def _close_popup_before_selection(hwnd, stop_event):
    """Close a stale purchase popup before clicking a different player row."""
    key_press(hwnd, win32con.VK_ESCAPE)
    deadline = time.time() + POPUP_CLOSE_BEFORE_SELECTION_TIMEOUT
    closed_reads = 0
    while time.time() < deadline:
        if stop_event.is_set():
            return False
        screen = capture_fco(hwnd)
        if _buy_popup_visible(screen):
            closed_reads = 0
        else:
            closed_reads += 1
            if closed_reads >= POPUP_CLOSE_STABLE_READS:
                return True
        random_sleep(0.05, 0.08)
    return False


def _select_player_row(hwnd, queue_row, position, stop_event, log_callback):
    if position < 1:
        raise ValueError("Vị trí cầu thủ phải bắt đầu từ hàng 1.")

    row_y = PLAYER_ROW_FIRST_Y + (position - 1) * PLAYER_ROW_STEP_Y
    log_callback(
        f"[Hàng {queue_row}] Đang chọn cầu thủ ở vị trí {position}."
    )
    click_client(
        hwnd,
        PLAYER_ROW_X,
        row_y,
        method="message",
        fast=True,
    )

    # Chờ danh sách cập nhật trạng thái chọn trước khi nút mua được xử lý.
    deadline = time.time() + PLAYER_SELECTION_WAIT
    while time.time() < deadline:
        if stop_event.is_set():
            return False
        random_sleep(0.005, 0.01)

    log_callback(
        f"[Hàng {queue_row}] ✅ Đã chọn cầu thủ ở vị trí {position}."
    )
    return True


def _insert_one(
    hwnd,
    queue_row,
    position,
    reset_code,
    quantity,
    previous_price,
    stop_event,
    log_callback,
):
    if previous_price is None:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            "⚠️ Chưa có giá max mốc ban đầu, không mua."
        )
        return previous_price, False, False

    if not _click_purchase_button(hwnd, stop_event, log_callback):
        return previous_price, False, False

    current_price, screen = _capture_buy_popup_price(hwnd, stop_event)
    if current_price is None or screen is None:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            "⚠️ Không đọc được giá trong popup mua."
        )
        key_press(hwnd, win32con.VK_ESCAPE)
        return previous_price, False, False

    log_callback(
        f"[Hàng {queue_row}][Vị trí {position}] "
        f"Giá ghi nhớ: {_format_price(previous_price)}; "
        f"giá trên game: {_format_price(current_price)}."
    )
    if current_price == previous_price:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            "Giá không đổi, đã hủy; chuyển sang lần kiểm tra tiếp theo."
        )
        key_press(hwnd, win32con.VK_ESCAPE)
        return previous_price, False, False

    gif_frames = [
        Image.fromarray(cv2.cvtColor(screen, cv2.COLOR_BGR2RGB))
    ]
    _add_gif_frame(gif_frames, hwnd)

    if not _click_max_price_row(
        hwnd,
        queue_row,
        position,
        reset_code,
        previous_price,
        stop_event,
        log_callback,
    ):
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            "❌ Không xác thực được hàng giá max hợp lệ, hủy lệnh mua."
        )
        key_press(hwnd, win32con.VK_ESCAPE)
        return previous_price, False, False

    _add_gif_frame(gif_frames, hwnd)

    if quantity > 1:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            f"Nhập số lượng theo tool: {quantity}."
        )
        double_click_input(
            hwnd,
            QUANTITY_X,
            QUANTITY_Y,
            method="message",
        )
        random_sleep(0.03, 0.05)
        type_text(hwnd, str(quantity))
        _add_gif_frame(gif_frames, hwnd)
    random_sleep(0.02, 0.04)
    click_client(
        hwnd,
        ORDER_BUY_X,
        ORDER_BUY_Y,
        method="message",
        fast=True,
    )
    _add_gif_frame(gif_frames, hwnd)

    popup_closed = _wait_for_buy_popup_close(hwnd, stop_event, gif_frames)
    if not popup_closed:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            "⚠️ Chưa xác nhận popup mua đã đóng; chưa phát thông báo."
        )
        return previous_price, False, False

    if not _capture_after_popup_close(hwnd, stop_event, gif_frames):
        return previous_price, False, False

    log_callback(
        f"[Hàng {queue_row}][Vị trí {position}] ✅ Đã gửi lệnh mua "
        f"{quantity} cầu thủ; "
        f"mua được với giá {_format_price(current_price)}/cầu thủ "
        f"(giá ghi nhớ: {_format_price(previous_price)})."
    )

    gif_path = _save_purchase_gif(gif_frames, position, reset_code)
    if gif_path:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] 🎞️ Đã lưu GIF: {gif_path}"
        )

    return previous_price, True, False


def _check_initial_price(
    hwnd,
    queue_row,
    position,
    reset_code,
    stop_event,
    log_callback,
    already_selected=False,
):
    """Read the current max price after opening the buy dialog."""
    if not already_selected and not _select_player_row(
        hwnd, queue_row, position, stop_event, log_callback
    ):
        return None

    if not _click_purchase_button(hwnd, stop_event, log_callback):
        return None

    price, screen = _capture_buy_popup_price(hwnd, stop_event)
    key_press(hwnd, win32con.VK_ESCAPE)

    if price is None:
        debug_path = None
        if screen is not None:
            debug_path = _save_price_debug(
                screen,
                position,
                reset_code,
                "initial_price_unreadable",
            )
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            "⚠️ Không đọc được giá max ban đầu."
            + (
                f" Ảnh debug: {debug_path}"
                if debug_path
                else " Không có ảnh màn hình cuối để debug."
            )
        )
        return None

    log_callback(
        f"[Hàng {queue_row}][Vị trí {position}] "
        f"Giá max mốc khi đến lượt chèn: {_format_price(price)}."
    )
    return price


def run_player_insert(hwnd, entries, stop_event, log_callback):
    """Process the sorted queue strictly from first item to last item."""
    if not entries:
        raise ValueError("Chưa cấu hình cầu thủ nào.")

    log_callback("🟢 Player Insert đang chạy.")
    queue = [
        (queue_row, entry)
        for queue_row, (_source_row, entry)
        in enumerate(entries, 1)
    ]
    log_callback(
        "📋 Thứ tự hàng đợi: "
        + ", ".join(
            f"thứ tự {row} ({entry['reset']})"
            for row, entry in queue
        )
    )

    completed_resets = {}

    while not stop_event.is_set():
        for queue_row, entry in queue:
            if stop_event.is_set():
                break

            position = entry["position"]
            reset_code = entry["reset"]
            quantity = entry["quantity"]
            # A popup left open from the previous game selection would intercept
            # the row click and make the next price read use the wrong player.
            if not _close_popup_before_selection(hwnd, stop_event):
                log_callback(
                    f"[Hàng {queue_row}][Vị trí {position}] "
                    "⚠️ Không đóng được popup cũ trước khi chọn cầu thủ."
                )
                break
            if not _select_player_row(
                hwnd,
                queue_row,
                position,
                stop_event,
                log_callback,
            ):
                break

            selected_price = None
            prepared_reset = None
            reset_started = None
            waiting_reset_logged = None

            while not stop_event.is_set():
                now = datetime.now()
                reset_at = _nearest_reset_datetime(reset_code, now)

                if completed_resets.get(queue_row) == reset_at:
                    next_reset = _next_reset_datetime(
                        reset_code,
                        reset_at + timedelta(minutes=20),
                    )
                    wait_seconds = min(
                        max((next_reset - now).total_seconds(), 0.05),
                        1.0,
                    )
                    interruptible_sleep(wait_seconds)
                    continue

                seconds_to_reset = (reset_at - now).total_seconds()
                in_window = reset_at <= now < reset_at + timedelta(minutes=20)
                if in_window:
                    if prepared_reset != reset_at:
                        log_callback(
                            f"[Thứ tự {queue_row}][Vị trí {position}] "
                            f"Reset {reset_code}: cập nhật giá ghi nhớ."
                        )
                        if not _select_player_row(
                            hwnd, queue_row, position, stop_event, log_callback
                        ):
                            break
                        selected_price = _check_initial_price(
                            hwnd,
                            queue_row,
                            position,
                            reset_code,
                            stop_event,
                            log_callback,
                            already_selected=True,
                        )
                        prepared_reset = reset_at
                        if selected_price is None:
                            log_callback(
                                f"[Thứ tự {queue_row}][Vị trí {position}] "
                                "Không cập nhật được giá reset; bỏ qua reset này."
                            )
                            break

                    if reset_started != reset_at:
                        reset_started = reset_at
                        log_callback(
                            f"[Thứ tự {queue_row}][Vị trí {position}] "
                            f"Bắt đầu kiểm tra reset {reset_code}."
                        )
                    if now >= reset_started + timedelta(minutes=20):
                        log_callback(
                            f"[Thứ tự {queue_row}][Vị trí {position}] "
                            "Hết 20 phút không đổi; chuyển sang thứ tự kế tiếp."
                        )
                        break
                    if now.second >= 15:
                        interruptible_sleep(0.2)
                        continue
                    (
                        selected_price,
                        purchased,
                        exhausted,
                    ) = _insert_one(
                        hwnd,
                        queue_row,
                        position,
                        reset_code,
                        quantity,
                        selected_price,
                        stop_event,
                        log_callback,
                    )
                    if purchased:
                        completed_resets[queue_row] = reset_at
                        log_callback(
                            f"[Thứ tự {queue_row}][Vị trí {position}] "
                            "✅ Chèn cầu thủ thành công; tiếp tục chạy đến reset kế tiếp."
                        )
                        play_notification(log_callback)
                        break
                    if exhausted:
                        break
                else:
                    next_reset = _next_reset_datetime(reset_code, now)
                    if waiting_reset_logged != next_reset:
                        log_callback(
                            f"[Thứ tự {queue_row}][Vị trí {position}] "
                            f"Đang chờ reset lúc {next_reset:%d/%m %H:%M}."
                        )
                        waiting_reset_logged = next_reset
                    wait_seconds = min(max(seconds_to_reset, 0.05), 1.0)
                    interruptible_sleep(wait_seconds)
                    continue

                interruptible_sleep(0.01)

    log_callback("⛔ Player Insert đã dừng.")
