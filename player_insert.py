import re
import time
import os
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
# The maximum price is shown as a compact M/B value next to "Giá tối đa".
MAX_PRICE_ROIS = (
    (900, 295, 1080, 345),
    (880, 285, 1100, 355),
)
# Click the maximum-price row shown below the minimum-price row.
MAX_PRICE_X = 1000
MAX_PRICE_Y = 370
ORDER_BUY_X = 826
ORDER_BUY_Y = 604
ORDER_CANCEL_X = 968
ORDER_CANCEL_Y = 604
# Quantity field in the player purchase dialog shown at 1280x752.
QUANTITY_X = 995
QUANTITY_Y = 460
PLAYER_SELECTION_WAIT = 0.25
PRICE_DIALOG_WAIT = (0.22, 0.32)
MAX_PRICE_INCREASE_RATIO = 1.30
GIF_CAPTURE_INTERVAL = 0.12
GIF_RETURN_WAIT = 3.0
GIF_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "purchase_gifs",
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

    for _ in range(48):
        if (
            candidate.hour % 2 == parity
            and candidate <= now
            and now < candidate + timedelta(minutes=21)
        ):
            return candidate
        if candidate >= now and candidate.hour % 2 == parity:
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
    fallback_values = []
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
        gray = cv2.resize(
            gray,
            None,
            fx=5,
            fy=5,
            interpolation=cv2.INTER_CUBIC,
        )
        images = [
            ("gray", gray),
            (
                "otsu",
                cv2.threshold(
                    gray,
                    0,
                    255,
                    cv2.THRESH_BINARY + cv2.THRESH_OTSU,
                )[1],
            ),
            (
                "dark",
                cv2.threshold(gray, 190, 255, cv2.THRESH_BINARY)[1],
            ),
        ]

        for image_name, image in images:
            for psm in (6, 7, 11):
                text = pytesseract.image_to_string(
                    image,
                    config=(
                        f"--psm {psm} "
                        "-c tessedit_char_whitelist=0123456789.MB"
                    ),
                    lang="eng",
                )
                normalized = (
                    text.upper()
                    .replace(",", ".")
                    .replace(" ", "")
                )
                unit_match = re.search(
                    r"(\d+(?:\.\d+)?)([MB])",
                    normalized,
                )
                if unit_match:
                    number_text = unit_match.group(1)
                    value = float(number_text)
                    if (
                        unit_match.group(2) == "M"
                        and "." not in number_text
                        and len(number_text) == 3
                    ):
                        # OCR can drop the decimal point in values such as 18.3M.
                        value /= 10
                    multiplier = (
                        1_000_000
                        if unit_match.group(2) == "M"
                        else 1_000_000_000
                    )
                    unit_values.append(int(value * multiplier))
                    continue

                digits = re.sub(r"[^\d]", "", normalized)
                if 2 <= len(digits) <= 4:
                    # OCR may drop the trailing M from values such as 18.3M.
                    fallback_values.append(int(digits) * 100_000)

    if unit_values:
        return max(unit_values)
    if fallback_values:
        return max(fallback_values)
    return None


def _click_purchase_button(hwnd, stop_event, log_callback):
    if stop_event.is_set():
        return False

    click_client(hwnd, BUY_PLAYER_X, BUY_PLAYER_Y)
    return True


def _select_player_row(hwnd, queue_row, position, stop_event, log_callback):
    if position < 1:
        raise ValueError("Vị trí cầu thủ phải bắt đầu từ hàng 1.")

    row_y = PLAYER_ROW_FIRST_Y + (position - 1) * PLAYER_ROW_STEP_Y
    log_callback(
        f"[Hàng {queue_row}] Đang chọn cầu thủ ở vị trí {position}."
    )
    click_client(hwnd, PLAYER_ROW_X, row_y)

    # Chờ danh sách cập nhật trạng thái chọn trước khi nút mua được xử lý.
    deadline = time.time() + PLAYER_SELECTION_WAIT
    while time.time() < deadline:
        if stop_event.is_set():
            return False
        random_sleep(0.03, 0.05)

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

    random_sleep(*PRICE_DIALOG_WAIT)
    screen = capture_fco(hwnd)
    price = _read_price(screen)
    if price is None:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            "⚠️ Không đọc được giá, bỏ qua."
        )
        click_client(hwnd, ORDER_CANCEL_X, ORDER_CANCEL_Y)
        return previous_price, False, False

    log_callback(
        f"[Hàng {queue_row}][Vị trí {position}] "
        f"Giá hiện tại: {_format_price(price)}."
    )
    if previous_price is not None and price == previous_price:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] Giá không đổi, đã hủy; "
            "sẽ kiểm tra lại "
            "trong vòng kiểm tra tiếp theo."
        )
        key_press(hwnd, win32con.VK_ESCAPE)
        return previous_price, False, False

    if price > previous_price * MAX_PRICE_INCREASE_RATIO:
        next_reset = _next_reset_datetime(
            reset_code,
            datetime.now(),
        )
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] Giá {_format_price(price)} "
            "tăng hơn 30% "
            f"so với giá cũ {_format_price(previous_price)}; "
            "bỏ qua đến lần reset tiếp theo "
            f"({next_reset:%d/%m %H:%M})."
        )
        key_press(hwnd, win32con.VK_ESCAPE)
        return previous_price, False, True

    gif_frames = [
        Image.fromarray(cv2.cvtColor(screen, cv2.COLOR_BGR2RGB))
    ]
    _add_gif_frame(gif_frames, hwnd)
    click_client(hwnd, MAX_PRICE_X, MAX_PRICE_Y)
    _add_gif_frame(gif_frames, hwnd)
    if quantity > 1:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            f"Giá đã thay đổi, nhập số lượng {quantity}."
        )
        double_click_input(hwnd, QUANTITY_X, QUANTITY_Y)
        random_sleep(0.08, 0.12)
        type_text(hwnd, str(quantity))
        _add_gif_frame(gif_frames, hwnd)
    random_sleep(0.05, 0.08)
    click_client(hwnd, ORDER_BUY_X, ORDER_BUY_Y)
    _add_gif_frame(gif_frames, hwnd)
    log_callback(
        f"[Hàng {queue_row}][Vị trí {position}] ✅ Đã gửi lệnh mua "
        f"{quantity} cầu thủ; "
        f"giá trước khi mua: {_format_price(price)}."
    )

    deadline = time.time() + GIF_RETURN_WAIT
    while time.time() < deadline:
        if stop_event.is_set():
            break
        random_sleep(GIF_CAPTURE_INTERVAL, GIF_CAPTURE_INTERVAL)
        _add_gif_frame(gif_frames, hwnd)

    price_after_purchase = _read_price(capture_fco(hwnd))
    if price_after_purchase is None:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            "⚠️ Không đọc được giá sau khi mua "
            f"(giá trước khi mua: {_format_price(price)})."
        )
    else:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] Giá sau khi mua: "
            f"{_format_price(price_after_purchase)}."
        )

    gif_path = _save_purchase_gif(gif_frames, position, reset_code)
    if gif_path:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] 🎞️ Đã lưu GIF: {gif_path}"
        )

    return price, True, False


def _check_initial_price(
    hwnd,
    queue_row,
    position,
    stop_event,
    log_callback,
    already_selected=False,
):
    """Read the current max price after START without sending an order."""
    if not already_selected and not _select_player_row(
        hwnd, queue_row, position, stop_event, log_callback
    ):
        return None

    if not _click_purchase_button(hwnd, stop_event, log_callback):
        return None

    random_sleep(*PRICE_DIALOG_WAIT)
    price = _read_price(capture_fco(hwnd))
    click_client(hwnd, ORDER_CANCEL_X, ORDER_CANCEL_Y)

    if price is None:
        log_callback(
            f"[Hàng {queue_row}][Vị trí {position}] "
            "⚠️ Không đọc được giá max ban đầu."
        )
        return None

    log_callback(
        f"[Hàng {queue_row}][Vị trí {position}] "
        f"Giá max mốc khi đến lượt chèn: {_format_price(price)}."
    )
    return price


def run_player_insert(hwnd, entries, stop_event, log_callback):
    """Run scheduled player insertions until stopped."""
    if not entries:
        raise ValueError("Chưa cấu hình cầu thủ nào.")

    previous_prices = {}
    processed_slots = set()
    purchased_reset_slots = set()
    selected_row = None
    log_callback("🟢 Player Insert đang chạy.")

    sorted_entries = sort_entries_by_reset(entries)
    entries = [
        (queue_row, entry)
        for queue_row, (_source_row, entry) in enumerate(sorted_entries, 1)
    ]
    log_callback(
        "📋 Thứ tự hàng đợi: "
        + ", ".join(
            f"hàng {row_number} ({entry['reset']})"
            for row_number, entry in entries
        )
    )

    first_row_number, first_entry = entries[0]
    if not stop_event.is_set():
        if _select_player_row(
            hwnd,
            first_row_number,
            first_entry["position"],
            stop_event,
            log_callback,
        ):
            selected_row = first_row_number
            previous_prices[first_row_number] = _check_initial_price(
                hwnd,
                first_row_number,
                first_entry["position"],
                stop_event,
                log_callback,
                already_selected=True,
            )
            next_reset = _next_reset_datetime(
                first_entry["reset"],
                datetime.now(),
            )
            now = datetime.now()
            matches, reset_minute = _reset_matches_hour(
                first_entry["reset"],
                now.hour,
            )
            in_reset_window = (
                matches
                and reset_minute <= now.minute < reset_minute + 21
                and now.second < 12
            )
            if in_reset_window:
                log_callback(
                    f"[Hàng {first_row_number}][Vị trí {first_entry['position']}] "
                    "Đã chuẩn bị, đang trong cửa sổ reset."
                )
            else:
                log_callback(
                    f"[Hàng {first_row_number}][Vị trí {first_entry['position']}] "
                    f"Đã chuẩn bị, chờ reset lúc {next_reset:%d/%m %H:%M}."
                )

    while not stop_event.is_set():
        now = datetime.now()
        eligible_entries = []
        for row_number, entry in entries:
            reset_code = entry["reset"]
            matches, reset_minute = _reset_matches_hour(reset_code, now.hour)
            if (
                matches
                and reset_minute <= now.minute < reset_minute + 21
                and now.second < 12
            ):
                eligible_entries.append((row_number, entry, now))

        # Keep reset order so the earliest reset is processed first.
        for row_number, entry, scheduled_at in eligible_entries:
            if stop_event.is_set():
                break

            current = datetime.now()
            if (
                current.date() != scheduled_at.date()
                or current.hour != scheduled_at.hour
                or current.minute != scheduled_at.minute
                or current.second >= 12
            ):
                break

            slot = (
                row_number,
                scheduled_at.date(),
                scheduled_at.hour,
                scheduled_at.minute,
            )
            if slot in processed_slots:
                continue

            reset_slot = (row_number, scheduled_at.date(), scheduled_at.hour)
            if reset_slot in purchased_reset_slots:
                continue

            if selected_row != row_number:
                if not _select_player_row(
                    hwnd,
                    row_number,
                    entry["position"],
                    stop_event,
                    log_callback,
                ):
                    break
                selected_row = row_number

            if row_number not in previous_prices:
                previous_prices[row_number] = _check_initial_price(
                    hwnd,
                    row_number,
                    entry["position"],
                    stop_event,
                    log_callback,
                    already_selected=True,
                )

            (
                previous_prices[row_number],
                purchase_sent,
                price_blocked,
            ) = _insert_one(
                hwnd,
                row_number,
                entry["position"],
                entry["reset"],
                entry["quantity"],
                previous_prices.get(row_number),
                stop_event,
                log_callback,
            )
            if purchase_sent:
                processed_slots.add(slot)
                purchased_reset_slots.add(reset_slot)
                log_callback(
                    f"[Hàng {row_number}][Vị trí {entry['position']}] "
                    "Đã mua trong lần reset này; "
                    "chuyển ngay sang hàng kế tiếp."
                )
            elif price_blocked:
                processed_slots.add(slot)
                purchased_reset_slots.add(reset_slot)

        interruptible_sleep(0.25)

    log_callback("⛔ Player Insert đã dừng.")
