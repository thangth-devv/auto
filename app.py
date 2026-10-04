import customtkinter as ctk
import win32gui
import win32con
import threading
import ctypes
import os
import sys
import time
import json
import re
import unicodedata
import hashlib
from collections import Counter
from tkinter import messagebox

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageTk

from auto_buy import (
    capture_fco,
    click_client,
    key_press,
    play_notification,
    run_auto_buy,
    scroll_client,
)
from player_insert import normalize_reset_code, run_player_insert, sort_entries_by_reset


# ============================================================
# CONFIG
# ============================================================

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

APP_TITLE = "AUTO FCO"
APP_WIDTH = 460
APP_HEIGHT = 660

BG = "#080D11"
INNER_COLOR = "#0B1217"
SIDEBAR = "#0B1218"
CARD = "#101820"
CARD_2 = "#0C141A"
BORDER = "#24333D"

GREEN = "#27F07D"
GREEN_HOVER = "#35FF8B"
TEXT = "#F3F6F8"
MUTED = "#7F8C97"
DANGER = "#D85E5E"
WARNING = "#E6C04E"
UPGRADE_TITLE_ROI = (975, 58, 1135, 108)
OWNED_PLAYERS_TAB_ROI = (595, 108, 765, 166)
OVR_SORT_POSITION = (890, 230)
OVR_ARROW_ROI = (890, 218, 908, 242)
OVR_SORT_MAX_CLICKS = 3
# Keep the level region limited to the card badge so nearby columns cannot
# contaminate template matching.
PLAYER_OVR_ROI = (858, 246, 902, 281)
PLAYER_CARD_LEVEL_ROI = (904, 246, 950, 281)
PLAYER_ROW_FIRST_Y = 263
PLAYER_ROW_STEP_Y = 40
PLAYER_ROW_COUNT = 10
PLAYER_ROW_CLICK_X = 760
UPGRADE_NEXT_POSITION = (1025, 668)
UPGRADE_CONFIRM_POSITION = (1015, 575)
UPGRADE_CONFIRM_ROI = (940, 555, 1095, 595)
SKIP_PROMPT_ROI = (1035, 695, 1215, 745)
BUY_BULK_TAB_POSITION = (900, 140)
OWNED_PLAYERS_TAB_POSITION = (680, 140)
# Level badge at the lower-left corner of the selected player's card.
OWNED_CARD_LEVEL_ROI = (196, 257, 231, 281)
# The result-screen "Tiếp" button is the wide green control at the lower
# right. Keep this ROI tight so green navigation/status elements cannot
# satisfy the detector before the result screen is ready.
# The result-screen "Tiếp" button is near the lower-right corner. Keep this
# ROI tight enough to exclude the other controls shown during the animation.
RESULT_NEXT_ROI = (980, 688, 1150, 738)
PLAYER_LIST_MAX_SCROLLS = 12
PLAYER_LIST_SCROLL_POSITION = (1040, 500)
PLAYER_LIST_SCROLL_NOTCHES = -10
PURCHASED_PLAYERS_SCROLL_NOTCHES = 10
PLAYER_LIST_ROI = (595, 240, 1120, 660)
PLAYER_ID_ROI = (635, 250, 860, 276)
UPGRADE_RATE_ROI = (300, 512, 565, 542)
MAX_UPGRADE_PLAYERS = 5


# ============================================================
# GLOBAL STATE
# ============================================================

bot_thread = None
stop_event = None
is_running = False
current_hwnd = None
start_time = None
player_insert_window = None
player_insert_thread = None
player_insert_stop_event = None
player_insert_layout = None
upgrade_window = None
upgrade_layout = None
upgrade_stop_event = None


def _upgrade_stop_requested():
    return upgrade_stop_event is not None and upgrade_stop_event.is_set()


def _upgrade_sleep(seconds):
    """Sleep that wakes up immediately when STOP is requested."""
    if upgrade_stop_event is not None:
        return upgrade_stop_event.wait(seconds)
    time.sleep(seconds)
    return False


# ============================================================
# RESOURCE
# ============================================================

def resource_path(relative_path):
    if getattr(sys, "frozen", False):
        base_path = getattr(
            sys,
            "_MEIPASS",
            os.path.dirname(sys.executable)
        )
    else:
        base_path = os.path.dirname(
            os.path.abspath(__file__)
        )

    return os.path.join(
        base_path,
        relative_path
    )


PROFILE_FILE = resource_path("profiles.json")
profiles = {}

ICON_PATH = resource_path("icon.png")


# ============================================================
# WINDOWS APP ID
# ============================================================

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "FCOnline.AutoBuy"
    )
except Exception:
    pass


# ============================================================
# FIND FC ONLINE
# ============================================================

def find_fc_online():
    candidates = []
    ignored_classes = {
        "PROGMAN",
        "WORKERW",
        "SHELL_TRAYWND",
        "WINDOWS.UI.CORE.COREWINDOW",
        "XAMLEXPLORERHOSTISLANDWINDOW"
    }

    def enum_window(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return

        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except win32gui.error:
            return

        width = right - left
        height = bottom - top

        if width < 640 or height < 360:
            return

        title = win32gui.GetWindowText(hwnd)
        class_name = win32gui.GetClassName(hwnd)
        title_upper = title.strip().upper()
        class_upper = class_name.strip().upper()

        if class_upper in ignored_classes:
            return

        try:
            owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        except win32gui.error:
            return

        if owner or ex_style & win32con.WS_EX_TOOLWINDOW:
            return

        # Chỉ nhận đúng title của client game, tránh bắt nhầm các cửa sổ
        # có chứa "FC ONLINE" như tiêu đề phụ hoặc công cụ khác.
        if title_upper != "FC ONLINE":
            return

        candidates.append(
            (
                1,
                width * height,
                hwnd
            )
        )

    win32gui.EnumWindows(
        enum_window,
        None
    )

    if not candidates:
        return None

    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def auto_scan_fc_online():
    global current_hwnd

    if not is_running:
        if (
            current_hwnd is None
            or not win32gui.IsWindow(current_hwnd)
        ):
            current_hwnd = find_fc_online()

    app.after(
        2000,
        auto_scan_fc_online
    )


# ============================================================
# UI CALLBACK
# ============================================================

def add_log(message):
    def update():
        status_text.insert(
            "end",
            message + "\n"
        )
        status_text.see("end")

    app.after(
        0,
        update
    )


def update_status(message):

    def update():

        status_label.configure(
            text=message
        )

        if "RUNNING" in message:

            status_dot.configure(
                text_color=GREEN
            )

            status_hint.configure(
                text="Bot đang hoạt động"
            )

        elif "STOPPING" in message:

            status_dot.configure(
                text_color=WARNING
            )

            status_hint.configure(
                text="Đang dừng bot..."
            )

        elif "ERROR" in message:

            status_dot.configure(
                text_color=DANGER
            )

            status_hint.configure(
                text="Đã xảy ra lỗi"
            )

        else:

            status_dot.configure(
                text_color=GREEN
            )

            status_hint.configure(
                text="Sẵn sàng hoạt động"
            )

    app.after(
        0,
        update
    )


def update_player_count(
    current,
    target
):

    def update():

        player_count_value.configure(
            text=f"{current} / {target}"
        )

        if target > 0:

            progress_bar.set(
                min(
                    current / target,
                    1.0
                )
            )

        else:

            progress_bar.set(0)

    app.after(
        0,
        update
    )


def update_timer():

    if (
        start_time is not None
        and is_running
    ):

        elapsed = int(
            time.time() - start_time
        )

        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        seconds = elapsed % 60

        timer_value.configure(
            text=(
                f"{hours:02d}:"
                f"{minutes:02d}:"
                f"{seconds:02d}"
            )
        )

    app.after(
        1000,
        update_timer
    )


# ============================================================
# BOT WORKER
# ============================================================

def bot_worker(
    hwnd,
    target_players,
    threshold,
    stat_min,
    stat_max,
    max_card_price,
    quantity,
    current_stop_event
):

    global is_running

    try:

        update_status(
            "🟢 BOT: RUNNING"
        )

        add_log(
            "FC Online Auto Buy"
        )

        add_log(
            "----------------------------------------"
        )

        add_log(
            f"Mục tiêu: {target_players} cầu thủ"
        )

        add_log(
            f"Filter: MIN={stat_min} | MAX={stat_max} | "
            f"Giá/thẻ={max_card_price:,}"
        )

        add_log(
            "----------------------------------------"
        )

        add_log(
            "▶ START BOT"
        )

        add_log(
            "🚀 Bot bắt đầu chạy..."
        )

        run_auto_buy(
            hwnd=hwnd,
            target_players=target_players,
            threshold=threshold,
            stat_min=stat_min,
            stat_max=stat_max,
            max_card_price=max_card_price,
            quantity=quantity,
            stop_event=current_stop_event,
            log_callback=add_log,
            player_count_callback=update_player_count
        )

    except Exception as exc:

        add_log(
            f"❌ BOT ERROR: {exc}"
        )

        update_status(
            "🔴 BOT ERROR"
        )

    finally:

        is_running = False

        app.after(
            0,
            bot_finished
        )


# ============================================================
# BOT FINISHED
# ============================================================

def bot_finished():

    global start_time

    start_time = None

    start_button.configure(
        state="normal"
    )

    stop_button.configure(
        state="disabled"
    )

    update_status(
        "⚪ BOT: STOPPED"
    )



# ============================================================
# PROFILE MANAGEMENT
# ============================================================

def load_profiles():
    global profiles

    try:
        if os.path.exists(PROFILE_FILE):
            with open(
                PROFILE_FILE,
                "r",
                encoding="utf-8"
            ) as f:
                data = json.load(f)

            profiles = data if isinstance(data, dict) else {}
        else:
            profiles = {}

    except Exception as exc:
        profiles = {}
        add_log(
            f"⚠️ Không đọc được profiles.json: {exc}"
        )


def save_profiles():
    try:
        with open(
            PROFILE_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                profiles,
                f,
                ensure_ascii=False,
                indent=2
            )

        return True

    except Exception as exc:
        add_log(
            f"❌ Không lưu được profile: {exc}"
        )
        return False


def refresh_profile_combo():
    names = list(profiles.keys())

    profile_combo.configure(
        values=names
    )

    if names:
        current = profile_combo.get()

        if current not in names:
            profile_combo.set(names[0])
    else:
        profile_combo.set("")


def profile_usage_count(data):
    if not isinstance(data, dict):
        return 0

    usage_count = data.get("usage_count", 0)

    if isinstance(usage_count, bool):
        return 0

    if isinstance(usage_count, int):
        return max(usage_count, 0)

    return 0


def select_most_used_profile():
    names = list(profiles.keys())

    if not names:
        return

    selected_name = max(
        names,
        key=lambda name: profile_usage_count(profiles.get(name))
    )

    profile_combo.set(selected_name)
    on_profile_selected(selected_name)


def on_profile_selected(name):
    data = profiles.get(name)

    if not isinstance(data, dict):
        return

    stat_min_entry.delete(0, "end")
    stat_min_entry.insert(
        0,
        str(data.get("stat_min", 97))
    )

    stat_max_entry.delete(0, "end")
    stat_max_entry.insert(
        0,
        str(data.get("stat_max", 99))
    )

    max_price_entry.delete(0, "end")
    max_price_entry.insert(
        0,
        f"{int(data.get('max_card_price', 10000)):,}"
    )

    add_log(
        f"📁 Đã chọn profile: {name}"
    )


def save_current_profile():
    name = profile_name_entry.get().strip()

    if not name:
        add_log(
            "❌ Nhập tên profile trước."
        )
        return

    try:
        stat_min = int(
            stat_min_entry.get().strip()
        )

        stat_max = int(
            stat_max_entry.get().strip()
        )

        max_card_price = int(
            re.sub(
                r"[^\d]",
                "",
                max_price_entry.get()
            )
        )

        quantity = int(
            quantity_entry.get().strip()
        )

        if stat_min <= 0 or stat_max <= 0 or quantity <= 0:
            raise ValueError

        if quantity > 10:
            quantity = 10
            quantity_entry.delete(0, "end")
            quantity_entry.insert(0, "10")
            add_log(
                "⚠️ Số lượng tối đa là 10; đã tự động đổi về 10."
            )

        if stat_min > stat_max:
            add_log(
                "❌ Chỉ số MIN không được lớn hơn MAX."
            )
            return

        if max_card_price <= 0:
            raise ValueError

    except ValueError:
        add_log(
            "❌ MIN / MAX / Giá tối đa / Số lượng không hợp lệ."
        )
        return

    current_data = profiles.get(name)
    profiles[name] = {
        "stat_min": stat_min,
        "stat_max": stat_max,
        "max_card_price": max_card_price,
        "usage_count": profile_usage_count(current_data)
    }

    if save_profiles():
        refresh_profile_combo()
        profile_combo.set(name)
        add_log(
            f"💾 Đã lưu profile '{name}'."
        )


def record_profile_usage():
    name = profile_combo.get().strip()
    data = profiles.get(name)

    if not isinstance(data, dict):
        return

    data["usage_count"] = profile_usage_count(data) + 1
    save_profiles()


def delete_current_profile():
    name = profile_combo.get().strip()

    if not name:
        add_log(
            "⚠️ Chưa chọn profile để xóa."
        )
        return

    if name not in profiles:
        add_log(
            f"⚠️ Không tìm thấy profile '{name}'."
        )
        return

    del profiles[name]

    if save_profiles():
        refresh_profile_combo()
        profile_name_entry.delete(0, "end")

        # Không xóa giá trị filter đang hiển thị,
        # chỉ bỏ profile khỏi danh sách.
        add_log(
            f"🗑️ Đã xóa profile '{name}'."
        )


def format_price_entry(event=None):
    value = re.sub(
        r"[^\d]",
        "",
        max_price_entry.get()
    )

    formatted = (
        f"{int(value):,}"
        if value
        else ""
    )

    if max_price_entry.get() != formatted:
        max_price_entry.delete(0, "end")
        max_price_entry.insert(0, formatted)


# ============================================================
# START BOT
# ============================================================

def start_bot():

    global bot_thread
    global stop_event
    global is_running
    global current_hwnd
    global start_time

    if is_running:

        add_log(
            "⚠️ Bot đang chạy."
        )

        return

    # --------------------------------------------------------
    # FIND FC ONLINE
    # --------------------------------------------------------

    hwnd = find_fc_online()

    if hwnd is None:

        current_hwnd = None

        add_log(
            "❌ Không tìm thấy FC ONLINE."
        )

        update_status(
            "⚪ BOT: READY"
        )

        return

    current_hwnd = hwnd

    if not win32gui.IsWindow(hwnd):

        add_log(
            "❌ Cửa sổ FC ONLINE không còn tồn tại."
        )

        update_status(
            "⚪ BOT: READY"
        )

        return

    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except win32gui.error:
        add_log(
            "❌ Không đọc được kích thước cửa sổ FC ONLINE."
        )
        return

    if right <= left or bottom <= top:
        add_log(
            "⚠️ Cửa sổ FC ONLINE chưa sẵn sàng. "
            "Vui lòng thử lại sau."
        )
        return

    add_log(
        f"🎮 Đã nhận diện cửa sổ FC ONLINE: "
        f"HWND={hwnd}, kích thước={right - left}x{bottom - top}, "
        f"class={win32gui.GetClassName(hwnd)}"
    )

    # --------------------------------------------------------
    # VALIDATE INPUT
    # --------------------------------------------------------

    try:

        target_players = int(
            target_entry.get().strip()
        )

        if target_players <= 0:

            raise ValueError


    except ValueError:

        add_log(
            "❌ Số cầu thủ phải > 0."
        )

        return

    # --------------------------------------------------------
    # VALIDATE PURCHASE FILTERS
    # --------------------------------------------------------

    try:
        stat_min = int(
            stat_min_entry.get().strip()
        )

        stat_max = int(
            stat_max_entry.get().strip()
        )

        max_card_price = int(
            re.sub(
                r"[^\d]",
                "",
                max_price_entry.get()
            )
        )

        quantity = int(
            quantity_entry.get().strip()
        )

        if stat_min <= 0 or stat_max <= 0 or quantity <= 0:
            raise ValueError

        if stat_min > stat_max:
            add_log(
                "❌ Chỉ số MIN không được lớn hơn MAX."
            )
            return

        if max_card_price <= 0:
            raise ValueError

    except ValueError:
        add_log(
            "❌ MIN / MAX / Giá tối đa không hợp lệ."
        )
        return

    record_profile_usage()

    # --------------------------------------------------------
    # SETTINGS
    # --------------------------------------------------------

    threshold = 0.85

    stop_event = threading.Event()

    is_running = True

    start_time = time.time()

    # --------------------------------------------------------
    # RESET UI
    # --------------------------------------------------------

    status_text.delete(
        "1.0",
        "end"
    )

    progress_bar.set(0)

    update_player_count(
        0,
        target_players
    )

    start_button.configure(
        state="disabled"
    )

    stop_button.configure(
        state="normal"
    )

    update_status(
        "🟢 BOT: RUNNING"
    )

    # --------------------------------------------------------
    # START THREAD
    # --------------------------------------------------------

    bot_thread = threading.Thread(
        target=bot_worker,
        args=(
            hwnd,
            target_players,
            threshold,
            stat_min,
            stat_max,
            max_card_price,
            quantity,
            stop_event
        ),
        daemon=True
    )

    bot_thread.start()


# ============================================================
# STOP BOT
# ============================================================

def stop_bot():

    if not is_running:

        add_log(
            "⚠️ Bot không đang chạy."
        )

        return

    add_log(
        "⛔ Đang yêu cầu STOP..."
    )

    if stop_event is not None:

        stop_event.set()

    stop_button.configure(
        state="disabled"
    )

    update_status(
        "🟡 BOT: STOPPING..."
    )


# ============================================================
# CLEAR LOG
# ============================================================

def clear_log():

    status_text.delete(
        "1.0",
        "end"
    )

    status_text.insert(
        "end",
        "FC Online Auto Buy\n"
        "----------------------------------------\n"
        "Status: READY\n"
        "Đã mua: 0 / 10\n"
        "\n"
        "Chưa bắt đầu.\n"
    )


# ============================================================
# CREATE APP
# ============================================================

app = ctk.CTk()

app.title(
    APP_TITLE
)

app.geometry(
    f"{APP_WIDTH}x{APP_HEIGHT}"
)

app.resizable(
    False,
    False
)


# ============================================================
# APP ICON
# ============================================================

if os.path.exists(ICON_PATH):

    try:

        icon_image = Image.open(
            ICON_PATH
        ).convert("RGBA")

        icon_image.thumbnail(
            (256, 256),
            Image.Resampling.LANCZOS
        )

        icon_photo = ImageTk.PhotoImage(
            icon_image
        )

        app.wm_iconphoto(
            True,
            icon_photo
        )

    except Exception as exc:

        print(
            f"⚠️ Không load được icon: {exc}"
        )


# ============================================================
# ROOT
# ============================================================

root = ctk.CTkFrame(
    app,
    fg_color=BG,
    corner_radius=0
)

root.pack(
    fill="both",
    expand=True
)

root.grid_columnconfigure(
    0,
    weight=1
)
root.grid_columnconfigure(
    1,
    weight=0
)

root.grid_rowconfigure(
    0,
    weight=1
)


# ============================================================
# SIDEBAR
# ============================================================

sidebar = ctk.CTkFrame(
    root,
    width=52,
    fg_color=SIDEBAR,
    corner_radius=0
)

sidebar.grid(
    row=0,
    column=0,
    sticky="nsew"
)

sidebar.grid_propagate(
    False
)
sidebar.grid_remove()


# ============================================================
# SIDEBAR LOGO
# ============================================================

side_logo_box = ctk.CTkFrame(
    sidebar,
    width=44,
    height=48,
    corner_radius=14,
    fg_color="#0D2A1B",
    border_width=1,
    border_color="#1D7044"
)

side_logo_box.pack(
    pady=(14, 18)
)

side_logo_box.pack_propagate(
    False
)


if os.path.exists(ICON_PATH):

    try:

        side_img = Image.open(
            ICON_PATH
        )

        side_photo = ctk.CTkImage(
            light_image=side_img,
            dark_image=side_img,
            size=(32, 32)
        )

        ctk.CTkLabel(
            side_logo_box,
            text="",
            image=side_photo
        ).pack(
            expand=True
        )

    except Exception:

        ctk.CTkLabel(
            side_logo_box,
            text="FC",
            text_color=GREEN,
            font=ctk.CTkFont(
                size=15,
                weight="bold"
            )
        ).pack(
            expand=True
        )

else:

    ctk.CTkLabel(
        side_logo_box,
        text="FC",
        text_color=GREEN,
        font=ctk.CTkFont(
            size=15,
            weight="bold"
        )
    ).pack(
        expand=True
    )


# ============================================================
# SIDEBAR ITEMS
# ============================================================

sidebar_items = []


def set_active_sidebar_item(selected_frame):
    for item in sidebar_items:
        is_selected = item["frame"] is selected_frame
        item["selected"] = is_selected
        item["frame"].configure(
            fg_color="#0D281B" if is_selected else "transparent",
            border_width=1 if is_selected else 0,
            border_color="#1C7546",
        )
        item["label"].configure(
            text_color=GREEN if is_selected else MUTED
        )


def sidebar_item(icon, active=False, command=None):

    frame = ctk.CTkFrame(
        sidebar,
        width=44,
        height=40,
        corner_radius=14,
        cursor="hand2",
        fg_color=(
            "#0D281B"
            if active
            else "transparent"
        ),
        border_width=(
            1
            if active
            else 0
        ),
        border_color="#1C7546"
    )

    frame.pack(
        padx=4,
        pady=5
    )

    frame.pack_propagate(
        False
    )

    label = ctk.CTkLabel(
        frame,
        text=icon,
        cursor="hand2",
        text_color=(
            GREEN
            if active
            else MUTED
        ),
        font=ctk.CTkFont(
            size=14,
            weight="bold"
        )
    )

    label.pack(
        expand=True
    )

    item = {
        "frame": frame,
        "label": label,
        "selected": active,
    }
    sidebar_items.append(item)

    def select_item(_event=None):
        set_active_sidebar_item(frame)
        if command is not None:
            command()
        return "break"

    def on_enter(_event=None):
        frame.configure(
            fg_color="#163D29",
            border_width=1,
            border_color="#1C7546",
        )
        label.configure(text_color=GREEN_HOVER)

    def on_leave(_event=None):
        is_selected = item["selected"]
        frame.configure(
            fg_color="#0D281B" if is_selected else "transparent",
            border_width=1 if is_selected else 0,
            border_color="#1C7546",
        )
        label.configure(text_color=GREEN if is_selected else MUTED)

    frame.bind("<Button-1>", select_item)
    label.bind("<Button-1>", select_item)
    frame.bind("<Enter>", on_enter)
    frame.bind("<Leave>", on_leave)
    label.bind("<Enter>", on_enter)
    label.bind("<Leave>", on_leave)

    return frame


sidebar_item(
    "⌂",
    active=True,
    command=lambda: show_auto_buy()
)

def _hide_main_content():
    layout = []
    for widget in main.winfo_children():
        if widget is topbar:
            continue
        layout.append((widget, widget.pack_info()))
        widget.pack_forget()
    return layout


def _restore_main_content(layout):
    if layout is None:
        return
    for widget, widget_layout in layout:
        widget.pack(**widget_layout)


def _upgrade_screen_detected(hwnd):
    screen = capture_fco(hwnd)
    if not _owned_players_tab_selected(screen):
        return False

    height, width = screen.shape[:2]
    roi_left, roi_top, roi_right, roi_bottom = UPGRADE_TITLE_ROI
    scale_x = width / 1280
    scale_y = height / 752
    title_region = screen[
        int(roi_top * scale_y):int(roi_bottom * scale_y),
        int(roi_left * scale_x):int(roi_right * scale_x),
    ]
    if title_region.size == 0:
        return False

    gray = cv2.cvtColor(title_region, cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(
        gray,
        None,
        fx=4,
        fy=4,
        interpolation=cv2.INTER_CUBIC,
    )
    variants = (
        enlarged,
        cv2.threshold(
            enlarged,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )[1],
        cv2.threshold(
            enlarged,
            150,
            255,
            cv2.THRESH_BINARY,
        )[1],
    )
    for variant in variants:
        text = pytesseract.image_to_string(
            variant,
            config="--psm 7",
            lang="eng",
        ).lower()
        normalized = text.translate(str.maketrans({"đ": "d", "Đ": "D"}))
        normalized = unicodedata.normalize("NFKD", normalized)
        normalized = "".join(
            character
            for character in normalized
            if not unicodedata.combining(character)
        )
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        if "nang cap cau thu" in normalized or "nang cap" in normalized:
            return True

    # The title is grey on a dark background and can be missed by OCR.
    title_gray = cv2.cvtColor(title_region, cv2.COLOR_BGR2GRAY)
    title_text_band = title_gray[
        int(title_gray.shape[0] * 0.25):int(title_gray.shape[0] * 0.85),
        int(title_gray.shape[1] * 0.05):int(title_gray.shape[1] * 0.95),
    ]
    return float((title_text_band >= 95).mean()) >= 0.025


def _owned_players_tab_selected(screen):
    height, width = screen.shape[:2]
    roi_left, roi_top, roi_right, roi_bottom = OWNED_PLAYERS_TAB_ROI
    scale_x = width / 1280
    scale_y = height / 752
    tab_region = screen[
        int(roi_top * scale_y):int(roi_bottom * scale_y),
        int(roi_left * scale_x):int(roi_right * scale_x),
    ]
    if tab_region.size == 0:
        return False

    gray = cv2.cvtColor(tab_region, cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(
        gray,
        None,
        fx=4,
        fy=4,
        interpolation=cv2.INTER_CUBIC,
    )
    text = pytesseract.image_to_string(
        enlarged,
        config="--psm 7",
        lang="eng",
    ).lower()
    normalized = text.translate(str.maketrans({"đ": "d", "Đ": "D"}))
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    has_owned_players_text = (
        "cau thu" in normalized
        and ("dang so huu" in normalized or "so huu" in normalized)
    )

    # The selected tab has a bright horizontal underline at the bottom.
    underline = gray[int(gray.shape[0] * 0.72):, :]
    bright_ratio = float((underline >= 130).mean())
    selected = bright_ratio >= 0.035
    return selected


def _ovr_sorted_ascending(screen, log_callback=None):
    height, width = screen.shape[:2]
    left, top, right, bottom = OVR_ARROW_ROI
    scale_x = width / 1280
    scale_y = height / 752
    arrow_region = screen[
        int(top * scale_y):int(bottom * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if arrow_region.size == 0:
        return False

    gray = cv2.cvtColor(arrow_region, cv2.COLOR_BGR2GRAY)
    bright = cv2.threshold(gray, 115, 255, cv2.THRESH_BINARY)[1]
    bright = cv2.morphologyEx(
        bright,
        cv2.MORPH_OPEN,
        np.ones((2, 2), dtype=np.uint8),
    )
    row_counts = (bright > 0).sum(axis=1)
    total_bright = int(row_counts.sum())
    if total_bright < 8:
        if log_callback is not None:
            log_callback(f"🔬 OVR icon: quá ít điểm sáng ({total_bright}).")
        return False

    peak_row = int(row_counts.argmax())
    if peak_row < 3 or peak_row >= len(row_counts) - 3:
        if log_callback is not None:
            log_callback(f"🔬 OVR icon: không có đỉnh hợp lệ ({peak_row}).")
        return False

    upper_pixels = int(row_counts[:peak_row].sum())
    lower_pixels = int(row_counts[peak_row + 1:].sum())
    upper_peak = int(row_counts[:peak_row].max())
    lower_peak = int(row_counts[peak_row + 1:].max())
    detected = (
        upper_peak >= 2
        and lower_peak >= 1
        and upper_pixels >= 3
        and lower_pixels >= 2
        and row_counts[peak_row] >= upper_peak
        and row_counts[peak_row] >= lower_peak
        and total_bright <= 150
    )
    if log_callback is not None:
        log_callback(
            "🔬 OVR icon metrics: "
            f"upper={upper_pixels}, peak={int(row_counts[peak_row])}, "
            f"lower={lower_pixels}, total={total_bright}, "
            f"detected={detected}."
        )
    return detected


def _sort_ovr_ascending(hwnd, log_upgrade):
    for attempt in range(OVR_SORT_MAX_CLICKS + 1):
        log_upgrade(
            f"🔎 Đang kiểm tra icon OVR (lần {attempt + 1}/{OVR_SORT_MAX_CLICKS + 1})..."
        )
        screen = capture_fco(hwnd)
        detected = _ovr_sorted_ascending(screen, log_upgrade)
        if detected:
            log_upgrade("✅ Cột OVR đã ở trạng thái tăng dần.")
            return True

        if attempt == OVR_SORT_MAX_CLICKS:
            break

        log_upgrade(
            f"🖱️ Click OVR tại {OVR_SORT_POSITION[0]}, {OVR_SORT_POSITION[1]}."
        )
        _upgrade_click(
            hwnd,
            OVR_SORT_POSITION[0],
            OVR_SORT_POSITION[1],
        )
        log_upgrade(
            f"↕️ Đã click cột OVR lần {attempt + 1}; đang kiểm tra icon..."
        )
        _upgrade_sleep(1.0)

    return False


def _upgrade_click(hwnd, image_x, image_y):
    """Send upgrade clicks to the game without moving the user's cursor."""
    click_client(
        hwnd,
        image_x,
        image_y,
        method="message",
        fast=True,
    )


def _row_background_is_dark(screen, row_y, threshold=55):
    """Detect the dimmed row background of special (registered/locked) cards.

    Normal player rows use a bright background, while special rows
    (e.g. the equipped player at the top of the list) are noticeably
    darker. Compare against the list's overall brightness so the check
    works at any window size.
    """
    height, width = screen.shape[:2]
    scale_x = width / 1280
    scale_y = height / 752
    roi = screen[
        int((row_y - 14) * scale_y):int((row_y + 14) * scale_y),
        int(595 * scale_x):int(1120 * scale_x),
    ]
    if roi.size == 0:
        return False

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    row_mean = float(gray.mean())
    # Use a wider horizontal band of the list as the reference brightness.
    reference = screen[
        int(240 * scale_y):int(660 * scale_y),
        int(595 * scale_x):int(1120 * scale_x),
    ]
    reference_mean = (
        float(cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY).mean())
        if reference.size
        else row_mean
    )
    return row_mean < threshold and reference_mean - row_mean >= 18


def _read_player_ovr(screen, row_y):
    height, width = screen.shape[:2]
    left, top, right, bottom = PLAYER_OVR_ROI
    scale_x = width / 1280
    scale_y = height / 752
    row_half_height = (bottom - top) / 2
    roi = screen[
        int((row_y - row_half_height) * scale_y):
        int((row_y + row_half_height) * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return None

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # Game renders OVR as light digits on a dark background. Tesseract
    # prefers dark text on a white background, so build both polarities
    # and let either one produce a valid read.
    dark_text = 255 - gray
    enlarged_dark = cv2.resize(
        dark_text,
        None,
        fx=6,
        fy=6,
        interpolation=cv2.INTER_CUBIC,
    )
    enlarged_light = cv2.resize(
        gray,
        None,
        fx=6,
        fy=6,
        interpolation=cv2.INTER_CUBIC,
    )
    variants = (
        enlarged_dark,
        cv2.threshold(
            enlarged_dark,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )[1],
        cv2.threshold(
            enlarged_light,
            0,
            255,
            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
        )[1],
    )
    candidates = []
    for variant in variants:
        for psm in (7, 8, 13):
            text = pytesseract.image_to_string(
                variant,
                config=(
                    f"--psm {psm} "
                    "-c tessedit_char_whitelist=0123456789"
                ),
                lang="eng",
            )
            # OVR is numeric-only. Accept only clean reads, and collect
            # every candidate so a noisy row can still resolve through
            # the best-candidate fallback below.
            normalized_text = re.sub(r"\s+", "", text)
            if re.fullmatch(r"\d{2,3}", normalized_text):
                value = int(normalized_text)
                if 1 <= value <= 200:
                    return value
    # No clean read. Fall back to the most common plausible two-digit
    # number found anywhere in the OCR output (90-149 keeps us inside
    # realistic OVR values while still skipping garbage reads).
    for variant in variants:
        text = pytesseract.image_to_string(
            variant,
            config=(
                "--psm 7 "
                "-c tessedit_char_whitelist=0123456789"
            ),
            lang="eng",
        )
        for match in re.finditer(r"\d{2,3}", text):
            value = int(match.group(0))
            if 90 <= value <= 149:
                candidates.append(value)
    if candidates:
        return Counter(candidates).most_common(1)[0][0]
    return None


_CARD_LEVEL_TEMPLATES = None


def _load_card_level_templates():
    global _CARD_LEVEL_TEMPLATES
    if _CARD_LEVEL_TEMPLATES is not None:
        return _CARD_LEVEL_TEMPLATES

    templates = []
    for level in range(1, 14):
        path = resource_path(
            os.path.join("templates", f"card_level_{level}.png")
        )
        template = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise FileNotFoundError(
                f"Không load được template cấp thẻ: {path}"
            )
        templates.append((level, template))
    _CARD_LEVEL_TEMPLATES = tuple(templates)
    return _CARD_LEVEL_TEMPLATES


def _match_card_level_template(screen, roi_box, threshold=0.62):
    height, width = screen.shape[:2]
    left, top, right, bottom = roi_box
    scale_x = width / 1280
    scale_y = height / 752
    roi = screen[
        int(top * scale_y):int(bottom * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return None

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    best = None
    for level, template in _load_card_level_templates():
        scaled = cv2.resize(
            template,
            (
                max(1, int(round(template.shape[1] * scale_x))),
                max(1, int(round(template.shape[0] * scale_y))),
            ),
            interpolation=cv2.INTER_AREA,
        )
        if (
            scaled.shape[0] > gray.shape[0]
            or scaled.shape[1] > gray.shape[1]
        ):
            continue

        for candidate in (gray, cv2.equalizeHist(gray)):
            result = cv2.matchTemplate(
                candidate,
                scaled,
                cv2.TM_CCOEFF_NORMED,
            )
            confidence = float(cv2.minMaxLoc(result)[1])
            if best is None or confidence > best[1]:
                best = (level, confidence)

    if best is None or best[1] < threshold:
        return None
    return best


def _read_player_card_level(screen, row_y):
    badge_roi = (
        910,
        row_y - 11,
        940,
        row_y + 11,
    )
    match = _match_card_level_template(screen, badge_roi)
    return None if match is None else match[0]


def _read_player_card_level_ocr(screen, row_y):
    height, width = screen.shape[:2]
    left, top, right, bottom = PLAYER_CARD_LEVEL_ROI
    scale_x = width / 1280
    scale_y = height / 752
    row_half_height = (bottom - top) / 2
    roi = screen[
        int((row_y - row_half_height) * scale_y):
        int((row_y + row_half_height) * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return None

    # Read the badge itself first. The surrounding table contains OVR,
    # position arrows, and player text; a broad ROI lets those elements
    # suppress the small level glyph during OCR.
    badge_left = int(910 * scale_x)
    badge_right = int(940 * scale_x)
    badge_top = int((row_y - 11) * scale_y)
    badge_bottom = int((row_y + 11) * scale_y)
    badge_roi = screen[badge_top:badge_bottom, badge_left:badge_right]
    if badge_roi.size:
        badge_gray = cv2.cvtColor(badge_roi, cv2.COLOR_BGR2GRAY)
        badge_scaled = cv2.resize(
            badge_gray,
            None,
            fx=14,
            fy=14,
            interpolation=cv2.INTER_CUBIC,
        )
        badge_scaled = cv2.copyMakeBorder(
            badge_scaled,
            30,
            30,
            30,
            30,
            cv2.BORDER_CONSTANT,
            value=0,
        )
        badge_variants = (
            badge_scaled,
            cv2.threshold(
                badge_scaled,
                0,
                255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU,
            )[1],
            cv2.threshold(
                badge_scaled,
                0,
                255,
                cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
            )[1],
        )
        for badge_variant in badge_variants:
            for psm in (7, 8, 10, 13):
                badge_text = pytesseract.image_to_string(
                    badge_variant,
                    config=(
                        f"--psm {psm} "
                        "-c tessedit_char_whitelist=0123456789"
                    ),
                    lang="eng",
                )
                badge_text = re.sub(r"\s+", "", badge_text)
                if re.fullmatch(r"(?:[1-9]|1[0-3])", badge_text):
                    return int(badge_text)

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(
        gray,
        None,
        fx=6,
        fy=6,
        interpolation=cv2.INTER_CUBIC,
    )
    # Remove the badge border/background before OCR. This is especially
    # important for level 1, whose single narrow stroke is easy to lose.
    inner = enlarged[
        max(0, int(enlarged.shape[0] * 0.06)):
        int(enlarged.shape[0] * 0.94),
        max(0, int(enlarged.shape[1] * 0.06)):
        int(enlarged.shape[1] * 0.94),
    ]
    # Fast path: two focused OCR passes cover normal badges and the
    # low-contrast digits without running every Tesseract PSM.
    padded = cv2.copyMakeBorder(
        enlarged,
        12,
        12,
        12,
        12,
        cv2.BORDER_CONSTANT,
        value=0,
    )
    fast_variants = (
        padded,
        cv2.threshold(
            padded,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )[1],
    )
    candidates = []
    for variant in fast_variants:
        for psm in (7, 10, 13):
            text = pytesseract.image_to_string(
                variant,
                config=(
                    f"--psm {psm} "
                    "-c tessedit_char_whitelist=0123456789"
                ),
                lang="eng",
            )
            # Whitespace around the OCR token is harmless; any other
            # character makes the result invalid.
            normalized_text = re.sub(r"\s+", "", text)
            if re.fullmatch(r"(?:[1-9]|1[0-3])", normalized_text):
                candidates.append(int(normalized_text))

    # Direct badge pass for small single-digit levels such as the +6 shown
    # beside OVR 82. Keep the original badge proportions and use a narrow
    # numeric whitelist so neighboring columns cannot affect the result.
    direct_badge = cv2.resize(
        gray,
        None,
        fx=10,
        fy=10,
        interpolation=cv2.INTER_CUBIC,
    )
    direct_variants = (
        direct_badge,
        cv2.threshold(
            direct_badge,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )[1],
        cv2.threshold(
            direct_badge,
            0,
            255,
            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
        )[1],
    )
    for variant in direct_variants:
        for psm in (8, 10, 13):
            text = pytesseract.image_to_string(
                variant,
                config=(
                    f"--psm {psm} "
                    "-c tessedit_char_whitelist=0123456789"
                ),
                lang="eng",
            )
            normalized_text = re.sub(r"\s+", "", text)
            if re.fullmatch(r"(?:[1-9]|1[0-3])", normalized_text):
                candidates.append(int(normalized_text))

    # Fallback only for difficult badges. It uses the trimmed ROI and two
    # alternate segmentation modes, so the common path remains fast.
    if not candidates and inner.size:
        fallback = cv2.copyMakeBorder(
            inner,
            12,
            12,
            12,
            12,
            cv2.BORDER_CONSTANT,
            value=0,
        )
        fallback_variants = (
            fallback,
            cv2.threshold(
                fallback,
                0,
                255,
                cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
            )[1],
        )
        for variant in fallback_variants:
            for psm in (10, 13):
                text = pytesseract.image_to_string(
                    variant,
                    config=(
                        f"--psm {psm} "
                        "-c tessedit_char_whitelist=0123456789"
                    ),
                    lang="eng",
                )
                normalized_text = re.sub(r"\s+", "", text)
                if re.fullmatch(r"(?:[1-9]|1[0-3])", normalized_text):
                    candidates.append(int(normalized_text))

    if not candidates:
        # The game badge is always a positive level. If OCR cannot resolve
        # the tiny glyph, keep the player eligible with the safest default.
        return 1
    return Counter(candidates).most_common(1)[0][0]


def _player_identity(screen, row_y):
    height, width = screen.shape[:2]
    left, top, right, bottom = PLAYER_ID_ROI
    scale_x = width / 1280
    scale_y = height / 752
    row_half_height = (bottom - top) / 2
    roi = screen[
        int((row_y - row_half_height) * scale_y):
        int((row_y + row_half_height) * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    reduced = cv2.resize(gray, (45, 6), interpolation=cv2.INTER_AREA)
    return hashlib.sha1(reduced.tobytes()).digest()


def _player_list_signature(screen):
    height, width = screen.shape[:2]
    left, top, right, bottom = PLAYER_LIST_ROI
    scale_x = width / 1280
    scale_y = height / 752
    roi = screen[
        int(top * scale_y):int(bottom * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    reduced = cv2.resize(gray, (32, 24), interpolation=cv2.INTER_AREA)
    return hashlib.sha1(reduced.tobytes()).digest()


def _upgrade_rate_is_full(screen):
    height, width = screen.shape[:2]
    left, top, right, bottom = UPGRADE_RATE_ROI
    scale_x = width / 1280
    scale_y = height / 752
    roi = screen[
        int(top * scale_y):int(bottom * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return False

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    slot_width = gray.shape[1] / MAX_UPGRADE_PLAYERS
    filled_slots = 0
    for index in range(MAX_UPGRADE_PLAYERS):
        start = int(index * slot_width)
        end = int((index + 1) * slot_width)
        slot = gray[:, start:end]
        if slot.size and float((slot >= 105).mean()) >= 0.12:
            filled_slots += 1
    return filled_slots >= MAX_UPGRADE_PLAYERS


def _upgrade_button_is_green(screen):
    height, width = screen.shape[:2]
    left, top, right, bottom = UPGRADE_CONFIRM_ROI
    scale_x = width / 1280
    scale_y = height / 752
    roi = screen[
        int(top * scale_y):int(bottom * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return False

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    green_pixels = cv2.inRange(
        hsv,
        np.array([35, 100, 100], dtype=np.uint8),
        np.array([90, 255, 255], dtype=np.uint8),
    )
    return float(np.count_nonzero(green_pixels)) / green_pixels.size >= 0.20


def _wait_for_upgrade_button(hwnd, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _upgrade_stop_requested():
            return False
        if _upgrade_button_is_green(capture_fco(hwnd)):
            return True
        _upgrade_sleep(0.15)
    return False


def _skip_prompt_is_visible(screen):
    height, width = screen.shape[:2]
    left, top, right, bottom = SKIP_PROMPT_ROI
    scale_x = width / 1280
    scale_y = height / 752
    roi = screen[
        int(top * scale_y):int(bottom * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return False

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(
        gray,
        None,
        fx=4,
        fy=4,
        interpolation=cv2.INTER_CUBIC,
    )
    for variant in (
        enlarged,
        cv2.threshold(
            enlarged,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )[1],
    ):
        text = pytesseract.image_to_string(
            variant,
            config="--psm 7",
            lang="eng",
        ).lower()
        normalized = unicodedata.normalize("NFKD", text)
        normalized = "".join(
            character
            for character in normalized
            if not unicodedata.combining(character)
        )
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        if "bo qua" in normalized:
            return True
    return False


def _wait_for_skip_prompt(hwnd, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _upgrade_stop_requested():
            return False
        if _skip_prompt_is_visible(capture_fco(hwnd)):
            return True
        _upgrade_sleep(0.2)
    return False


def _wait_for_skip_prompt_gone(hwnd, timeout=10.0):
    """Đợi overlay 'Bỏ qua' biến mất sau khi gửi Space.
    Nếu vẫn còn hiển thị sau 1.5 giây, gửi lại phím Space một lần.
    """
    deadline = time.time() + timeout
    resent = False
    resent_at = None
    while time.time() < deadline:
        if _upgrade_stop_requested():
            return False
        screen = capture_fco(hwnd)
        if not _skip_prompt_is_visible(screen):
            if resent_at is None or time.time() - resent_at > 0.4:
                return True
        elif not resent:
            _upgrade_sleep(1.5)
            if _skip_prompt_is_visible(capture_fco(hwnd)):
                add_log("␠ Overlay Bỏ qua vẫn còn; gửi lại phím Space.")
                key_press(hwnd, win32con.VK_SPACE)
                resent = True
                resent_at = time.time()
        _upgrade_sleep(0.2)
    return not _skip_prompt_is_visible(capture_fco(hwnd))


def _result_next_button_is_green(screen):
    return _result_next_button_position(screen) is not None


def _result_next_button_position(screen):
    height, width = screen.shape[:2]
    left, top, right, bottom = RESULT_NEXT_ROI
    scale_x = width / 1280
    scale_y = height / 752
    roi = screen[
        int(top * scale_y):int(bottom * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return None

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    green_pixels = cv2.inRange(
        hsv,
        np.array([38, 120, 150], dtype=np.uint8),
        np.array([85, 255, 255], dtype=np.uint8),
    )
    green_pixels = cv2.morphologyEx(
        green_pixels,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), dtype=np.uint8),
    )
    contours, _ = cv2.findContours(
        green_pixels,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    contour_area = cv2.contourArea(contour)
    roi_area = roi.shape[0] * roi.shape[1]
    if contour_area < 450 or contour_area / roi_area < 0.06:
        return None

    contour_x, contour_y, contour_width, contour_height = cv2.boundingRect(
        contour
    )
    aspect_ratio = contour_width / max(contour_height, 1)
    if (
        contour_width < 80
        or contour_width > 220
        or contour_height < 16
        or contour_height > 60
        or aspect_ratio < 2.0
        or aspect_ratio > 9.0
    ):
        return None

    center_x = left + (contour_x + contour_width / 2) / scale_x
    center_y = top + (contour_y + contour_height / 2) / scale_y
    if not (985 <= center_x <= 1145 and 688 <= center_y <= 740):
        return None
    return int(center_x), int(center_y)


def _plate_crop(gray):
    """Tim vung plate sang lon o trung tam va cat sat de loai nen."""
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    h, w = gray.shape
    best = None
    for i in range(1, count):
        x, y, ww, hh, area = stats[i]
        cx, cy = x + ww / 2, y + hh / 2
        if w * 0.25 <= cx <= w * 0.75 and h * 0.25 <= cy <= h * 0.75:
            if best is None or area > stats[best][4]:
                best = i
    if best is None:
        return gray
    x, y, ww, hh, _ = stats[best]
    return gray[y:y + hh, x:x + ww]


def _read_owned_card_level_ocr_badge(screen):
    height, width = screen.shape[:2]
    left, top, right, bottom = OWNED_CARD_LEVEL_ROI
    scale_x = width / 1280
    scale_y = height / 752
    roi = screen[
        int(top * scale_y):int(bottom * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return None

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    binary = cv2.threshold(
        gray,
        120,
        255,
        cv2.THRESH_BINARY,
    )
    candidates = []
    for scale in (4, 8):
        upscaled = cv2.resize(
            binary[1],
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )
        for padding_color in (0, 255):
            padded = cv2.copyMakeBorder(
                upscaled,
                scale * 2,
                scale * 2,
                scale * 2,
                scale * 2,
                cv2.BORDER_CONSTANT,
                value=padding_color,
            )
            for psm in (10, 8, 7, 13):
                try:
                    text = pytesseract.image_to_string(
                        padded,
                        config=(
                            f"--psm {psm} "
                            "-c tessedit_char_whitelist=0123456789"
                        ),
                        lang="eng",
                    )
                except Exception:
                    return None
                digits = "".join(ch for ch in text if ch.isdigit())
                if len(digits) in (1, 2):
                    value = int(digits)
                    if 1 <= value <= 20:
                        candidates.append(value)
    if not candidates:
        return None
    value, votes = Counter(candidates).most_common(1)[0]
    if votes < 2:
        return None
    return value


def _read_owned_card_level(screen):
    return _read_owned_card_level_ocr_badge(screen)


def _read_owned_card_level_ocr(screen):
    height, width = screen.shape[:2]
    left, top, right, bottom = OWNED_CARD_LEVEL_ROI
    scale_x = width / 1280
    scale_y = height / 752
    roi = screen[
        int(top * scale_y):int(bottom * scale_y),
        int(left * scale_x):int(right * scale_x),
    ]
    if roi.size == 0:
        return None

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(
        gray,
        None,
        fx=8,
        fy=8,
        interpolation=cv2.INTER_CUBIC,
    )
    padded = cv2.copyMakeBorder(
        enlarged,
        24,
        24,
        24,
        24,
        cv2.BORDER_CONSTANT,
        value=0,
    )
    variants = (
        padded,
        cv2.threshold(
            padded,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )[1],
        cv2.threshold(
            padded,
            0,
            255,
            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
        )[1],
        cv2.adaptiveThreshold(
            padded,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            5,
        ),
    )
    candidates = []
    for variant in variants:
        for psm in (10, 13):
            text = pytesseract.image_to_string(
                variant,
                config=(
                    f"--psm {psm} "
                    "-c tessedit_char_whitelist=0123456789"
                ),
                lang="eng",
            )
            for match in re.finditer(r"\d{1,2}", text):
                value = int(match.group(0))
                if 0 <= value <= 20:
                    candidates.append(value)
    if not candidates:
        return None
    return Counter(candidates).most_common(1)[0][0]


RESULT_NEXT_STABLE_READS = 3
RESULT_NEXT_POSITION_TOLERANCE = 6


def _position_close(a, b, tolerance=RESULT_NEXT_POSITION_TOLERANCE):
    if a is None or b is None:
        return False
    return (
        abs(a[0] - b[0]) <= tolerance
        and abs(a[1] - b[1]) <= tolerance
    )


def _wait_for_result_screen(hwnd, timeout=15.0, report_timeout=True):
    deadline = time.time() + timeout
    stable_position = None
    stable_reads = 0
    first_detection_at = None
    debug_saved = False
    while time.time() < deadline:
        if _upgrade_stop_requested():
            return None
        screen = capture_fco(hwnd)
        position = _result_next_button_position(screen)
        if position is not None:
            if first_detection_at is None:
                first_detection_at = time.time()
            if time.time() - first_detection_at < 0.4:
                _upgrade_sleep(0.2)
                continue
            if _position_close(position, stable_position):
                stable_position = (
                    (stable_position[0] + position[0]) // 2,
                    (stable_position[1] + position[1]) // 2,
                )
                stable_reads += 1
            else:
                stable_position = position
                stable_reads = 1
            if stable_reads >= RESULT_NEXT_STABLE_READS:
                add_log(
                    f"✅ Nút Tiếp ổn định tại {stable_position}."
                )
                return screen
        else:
            if report_timeout and not debug_saved:
                debug_saved = True
                _save_result_next_debug(screen)
            stable_position = None
            stable_reads = 0
            first_detection_at = None
        _upgrade_sleep(0.2)
    if report_timeout:
        add_log(
            "❌ Không thấy nút Tiếp màu xanh trong thời gian chờ; "
            "đã lưu ảnh debug vào ocr_debug/ nếu màn hình khác trống."
        )
        _save_result_next_debug(screen)
    return None


def _save_result_next_debug(screen):
    try:
        os.makedirs("ocr_debug", exist_ok=True)
        height, width = screen.shape[:2]
        left, top, right, bottom = RESULT_NEXT_ROI
        scale_x = width / 1280
        scale_y = height / 752
        roi = screen[
            int(top * scale_y):int(bottom * scale_y),
            int(left * scale_x):int(right * scale_x),
        ]
        stamp = time.strftime("%Y%m%d_%H%M%S")
        cv2.imwrite(
            os.path.join("ocr_debug", f"result_next_roi_{stamp}.png"),
            roi,
        )
    except Exception:
        pass


def _save_owned_card_level_debug(screen):
    height, width = screen.shape[:2]
    left, top, right, bottom = OWNED_CARD_LEVEL_ROI
    scale_x = width / 1280
    scale_y = height / 752
    x1 = max(0, min(width, int(left * scale_x)))
    y1 = max(0, min(height, int(top * scale_y)))
    x2 = max(0, min(width, int(right * scale_x)))
    y2 = max(0, min(height, int(bottom * scale_y)))
    roi = screen[y1:y2, x1:x2]
    if roi.size == 0:
        raise ValueError(
            f"Vùng cấp thẻ trống tại pixel ({x1}, {y1}, {x2}, {y2}) "
            f"trên ảnh {width}x{height}."
        )

    os.makedirs("ocr_debug", exist_ok=True)
    stamp = f"{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}"
    full_path = os.path.join(
        "ocr_debug",
        f"owned_card_level_screen_{stamp}.png",
    )
    roi_path = os.path.join(
        "ocr_debug",
        f"owned_card_level_roi_{stamp}.png",
    )
    marked_screen = screen.copy()
    cv2.rectangle(
        marked_screen,
        (x1, y1),
        (x2 - 1, y2 - 1),
        (0, 0, 255),
        2,
    )
    if not cv2.imwrite(full_path, marked_screen):
        raise OSError(f"Không lưu được ảnh debug: {full_path}")
    if not cv2.imwrite(roi_path, roi):
        raise OSError(f"Không lưu được ảnh debug: {roi_path}")
    return full_path, roi_path, (x1, y1, x2, y2), (width, height)


def _wait_for_owned_card_level(hwnd, timeout=5.0):
    deadline = time.time() + timeout
    debug_saved = False
    while time.time() < deadline:
        if _upgrade_stop_requested():
            return None
        screen = capture_fco(hwnd)
        if not debug_saved:
            debug_saved = True
            try:
                full_path, roi_path, pixel_roi, screen_size = (
                    _save_owned_card_level_debug(screen)
                )
                add_log(
                    "🔎 Vùng dò cấp thẻ: "
                    f"tọa độ chuẩn {OWNED_CARD_LEVEL_ROI}, "
                    f"pixel {pixel_roi}, ảnh {screen_size[0]}x{screen_size[1]}. "
                    f"Ảnh khoanh vùng: {full_path}; ảnh crop: {roi_path}."
                )
            except (OSError, ValueError) as exc:
                add_log(f"⚠️ Không lưu được ảnh debug cấp thẻ: {exc}")
        level = _read_owned_card_level(screen)
        if level is not None:
            return level
        _upgrade_sleep(0.2)
    return None


def _wait_for_owned_players_tab(hwnd, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _upgrade_stop_requested():
            return False
        if _owned_players_tab_selected(capture_fco(hwnd)):
            return True
        _upgrade_sleep(0.2)
    return False


def _wait_for_player_list_ready(hwnd, timeout=8.0):
    deadline = time.time() + timeout
    previous_signature = None
    stable_captures = 0
    while time.time() < deadline:
        if _upgrade_stop_requested():
            return False
        signature = _player_list_signature(capture_fco(hwnd))
        if signature is not None and signature == previous_signature:
            stable_captures += 1
            if stable_captures >= 3:
                return True
        else:
            stable_captures = 0
            previous_signature = signature
        _upgrade_sleep(0.25)
    return False


def _select_player_in_stat_range(
    hwnd,
    stat_min,
    stat_max,
    log_upgrade,
):
    selected_count = 0
    selected_players = set()
    previous_list_signature = None
    previous_low_last_row_ovr = None
    repeated_low_ovr_pages = 0

    def switch_to_bulk_purchase(reason):
        log_upgrade(reason)
        log_upgrade("➡️ Đang chuyển sang tab Mua hàng loạt.")
        _upgrade_click(
            hwnd,
            BUY_BULK_TAB_POSITION[0],
            BUY_BULK_TAB_POSITION[1],
        )

    for scroll_index in range(PLAYER_LIST_MAX_SCROLLS + 1):
        if _upgrade_stop_requested():
            return False
        screen = capture_fco(hwnd)
        list_signature = _player_list_signature(screen)
        if (
            previous_list_signature is not None
            and list_signature == previous_list_signature
        ):
            log_upgrade(
                "⚠️ Danh sách chưa thay đổi sau khi scroll; "
                "đã đến cuối danh sách."
            )
            log_upgrade(
                "➡️ Đang chuyển sang tab Mua hàng loạt."
            )
            _upgrade_click(
                hwnd,
                BUY_BULK_TAB_POSITION[0],
                BUY_BULK_TAB_POSITION[1],
            )
            return False

        previous_list_signature = list_signature
        first_row_y = PLAYER_ROW_FIRST_Y
        first_row_is_dark = _row_background_is_dark(screen, first_row_y)
        first_row_ovr = (
            None
            if first_row_is_dark
            else _read_player_ovr(screen, first_row_y)
        )
        last_row_y = PLAYER_ROW_FIRST_Y + (
            PLAYER_ROW_COUNT - 1
        ) * PLAYER_ROW_STEP_Y
        last_row_ovr = _read_player_ovr(screen, last_row_y)
        page_ovr_cache = {
            first_row_y: first_row_ovr,
            last_row_y: last_row_ovr,
        }
        log_upgrade(
            f"🔎 Kiểm tra OVR cuối danh sách (dòng {PLAYER_ROW_COUNT}, "
            f"cuộn {scroll_index}): "
            f"{last_row_ovr if last_row_ovr is not None else 'không đọc được'}."
        )
        # Kiểm tra OVR dòng đầu, nhưng bỏ qua nếu dòng có nền tối
        # (thẻ đặc biệt Đăng ký/khoá, ví dụ T. Hernández 115 OVR).
        if _row_background_is_dark(screen, first_row_y):
            log_upgrade(
                "ℹ️ Dòng đầu có nền tối (thẻ đặc biệt/đăng ký); "
                "bỏ qua kiểm tra OVR dòng này."
            )
        elif first_row_ovr is not None and first_row_ovr > stat_max:
            switch_to_bulk_purchase(
                f"⚠️ OVR đầu danh sách {first_row_ovr} đã lớn hơn MAX "
                f"{stat_max}; không còn cầu thủ phù hợp."
            )
            return False
        if last_row_ovr is not None and last_row_ovr < stat_min:
            if last_row_ovr == previous_low_last_row_ovr:
                repeated_low_ovr_pages += 1
            else:
                repeated_low_ovr_pages = 1
            previous_low_last_row_ovr = last_row_ovr
        else:
            previous_low_last_row_ovr = None
            repeated_low_ovr_pages = 0

        if repeated_low_ovr_pages >= 2:
            log_upgrade(
                f"⚠️ OVR cuối {last_row_ovr} thấp hơn MIN {stat_min} "
                "trên nhiều trang liên tiếp; đã đến cuối danh sách."
            )
            log_upgrade("➡️ Đang chuyển sang tab Mua hàng loạt.")
            _upgrade_click(
                hwnd,
                BUY_BULK_TAB_POSITION[0],
                BUY_BULK_TAB_POSITION[1],
            )
            return False

        if last_row_ovr is not None and last_row_ovr < stat_min:
            if scroll_index >= PLAYER_LIST_MAX_SCROLLS:
                log_upgrade(
                    f"⚠️ Đã đến cuối danh sách nhưng OVR cuối {last_row_ovr} "
                    f"vẫn thấp hơn MIN {stat_min}; đang chuyển sang tab "
                    "Mua hàng loạt."
                )
                _upgrade_click(
                    hwnd,
                    BUY_BULK_TAB_POSITION[0],
                    BUY_BULK_TAB_POSITION[1],
                )
                return False
            log_upgrade(
                f"↕️ OVR cuối {last_row_ovr} vẫn thấp hơn MIN {stat_min}; "
                "cuộn khoảng 10 vị trí để tìm tiếp."
            )
            scroll_client(
                hwnd,
                PLAYER_LIST_SCROLL_POSITION[0],
                PLAYER_LIST_SCROLL_POSITION[1],
                PLAYER_LIST_SCROLL_NOTCHES,
            )
            _upgrade_sleep(1.0)
            continue

        valid_found_on_page = False
        for row_index in range(PLAYER_ROW_COUNT):
            if _upgrade_stop_requested():
                return False
            row_y = PLAYER_ROW_FIRST_Y + row_index * PLAYER_ROW_STEP_Y
            if _row_background_is_dark(screen, row_y):
                log_upgrade(
                    f"⬛ Bỏ qua dòng {row_index + 1} (cuộn {scroll_index}): "
                    "nền thẻ bị tối, không phải thẻ thường."
                )
                continue
            ovr = page_ovr_cache.get(row_y)
            if row_y not in page_ovr_cache:
                ovr = _read_player_ovr(screen, row_y)
                page_ovr_cache[row_y] = ovr
            if _row_background_is_dark(screen, row_y):
                log_upgrade(
                    f"⏭️ Dòng {row_index + 1} (cuộn {scroll_index}): nền tối "
                    "(thẻ đặc biệt); bỏ qua."
                )
                continue
            log_upgrade(
                f"🔎 Dòng {row_index + 1} (cuộn {scroll_index}): OVR "
                f"{ovr if ovr is not None else 'không đọc được'}."
            )
            if ovr is None or not stat_min <= ovr <= stat_max:
                continue

            valid_found_on_page = True

            player_id = _player_identity(screen, row_y)
            if player_id is None:
                log_upgrade(
                    f"⚠️ Bỏ qua dòng {row_index + 1}: không xác định được "
                    "danh tính cầu thủ."
                )
                continue
            if player_id in selected_players:
                log_upgrade(
                    f"↪️ Bỏ qua dòng {row_index + 1}: cầu thủ này đã được chọn."
                )
                continue

            latest_screen = capture_fco(hwnd)
            latest_ovr = _read_player_ovr(latest_screen, row_y)
            if (
                latest_ovr is None
                or latest_ovr != ovr
                or not stat_min <= latest_ovr <= stat_max
            ):
                log_upgrade(
                    f"⚠️ Bỏ qua dòng {row_index + 1}: OVR đọc lại là "
                    f"{latest_ovr if latest_ovr is not None else 'không đọc được'}, "
                    f"không thuộc phạm vi {stat_min}-{stat_max}."
                )
                screen = latest_screen
                continue

            latest_player_id = _player_identity(latest_screen, row_y)
            if latest_player_id != player_id:
                log_upgrade(
                    f"⚠️ Bỏ qua dòng {row_index + 1}: cầu thủ đã thay đổi "
                    "trước khi click."
                )
                screen = latest_screen
                continue

            _upgrade_click(hwnd, PLAYER_ROW_CLICK_X, row_y)
            selected_players.add(player_id)
            selected_count += 1
            log_upgrade(
                f"✅ Đã chọn cầu thủ thứ {selected_count} ở dòng {row_index + 1}, "
                f"OVR {ovr} (phạm vi {stat_min}-{stat_max})."
            )
            _upgrade_sleep(0.3)
            screen = capture_fco(hwnd)
            if _upgrade_rate_is_full(screen):
                log_upgrade("✅ Tỉ lệ nâng cấp đã đủ 5 vạch.")
                return True
            if selected_count >= MAX_UPGRADE_PLAYERS:
                log_upgrade("✅ Đã chọn đủ 5 cầu thủ nâng cấp.")
                return True

        if scroll_index >= PLAYER_LIST_MAX_SCROLLS:
            break

        if not valid_found_on_page:
            log_upgrade(
                f"↕️ Không có cầu thủ hợp lệ ở danh sách hiện tại; "
                f"đang cuộn xuống lần {scroll_index + 1}."
            )
        else:
            log_upgrade(
                f"↕️ Đã quét xong các cầu thủ hợp lệ trong danh sách hiện tại; "
                f"đang cuộn xuống lần {scroll_index + 1}."
            )
        scroll_client(
            hwnd,
            PLAYER_LIST_SCROLL_POSITION[0],
            PLAYER_LIST_SCROLL_POSITION[1],
            PLAYER_LIST_SCROLL_NOTCHES,
        )
        _upgrade_sleep(1.0)

    log_upgrade(
        f"⚠️ Đã chọn {selected_count}/5 cầu thủ; "
        f"không còn cầu thủ phù hợp trong khoảng {stat_min}-{stat_max}."
    )
    log_upgrade(
        "➡️ Đã quét hết danh sách; đang chuyển sang tab Mua hàng loạt "
        f"tại {BUY_BULK_TAB_POSITION[0]}, {BUY_BULK_TAB_POSITION[1]}."
    )
    _upgrade_click(
        hwnd,
        BUY_BULK_TAB_POSITION[0],
        BUY_BULK_TAB_POSITION[1],
    )
    return False


def open_upgrade():
    global upgrade_window, upgrade_layout

    if upgrade_window is not None and upgrade_window.winfo_exists():
        return

    if player_insert_window is not None and player_insert_window.winfo_exists():
        show_auto_buy()

    auto_buy_tab.configure(fg_color="transparent", text_color=MUTED)
    player_insert_tab.configure(fg_color="transparent", text_color=MUTED)
    upgrade_tab.configure(fg_color="transparent", text_color=GREEN)
    auto_buy_indicator.configure(fg_color="transparent")
    player_insert_indicator.configure(fg_color="transparent")
    upgrade_indicator.configure(fg_color=GREEN)

    upgrade_layout = _hide_main_content()
    upgrade_window = ctk.CTkFrame(main, fg_color="transparent", corner_radius=0)
    upgrade_window.pack(fill="both", expand=True)

    title_bar = ctk.CTkFrame(
        upgrade_window,
        fg_color="transparent",
    )
    title_bar.pack(fill="x", padx=18, pady=(12, 5))
    title_bar.grid_columnconfigure(0, weight=1)
    title_bar.grid_columnconfigure(1, weight=1)
    title_bar.grid_columnconfigure(2, weight=1)
    ctk.CTkLabel(
        title_bar,
        text="ĐẬP CẦU THỦ",
        text_color=GREEN,
        font=ctk.CTkFont(size=16, weight="bold"),
    ).grid(row=0, column=1)
    profile_actions = ctk.CTkFrame(
        title_bar,
        fg_color="transparent",
    )
    profile_actions.grid(row=0, column=2, sticky="e")
    stat_config = ctk.CTkFrame(upgrade_window, fg_color=CARD)
    stat_config.pack(fill="x", padx=18, pady=(0, 10))
    for column in range(3):
        stat_config.grid_columnconfigure(column, weight=1)

    ctk.CTkLabel(
        stat_config,
        text="CHỈ SỐ MIN",
        text_color=MUTED,
        font=ctk.CTkFont(size=9, weight="bold"),
    ).grid(row=0, column=0, sticky="w", padx=(12, 6), pady=(8, 0))
    ctk.CTkLabel(
        stat_config,
        text="CHỈ SỐ MAX",
        text_color=MUTED,
        font=ctk.CTkFont(size=9, weight="bold"),
    ).grid(row=0, column=1, sticky="w", padx=(6, 12), pady=(8, 0))
    ctk.CTkLabel(
        stat_config,
        text="MỨC ĐÍCH",
        text_color=MUTED,
        font=ctk.CTkFont(size=9, weight="bold"),
    ).grid(row=0, column=2, sticky="w", padx=(6, 12), pady=(8, 0))

    upgrade_stat_min_entry = ctk.CTkEntry(
        stat_config,
        height=30,
        fg_color=INNER_COLOR,
        border_color="#3B4A55",
        text_color=TEXT,
        font=ctk.CTkFont(size=11, weight="bold"),
    )
    upgrade_stat_min_entry.grid(
        row=1, column=0, sticky="ew", padx=(12, 6), pady=(3, 10)
    )
    upgrade_stat_min_entry.insert(0, "1")

    upgrade_stat_max_entry = ctk.CTkEntry(
        stat_config,
        height=30,
        fg_color=INNER_COLOR,
        border_color="#3B4A55",
        text_color=TEXT,
        font=ctk.CTkFont(size=11, weight="bold"),
    )
    upgrade_stat_max_entry.grid(
        row=1, column=1, sticky="ew", padx=6, pady=(3, 10)
    )
    upgrade_stat_max_entry.insert(0, "999")

    upgrade_target_level_entry = ctk.CTkEntry(
        stat_config,
        height=30,
        fg_color=INNER_COLOR,
        border_color="#3B4A55",
        text_color=TEXT,
        font=ctk.CTkFont(size=11, weight="bold"),
    )
    upgrade_target_level_entry.grid(
        row=1, column=2, sticky="ew", padx=(6, 12), pady=(3, 10)
    )
    upgrade_target_level_entry.insert(0, "5")

    purchase_config = ctk.CTkFrame(upgrade_window, fg_color=CARD)
    purchase_config.pack(fill="x", padx=18, pady=(0, 10))
    purchase_config.grid_columnconfigure(0, weight=1)
    purchase_config.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(
        purchase_config,
        text="SỐ PHÔI CẦN MUA",
        text_color=MUTED,
        font=ctk.CTkFont(size=9, weight="bold"),
    ).grid(row=0, column=0, sticky="w", padx=(12, 6), pady=(8, 0))
    ctk.CTkLabel(
        purchase_config,
        text="GIÁ TỐI ĐA / PHÔI",
        text_color=MUTED,
        font=ctk.CTkFont(size=9, weight="bold"),
    ).grid(row=0, column=1, sticky="w", padx=(6, 12), pady=(8, 0))

    upgrade_purchase_quantity_entry = ctk.CTkEntry(
        purchase_config,
        height=30,
        fg_color=INNER_COLOR,
        border_color="#3B4A55",
        text_color=TEXT,
        font=ctk.CTkFont(size=11, weight="bold"),
    )
    upgrade_purchase_quantity_entry.grid(
        row=1, column=0, sticky="ew", padx=(12, 6), pady=(3, 10)
    )
    upgrade_purchase_quantity_entry.insert(0, "1")

    upgrade_purchase_price_entry = ctk.CTkEntry(
        purchase_config,
        height=30,
        fg_color=INNER_COLOR,
        border_color="#3B4A55",
        text_color=TEXT,
        font=ctk.CTkFont(size=11, weight="bold"),
    )
    upgrade_purchase_price_entry.grid(
        row=1, column=1, sticky="ew", padx=(6, 12), pady=(3, 10)
    )
    upgrade_purchase_price_entry.insert(0, "10,000")

    def format_upgrade_purchase_price(_event=None):
        value = re.sub(
            r"[^\d]",
            "",
            upgrade_purchase_price_entry.get(),
        )
        formatted = f"{int(value):,}" if value else ""
        if upgrade_purchase_price_entry.get() != formatted:
            upgrade_purchase_price_entry.delete(0, "end")
            upgrade_purchase_price_entry.insert(0, formatted)

    upgrade_purchase_price_entry.bind(
        "<FocusOut>",
        format_upgrade_purchase_price,
    )

    purchased_materials_label = ctk.CTkLabel(
        purchase_config,
        text="ĐÃ MUA: 0",
        text_color=GREEN,
        font=ctk.CTkFont(size=10, weight="bold"),
        anchor="w",
    )
    purchased_materials_label.grid(
        row=2,
        column=0,
        columnspan=2,
        sticky="w",
        padx=12,
        pady=(0, 8),
    )

    log_box = ctk.CTkTextbox(upgrade_window, height=100)
    log_box.pack(fill="both", expand=True, padx=18, pady=(0, 8))

    def log_upgrade(message):
        print(message)
        app.after(
            0,
            lambda: (log_box.insert("end", message + "\n"), log_box.see("end")),
        )

    def update_purchased_materials_count(purchased, target):
        app.after(
            0,
            lambda: purchased_materials_label.configure(
                text=f"ĐÃ MUA: {purchased}/{target}"
            ),
        )

    def reset_purchased_materials_count():
        app.after(
            0,
            lambda: purchased_materials_label.configure(
                text="ĐÃ MUA: 0"
            ),
        )

    def stop_upgrade():
        if upgrade_stop_event is not None:
            upgrade_stop_event.set()
        log_upgrade("⛔ Đã yêu cầu dừng đập cầu thủ.")

    def save_upgrade_profile():
        profiles["upgrade"] = {
            "stat_min": upgrade_stat_min_entry.get(),
            "stat_max": upgrade_stat_max_entry.get(),
            "target_level": upgrade_target_level_entry.get(),
            "purchase_quantity": upgrade_purchase_quantity_entry.get(),
            "purchase_price": upgrade_purchase_price_entry.get(),
        }
        if save_profiles():
            log_upgrade("💾 Đã lưu profile Đập cầu thủ.")

    def load_upgrade_profile():
        saved_profile = profiles.get("upgrade")
        if not isinstance(saved_profile, dict):
            log_upgrade("ℹ️ Chưa có profile Đập cầu thủ đã lưu.")
            return

        for entry, key in (
            (upgrade_stat_min_entry, "stat_min"),
            (upgrade_stat_max_entry, "stat_max"),
            (upgrade_target_level_entry, "target_level"),
            (upgrade_purchase_quantity_entry, "purchase_quantity"),
            (upgrade_purchase_price_entry, "purchase_price"),
        ):
            value = saved_profile.get(key)
            if value is None:
                continue
            entry.delete(0, "end")
            entry.insert(0, str(value))

        log_upgrade("📂 Đã tải profile Đập cầu thủ.")

    def start_upgrade():
        global upgrade_stop_event
        if upgrade_stop_event is not None and not upgrade_stop_event.is_set():
            log_upgrade("⚠️ Luồng đập cầu thủ đang chạy.")
            return
        try:
            stat_min = int(upgrade_stat_min_entry.get().strip())
            stat_max = int(upgrade_stat_max_entry.get().strip())
            target_level = int(upgrade_target_level_entry.get().strip())
            purchase_quantity = int(
                upgrade_purchase_quantity_entry.get().strip()
            )
            purchase_price = int(
                re.sub(r"[^\d]", "", upgrade_purchase_price_entry.get())
            )
        except ValueError:
            log_upgrade("❌ Các giá trị cấu hình phải là số nguyên.")
            messagebox.showerror(
                "Cấu hình không hợp lệ",
                "MIN, MAX, các mức thẻ đập và mức đích phải là số nguyên.",
                parent=app,
            )
            return

        if stat_min < 1 or stat_max < 1 or stat_min > stat_max:
            log_upgrade(
                "❌ Chỉ số không hợp lệ: MIN/MAX phải lớn hơn 0 và MIN không "
                "được lớn hơn MAX."
            )
            messagebox.showerror(
                "Cấu hình không hợp lệ",
                (
                    "Chỉ số MIN/MAX phải lớn hơn 0 và "
                    "MIN không được lớn hơn MAX."
                ),
                parent=app,
            )
            return
        if target_level < 1:
            log_upgrade("❌ Mức thẻ cộng phải lớn hơn 0.")
            messagebox.showerror(
                "Cấu hình không hợp lệ",
                "Mức thẻ cộng phải lớn hơn 0.",
                parent=app,
            )
            return
        if purchase_quantity < 1 or purchase_price < 1:
            log_upgrade(
                "❌ Số phôi cần mua và giá tối đa/phôi phải lớn hơn 0."
            )
            messagebox.showerror(
                "Cấu hình không hợp lệ",
                "Số phôi cần mua và giá tối đa/phôi phải lớn hơn 0.",
                parent=app,
            )
            return

        reset_purchased_materials_count()
        hwnd = find_fc_online()
        if hwnd is None:
            log_upgrade("❌ Không tìm thấy FC ONLINE.")
            messagebox.showerror(
                "Không tìm thấy FC ONLINE",
                "Không tìm thấy cửa sổ FC ONLINE.",
                parent=app,
            )
            return
        try:
            detected = _upgrade_screen_detected(hwnd)
        except (OSError, RuntimeError, pytesseract.TesseractError) as exc:
            log_upgrade(f"❌ Không thể kiểm tra màn hình nâng cấp: {exc}")
            messagebox.showerror(
                "Lỗi kiểm tra giao diện",
                f"Không thể kiểm tra giao diện đập cầu thủ:\n{exc}",
                parent=app,
            )
            return
        if not detected:
            log_upgrade(
                "❌ Chưa mở màn hình đập cầu thủ/nâng cấp. "
                "Hãy mở màn hình nâng cấp rồi thử lại."
            )
            messagebox.showerror(
                "Chưa mở giao diện đập cầu thủ",
                (
                    "Không tìm thấy giao diện đập cầu thủ.\n\n"
                    "Hãy mở màn hình 'Nâng cấp cầu thủ' và chọn tab "
                    "'Cầu thủ đang sở hữu', rồi thử lại."
                ),
                parent=app,
            )
            return
        log_upgrade("✅ Đã nhận diện màn hình nâng cấp.")
        log_upgrade(
            f"🎯 Phạm vi chỉ số: {stat_min} - {stat_max}."
        )
        log_upgrade(f"🎯 Mục tiêu mức thẻ cộng: +{target_level}.")
        log_upgrade(
            f"🛒 Cấu hình mua phôi: {purchase_quantity} phôi, "
            f"giá tối đa {purchase_price:,}/phôi."
        )
        upgrade_stop_event = threading.Event()

        def run_upgrade_cycle():
            if _upgrade_stop_requested():
                return False
            reset_purchased_materials_count()
            try:
                sorted_ascending = _sort_ovr_ascending(hwnd, log_upgrade)
            except (OSError, RuntimeError) as exc:
                log_upgrade(f"❌ Không thể sắp xếp cột OVR: {exc}")
                app.after(
                    0,
                    lambda error=exc: messagebox.showerror(
                        "Lỗi sắp xếp OVR",
                        f"Không thể click cột OVR:\n{error}",
                        parent=app,
                    ),
                )
                return
            except (cv2.error, ValueError, TypeError, NameError) as exc:
                log_upgrade(f"❌ Lỗi xử lý ảnh/icon OVR: {exc}")
                return
            if not sorted_ascending:
                log_upgrade("❌ Không chuyển được icon OVR sang tăng dần.")
                app.after(
                    0,
                    lambda: messagebox.showerror(
                        "Lỗi sắp xếp OVR",
                        "Không xác nhận được icon OVR tăng dần sau các lần click.",
                        parent=app,
                    ),
                )
                return
            try:
                selected = _select_player_in_stat_range(
                    hwnd,
                    stat_min,
                    stat_max,
                    log_upgrade,
                )
            except (OSError, RuntimeError, cv2.error, ValueError, TypeError) as exc:
                log_upgrade(f"❌ Lỗi khi quét/chọn cầu thủ: {exc}")
                app.after(
                    0,
                    lambda error=exc: messagebox.showerror(
                        "Lỗi quét cầu thủ",
                        f"Không thể quét hoặc chọn cầu thủ:\n{error}",
                        parent=app,
                    ),
                )
                return
            if not selected:
                if _upgrade_stop_requested():
                    return
                log_upgrade(
                    f"🛒 Đã chuyển sang Mua hàng loạt; bắt đầu mua "
                    f"{purchase_quantity} phôi, mỗi lần nhập số lượng 10, "
                    f"giá tối đa {purchase_price:,}/phôi."
                )
                run_auto_buy(
                    hwnd=hwnd,
                    target_players=purchase_quantity,
                    threshold=0.85,
                    stat_min=stat_min,
                    stat_max=stat_max,
                    max_card_price=purchase_price,
                    quantity=10,
                    stop_event=upgrade_stop_event,
                    log_callback=log_upgrade,
                    player_count_callback=update_purchased_materials_count,
                    notify_on_complete=False,
                    mouse_method="message",
                )
                if _upgrade_stop_requested():
                    return False
                reset_purchased_materials_count()
                log_upgrade(
                    "✅ Đã mua đủ số phôi yêu cầu; đang quay lại tab "
                    f"Cầu thủ đang sở hữu tại "
                    f"{OWNED_PLAYERS_TAB_POSITION[0]}, "
                    f"{OWNED_PLAYERS_TAB_POSITION[1]}."
                )
                _upgrade_click(
                    hwnd,
                    OWNED_PLAYERS_TAB_POSITION[0],
                    OWNED_PLAYERS_TAB_POSITION[1],
                )
                if not _wait_for_owned_players_tab(hwnd):
                    log_upgrade(
                        "❌ Không xác nhận được tab Cầu thủ đang sở hữu "
                        "sau khi mua phôi."
                    )
                    return False
                log_upgrade("⏳ Đang chờ danh sách cầu thủ tải hoàn tất...")
                if not _wait_for_player_list_ready(hwnd):
                    log_upgrade(
                        "❌ Danh sách cầu thủ chưa tải ổn định sau khi mua phôi."
                    )
                    return False
                log_upgrade(
                    "↕️ Đã quay lại tab Cầu thủ đang sở hữu; "
                    "đang scroll lên để tìm phôi vừa mua."
                )
                scroll_client(
                    hwnd,
                    PLAYER_LIST_SCROLL_POSITION[0],
                    PLAYER_LIST_SCROLL_POSITION[1],
                    PURCHASED_PLAYERS_SCROLL_NOTCHES,
                )
                if upgrade_stop_event.wait(1.0):
                    return False
                log_upgrade(
                    "🔁 Đã mua đủ phôi; bắt đầu lại toàn bộ quy trình "
                    "đập cầu thủ."
                )
                return True
            if _upgrade_stop_requested():
                return False
            log_upgrade(
                "➡️ Đã chọn đủ vật liệu; đang bấm Tiếp theo "
                f"tại {UPGRADE_NEXT_POSITION[0]}, {UPGRADE_NEXT_POSITION[1]}."
            )
            _upgrade_click(
                hwnd,
                UPGRADE_NEXT_POSITION[0],
                UPGRADE_NEXT_POSITION[1],
            )
            if _upgrade_stop_requested():
                return False
            log_upgrade("⏳ Đang chờ màn hình nâng cấp hiển thị...")
            if not _wait_for_upgrade_button(hwnd):
                log_upgrade(
                    "❌ Nút Nâng cấp chưa chuyển sang màu xanh trong thời gian chờ."
                )
                return
            log_upgrade(
                "⬆️ Màn hình nâng cấp đã hiển thị; đang bấm Nâng cấp "
                f"tại {UPGRADE_CONFIRM_POSITION[0]}, "
                f"{UPGRADE_CONFIRM_POSITION[1]}."
            )
            _upgrade_click(
                hwnd,
                UPGRADE_CONFIRM_POSITION[0],
                UPGRADE_CONFIRM_POSITION[1],
            )
            if _upgrade_stop_requested():
                return False
            log_upgrade("⏳ Đang chờ hiển thị chữ Bỏ qua...")
            if _wait_for_skip_prompt(hwnd):
                log_upgrade(
                    "␠ Đã thấy chữ Bỏ qua; gửi phím Space để kết thúc nhanh."
                )
                key_press(hwnd, win32con.VK_SPACE)
            else:
                log_upgrade(
                    "⚠️ Không thấy chữ Bỏ qua trong thời gian chờ; "
                    "không gửi phím Space."
                )
                return False

            log_upgrade("⏳ Đang đợi overlay Bỏ qua biến mất sau Space...")
            if not _wait_for_skip_prompt_gone(hwnd):
                log_upgrade(
                    "⚠️ Overlay Bỏ qua vẫn còn; tiếp tục chờ nút Tiếp."
                )

            log_upgrade("⏳ Đang chờ nút Tiếp màu xanh sáng trên màn hình kết quả...")
            result_screen = _wait_for_result_screen(hwnd)
            if result_screen is None:
                log_upgrade("❌ Không thấy nút Tiếp màu xanh trên màn hình kết quả.")
                return False

            result_next_position = _result_next_button_position(result_screen)
            if result_next_position is None:
                log_upgrade(
                    "❌ Không xác định được vị trí nút Tiếp màu xanh sau khi "
                    "đã phát hiện màn hình kết quả."
                )
                return False
            # Chờ màn hình kết quả ổn định hoàn toàn trước khi bấm,
            # tránh trường hợp bấm vào nút khi animation chưa xong.
            log_upgrade("⏳ Chờ màn hình kết quả ổn định trước khi bấm Tiếp...")
            if _upgrade_sleep(1.5):
                return False
            settle_deadline = time.time() + 8.0
            settled = False
            while time.time() < settle_deadline:
                if _upgrade_stop_requested():
                    return False
                verify_screen = capture_fco(hwnd)
                verify_position = _result_next_button_position(verify_screen)
                if verify_position is None:
                    # Nút biến mất (có thể đã bấm trúng hoặc màn hình chuyển);
                    # chờ màn hình kết quả xuất hiện lại nếu cần.
                    log_upgrade(
                        "⏳ Nút Tiếp chưa sẵn sàng; chờ màn hình kết quả..."
                    )
                    refreshed = _wait_for_result_screen(hwnd, timeout=5.0)
                    if refreshed is None:
                        continue
                    new_position = _result_next_button_position(refreshed)
                    if new_position is not None:
                        result_next_position = new_position
                    if _upgrade_sleep(1.0):
                        return False
                    continue
                if _position_close(verify_position, result_next_position):
                    result_next_position = verify_position
                    settled = True
                    break
                result_next_position = verify_position
                if _upgrade_sleep(0.5):
                    return False
            if not settled:
                log_upgrade(
                    "⚠️ Nút Tiếp không ổn định; vẫn thử bấm tại vị trí gần nhất."
                )
            log_upgrade(
                "✅ Nút Tiếp đã ổn định; đang bấm Tiếp tại "
                f"{result_next_position[0]}, {result_next_position[1]}."
            )

            if _upgrade_stop_requested():
                return False
            _upgrade_click(
                hwnd,
                result_next_position[0],
                result_next_position[1],
            )
            if _upgrade_stop_requested():
                return False

            if _upgrade_sleep(0.5):
                return False
            next_result_screen = _wait_for_result_screen(
                hwnd,
                timeout=3.0,
                report_timeout=False,
            )
            if next_result_screen is not None:
                result_next_position = _result_next_button_position(
                    next_result_screen
                )
                if result_next_position is None:
                    log_upgrade(
                        "❌ Nút Tiếp không còn ổn định; dừng để tránh bấm sai."
                    )
                    return False
                log_upgrade(
                    "➡️ Nút Tiếp vẫn còn sau lần bấm đầu; đang bấm Tiếp lần 2 "
                    f"tại {result_next_position[0]}, {result_next_position[1]}."
                )
                _upgrade_click(
                    hwnd,
                    result_next_position[0],
                    result_next_position[1],
                )
                if _upgrade_stop_requested():
                    return False
                if _upgrade_sleep(0.5):
                    return False
                lingering_result_screen = _wait_for_result_screen(
                    hwnd,
                    timeout=2.5,
                    report_timeout=False,
                )
                if lingering_result_screen is not None:
                    log_upgrade(
                        "❌ Nút Tiếp vẫn còn sau hai lần bấm; dừng để tránh "
                        "chọn phôi khi màn hình kết quả chưa đóng."
                    )
                    return False
            else:
                log_upgrade(
                    "✅ Nút Tiếp đã biến mất; tiếp tục kiểm tra cầu thủ "
                    "trong danh sách."
                )

            if _upgrade_sleep(0.5):
                return False
            result_level = _wait_for_owned_card_level(hwnd, timeout=4.0)
            if result_level is None:
                log_upgrade(
                    "⚠️ Không đọc được mức Cấp thẻ trong danh sách; "
                    "tiếp tục quy trình chọn phôi."
                )
            else:
                log_upgrade(
                    f"📈 Mức thẻ trên thẻ cầu thủ: +{result_level}; "
                    f"mục tiêu +{target_level}."
                )
                if result_level >= target_level:
                    log_upgrade(
                        f"✅ Đã đạt hoặc vượt mục tiêu +{target_level}; "
                        "dừng tool."
                    )
                    play_notification(log_upgrade)
                    return False

                log_upgrade(
                    f"🔁 Chưa đạt mục tiêu +{target_level}; chuẩn bị đập lại."
                )
            if upgrade_stop_event.wait(1.0):
                return False
            return True

        def sort_worker():
            cycle = 1
            try:
                log_upgrade(
                    "🔎 Đang kiểm tra cấp thẻ hiện tại trước lần đập đầu..."
                )
                current_level = _wait_for_owned_card_level(
                    hwnd,
                    timeout=5.0,
                )
                if current_level is None:
                    log_upgrade(
                        "❌ Không đọc được cấp thẻ hiện tại; dừng để tránh "
                        "đập khi chưa xác định được mức thẻ."
                    )
                    if upgrade_stop_event is not None:
                        upgrade_stop_event.set()
                    return
                log_upgrade(
                    f"📈 Cấp thẻ hiện tại: +{current_level}; "
                    f"mục tiêu +{target_level}."
                )
                if current_level >= target_level:
                    log_upgrade(
                        f"✅ Đã đạt hoặc vượt mục tiêu +{target_level}; "
                        "dừng tool."
                    )
                    play_notification(log_upgrade)
                    if upgrade_stop_event is not None:
                        upgrade_stop_event.set()
                    return
                log_upgrade(
                    f"🔁 Chưa đạt mục tiêu +{target_level}; bắt đầu đập thẻ."
                )

                while True:
                    if _upgrade_stop_requested():
                        return
                    log_upgrade(f"🔄 Bắt đầu lần đập {cycle}.")
                    try:
                        should_repeat = run_upgrade_cycle()
                    except Exception as exc:
                        log_upgrade(
                            f"❌ Lỗi ngoài dự kiến khi đập cầu thủ: {exc}"
                        )
                        should_repeat = False
                    if not should_repeat:
                        if upgrade_stop_event is not None:
                            upgrade_stop_event.set()
                        log_upgrade(
                            "⛔ TOOL ĐÃ DỪNG (đạt mục tiêu hoặc gặp lỗi)."
                        )
                        return
                    if _upgrade_stop_requested():
                        log_upgrade("⛔ TOOL ĐÃ DỪNG.")
                        return
                    cycle += 1
            except Exception as exc:
                log_upgrade(f"❌ Lỗi ngoài dự kiến khi đập cầu thủ: {exc}")
                if upgrade_stop_event is not None:
                    upgrade_stop_event.set()
            finally:
                app.after(
                    0,
                    lambda: upgrade_start_button.configure(state="normal"),
                )

        upgrade_start_button.configure(state="disabled")
        try:
            threading.Thread(target=sort_worker, daemon=True).start()
        except Exception:
            upgrade_start_button.configure(state="normal")
            raise

    button_row = ctk.CTkFrame(upgrade_window, fg_color="transparent")
    button_row.pack(
        side="bottom",
        fill="x",
        padx=18,
        pady=(0, 12),
    )
    upgrade_start_button = ctk.CTkButton(
        button_row,
        text="▶ START",
        command=start_upgrade,
        fg_color="#16B95D",
        hover_color=GREEN_HOVER,
        width=150,
    )
    upgrade_start_button.pack(side="left", expand=True, padx=(0, 5))
    ctk.CTkButton(
        button_row,
        text="■ STOP",
        command=stop_upgrade,
        fg_color="#313A42",
        hover_color="#3C4750",
        width=100,
    ).pack(side="left", expand=True, padx=5)

    ctk.CTkButton(
        profile_actions,
        text="💾",
        command=save_upgrade_profile,
        width=30,
        height=26,
        fg_color="#313A42",
        hover_color="#3C4750",
        corner_radius=7,
        font=ctk.CTkFont(size=13),
    ).pack(side="left", padx=(0, 4))
    ctk.CTkButton(
        profile_actions,
        text="📂",
        command=load_upgrade_profile,
        width=30,
        height=26,
        fg_color="#313A42",
        hover_color="#3C4750",
        corner_radius=7,
        font=ctk.CTkFont(size=13),
    ).pack(side="left")
    load_upgrade_profile()


def open_player_insert():
    global player_insert_window, player_insert_layout

    if player_insert_window is not None and player_insert_window.winfo_exists():
        return

    if upgrade_window is not None and upgrade_window.winfo_exists():
        show_auto_buy()

    auto_buy_tab.configure(
        fg_color="transparent",
        text_color=MUTED,
    )
    player_insert_tab.configure(
        fg_color="transparent",
        text_color=GREEN,
    )
    auto_buy_indicator.configure(fg_color="transparent")
    player_insert_indicator.configure(fg_color=GREEN)

    player_insert_layout = []
    for widget in main.winfo_children():
        if widget is topbar:
            continue
        player_insert_layout.append((widget, widget.pack_info()))
        widget.pack_forget()

    player_insert_window = ctk.CTkFrame(
        main,
        fg_color="transparent",
        corner_radius=0,
    )
    player_insert_window.pack(fill="both", expand=True)

    title_bar = ctk.CTkFrame(
        player_insert_window,
        fg_color="transparent",
    )
    title_bar.pack(fill="x", padx=18, pady=(12, 4))
    title_bar.grid_columnconfigure(0, weight=1)
    title_bar.grid_columnconfigure(1, weight=1)
    title_bar.grid_columnconfigure(2, weight=1)
    ctk.CTkLabel(
        title_bar,
        text="CHÈN CẦU THỦ THEO GIỜ RESET",
        text_color=GREEN,
        font=ctk.CTkFont(size=16, weight="bold"),
    ).grid(row=0, column=1)
    profile_actions = ctk.CTkFrame(
        title_bar,
        fg_color="transparent",
    )
    profile_actions.grid(row=0, column=2, sticky="e")

    ctk.CTkLabel(
        player_insert_window,
        text="Trong 20 phút đầu giờ, chỉ xử lý 12 giây đầu mỗi phút.",
        text_color=MUTED,
        font=ctk.CTkFont(size=9),
    ).pack(pady=(0, 10))

    header = ctk.CTkFrame(player_insert_window, fg_color="transparent")
    header.pack(fill="x", padx=18)
    ctk.CTkLabel(header, text="THỨ TỰ", width=55, text_color=MUTED).pack(side="left")
    ctk.CTkLabel(header, text="VỊ TRÍ", width=90, text_color=MUTED).pack(side="left")
    ctk.CTkLabel(header, text="RESET", width=90, text_color=MUTED).pack(side="left")
    ctk.CTkLabel(header, text="SL", width=45, text_color=MUTED).pack(side="left")

    rows_frame = ctk.CTkFrame(player_insert_window, fg_color=CARD)
    rows_frame.pack(fill="both", expand=False, padx=12)
    row_controls = []
    row_widgets = []

    def limit_entry_length(entry, maximum):
        value = entry.get()
        if len(value) > maximum:
            entry.delete(maximum, "end")

    for row_number in range(1, 11):
        row = ctk.CTkFrame(rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=1)
        row_label = ctk.CTkLabel(row, text=str(row_number), width=55)
        row_label.pack(side="left")
        position = ctk.CTkEntry(
            row,
            width=90,
            height=26,
            placeholder_text="1-10",
        )
        position.pack(side="left", padx=(0, 8))
        position.bind(
            "<KeyRelease>",
            lambda _event, field=position: limit_entry_length(field, 2),
        )
        reset = ctk.CTkEntry(row, width=90, height=26)
        reset.pack(side="left", padx=(0, 8))
        reset.bind(
            "<KeyRelease>",
            lambda _event, field=reset: limit_entry_length(field, 3),
        )
        quantity = ctk.CTkEntry(
            row,
            width=45,
            height=26,
            placeholder_text="1",
        )
        quantity.pack(side="left")
        quantity.bind(
            "<KeyRelease>",
            lambda _event, field=quantity: limit_entry_length(field, 2),
        )
        row_controls.append((position, reset, quantity))
        row_widgets.append((row, row_label))

    log_box = ctk.CTkTextbox(player_insert_window, height=100)
    log_box.pack(fill="both", expand=True, padx=18, pady=(8, 8))

    def log_player(message):
        print(message)
        app.after(0, lambda: (log_box.insert("end", message + "\n"), log_box.see("end")))

    def save_player_insert_profile():
        profile_rows = []
        for position_entry, reset_entry, quantity_entry in row_controls:
            profile_rows.append({
                "position": position_entry.get(),
                "reset": reset_entry.get(),
                "quantity": quantity_entry.get(),
            })
        profiles["player_insert"] = profile_rows
        if save_profiles():
            log_player("💾 Đã lưu profile Player Insert.")

    def load_player_insert_profile():
        profile_rows = profiles.get("player_insert")
        if not isinstance(profile_rows, list):
            log_player("ℹ️ Chưa có profile Player Insert đã lưu.")
            return
        for controls, saved_row in zip(row_controls, profile_rows):
            if not isinstance(saved_row, dict):
                continue
            for field, key in zip(
                controls,
                ("position", "reset", "quantity"),
            ):
                field.delete(0, "end")
                field.insert(0, str(saved_row.get(key, "")))
        log_player("📂 Đã tải profile Player Insert.")

    def stop_player_insert():
        if player_insert_stop_event is not None:
            player_insert_stop_event.set()

    def start_player_insert():
        global player_insert_thread, player_insert_stop_event
        if player_insert_thread is not None and player_insert_thread.is_alive():
            log_player("⚠️ Player Insert đang chạy.")
            return
        hwnd = find_fc_online()
        if hwnd is None:
            log_player("❌ Không tìm thấy FC ONLINE.")
            return

        entries = []
        try:
            for row_number, (
                position_entry,
                reset_entry,
                quantity_entry,
            ) in enumerate(row_controls, 1):
                position_text = position_entry.get().strip()
                reset_text = reset_entry.get().strip().lower()
                quantity_text = quantity_entry.get().strip()
                if not position_text and not reset_text and not quantity_text:
                    continue
                try:
                    position = int(position_text)
                except ValueError as exc:
                    raise ValueError(
                        f"Dòng {row_number}: Vị trí phải là số từ 1 đến 10."
                    ) from exc
                if position < 1 or position > 10:
                    raise ValueError("Vị trí phải từ 1 đến 10.")
                reset_text = _reset_matches_hour_for_ui(reset_text)
                try:
                    quantity = int(quantity_text or "1")
                except ValueError as exc:
                    raise ValueError(
                        f"Dòng {row_number}: Số lượng phải là số nguyên."
                    ) from exc
                if quantity < 1:
                    raise ValueError("Số lượng phải từ 1 trở lên.")
                entries.append((
                    row_number,
                    {
                        "position": position,
                        "reset": reset_text,
                        "quantity": quantity,
                    },
                ))
        except ValueError as exc:
            log_player(f"❌ Cấu hình không hợp lệ: {exc}")
            return

        if not entries:
            log_player("❌ Cần nhập ít nhất một cầu thủ.")
            return

        sorted_entries = sort_entries_by_reset(entries)
        for display_row, (_source_row, entry) in enumerate(sorted_entries, 1):
            position_entry, reset_entry, quantity_entry = row_controls[display_row - 1]
            position_entry.delete(0, "end")
            position_entry.insert(0, str(entry["position"]))
            reset_entry.delete(0, "end")
            reset_entry.insert(0, entry["reset"])
            quantity_entry.delete(0, "end")
            quantity_entry.insert(0, str(entry["quantity"]))

        for display_row in range(len(sorted_entries) + 1, len(row_controls) + 1):
            for entry in row_controls[display_row - 1]:
                entry.delete(0, "end")

        entries = [
            (display_row, entry)
            for display_row, (_source_row, entry) in enumerate(sorted_entries, 1)
        ]

        player_insert_stop_event = threading.Event()
        log_box.delete("1.0", "end")

        start_button.configure(state="disabled")
        stop_button.configure(state="normal")

        def player_insert_worker():
            try:
                run_player_insert(
                    hwnd,
                    entries,
                    player_insert_stop_event,
                    log_player,
                )
            finally:
                app.after(
                    0,
                    lambda: (
                        start_button.configure(state="normal"),
                        stop_button.configure(state="disabled"),
                    ),
                )

        player_insert_thread = threading.Thread(
            target=player_insert_worker,
            daemon=True,
        )
        player_insert_thread.start()

    ctk.CTkButton(
        profile_actions,
        text="💾",
        command=save_player_insert_profile,
        width=30,
        height=26,
        fg_color="#313A42",
        hover_color="#3C4750",
        corner_radius=7,
        font=ctk.CTkFont(size=13),
    ).pack(side="left", padx=(0, 4))
    ctk.CTkButton(
        profile_actions,
        text="📂",
        command=load_player_insert_profile,
        width=30,
        height=26,
        fg_color="#313A42",
        hover_color="#3C4750",
        corner_radius=7,
        font=ctk.CTkFont(size=13),
    ).pack(side="left")

    button_row = ctk.CTkFrame(
        player_insert_window,
        fg_color="transparent",
    )
    button_row.pack(fill="x", padx=18, pady=(0, 16))
    start_button = ctk.CTkButton(
        button_row, text="▶ START", command=start_player_insert,
        fg_color="#16B95D", hover_color=GREEN_HOVER, width=150
    )
    start_button.pack(side="left", expand=True, padx=(0, 5))
    stop_button = ctk.CTkButton(
        button_row, text="■ STOP", command=stop_player_insert,
        fg_color="#313A42", hover_color="#3C4750", width=100,
        state="disabled",
    )
    stop_button.pack(side="left", expand=True, padx=5)

    load_player_insert_profile()


def show_auto_buy():
    global player_insert_window, player_insert_layout, upgrade_window, upgrade_layout

    if (
        (player_insert_window is None or not player_insert_window.winfo_exists())
        and (upgrade_window is None or not upgrade_window.winfo_exists())
    ):
        return

    auto_buy_tab.configure(
        fg_color="transparent",
        text_color=GREEN,
    )
    player_insert_tab.configure(
        fg_color="transparent",
        text_color=MUTED,
    )
    upgrade_tab.configure(
        fg_color="transparent",
        text_color=MUTED,
    )
    auto_buy_indicator.configure(fg_color=GREEN)
    player_insert_indicator.configure(fg_color="transparent")
    upgrade_indicator.configure(fg_color="transparent")

    if player_insert_stop_event is not None:
        player_insert_stop_event.set()

    if player_insert_window is not None and player_insert_window.winfo_exists():
        player_insert_window.destroy()
        player_insert_window = None
    if upgrade_window is not None and upgrade_window.winfo_exists():
        upgrade_window.destroy()
        upgrade_window = None

    if player_insert_layout is not None:
        _restore_main_content(player_insert_layout)
        player_insert_layout = None
    if upgrade_layout is not None:
        _restore_main_content(upgrade_layout)
        upgrade_layout = None

def _reset_matches_hour_for_ui(reset_code):
    return normalize_reset_code(reset_code)


# ============================================================
# SIDEBAR FOOTER
# ============================================================

ctk.CTkLabel(
    sidebar,
    text="v1.0.0",
    text_color="#58656F",
    font=ctk.CTkFont(
        size=8,
        weight="bold"
    )
).pack(
    side="bottom",
    pady=(0, 5)
)


# ============================================================
# MAIN PANEL
# ============================================================

main = ctk.CTkFrame(
    root,
    fg_color="transparent",
    corner_radius=0
)

main.grid(
    row=0,
    column=0,
    sticky="nsew",
    padx=10,
    pady=8
)


# ============================================================
# TOP BAR
# ============================================================

topbar = ctk.CTkFrame(
    main,
    fg_color="transparent",
    height=48
)

topbar.pack(
    fill="x"
)

topbar.pack_propagate(
    False
)


auto_buy_tab_wrap = ctk.CTkFrame(
    topbar,
    width=105,
    height=34,
    fg_color="transparent",
    corner_radius=0,
)
auto_buy_tab_wrap.pack(side="left", padx=(20, 0), pady=7)
auto_buy_tab_wrap.pack_propagate(False)

auto_buy_tab = ctk.CTkButton(
    auto_buy_tab_wrap,
    text="Mua phôi",
    command=show_auto_buy,
    width=105,
    height=29,
    fg_color="transparent",
    hover_color="#1B1F24",
    text_color=GREEN,
    corner_radius=0,
    border_width=0,
)
auto_buy_tab.pack(fill="x")
auto_buy_indicator = ctk.CTkFrame(
    auto_buy_tab_wrap,
    height=2,
    fg_color=GREEN,
    corner_radius=0,
)
auto_buy_indicator.pack(fill="x", padx=8, side="bottom")

player_insert_tab_wrap = ctk.CTkFrame(
    topbar,
    width=115,
    height=34,
    fg_color="transparent",
    corner_radius=0,
)
player_insert_tab_wrap.pack(side="left", padx=0, pady=7)
player_insert_tab_wrap.pack_propagate(False)

player_insert_tab = ctk.CTkButton(
    player_insert_tab_wrap,
    text="Chèn cầu thủ",
    command=open_player_insert,
    width=115,
    height=29,
    fg_color="transparent",
    hover_color="#1B1F24",
    text_color=MUTED,
    corner_radius=0,
    border_width=0,
)
player_insert_tab.pack(fill="x")
player_insert_indicator = ctk.CTkFrame(
    player_insert_tab_wrap,
    height=2,
    fg_color="transparent",
    corner_radius=0,
)
player_insert_indicator.pack(fill="x", padx=8, side="bottom")

upgrade_tab_wrap = ctk.CTkFrame(
    topbar,
    width=110,
    height=34,
    fg_color="transparent",
    corner_radius=0,
)
upgrade_tab_wrap.pack(side="left", padx=0, pady=7)
upgrade_tab_wrap.pack_propagate(False)

upgrade_tab = ctk.CTkButton(
    upgrade_tab_wrap,
    text="Đập cầu thủ",
    command=open_upgrade,
    width=110,
    height=29,
    fg_color="transparent",
    hover_color="#1B1F24",
    text_color=MUTED,
    corner_radius=0,
    border_width=0,
)
upgrade_tab.pack(fill="x")
upgrade_indicator = ctk.CTkFrame(
    upgrade_tab_wrap,
    height=2,
    fg_color="transparent",
    corner_radius=0,
)
upgrade_indicator.pack(fill="x", padx=8, side="bottom")


ready_badge = ctk.CTkFrame(
    topbar,
    fg_color="#0C2318",
    border_width=1,
    border_color="#1B6740",
    corner_radius=8,
    width=78,
    height=26
)

ready_badge.pack(
    side="right",
    pady=11
)

ready_badge.pack_propagate(
    False
)


ctk.CTkLabel(
    ready_badge,
    text="● READY",
    text_color=GREEN,
    font=ctk.CTkFont(
        size=8,
        weight="bold"
    )
).pack(
    expand=True
)


# ============================================================
# CONTROL CARDS
# ============================================================

cards = ctk.CTkFrame(
    main,
    fg_color="transparent"
)

cards.pack(
    fill="x",
    pady=(0, 7)
)

cards.grid_columnconfigure(
    0,
    weight=1
)

cards.grid_columnconfigure(
    1,
    weight=1
)


# ============================================================
# INPUT + PROFILE CARD
# ============================================================

input_card = ctk.CTkFrame(
    cards,
    fg_color=CARD,
    border_width=1,
    border_color=BORDER,
    corner_radius=16,
    height=218
)

input_card.grid(
    row=0,
    column=0,
    columnspan=2,
    sticky="ew"
)

input_card.grid_propagate(False)


input_content = ctk.CTkFrame(
    input_card,
    fg_color="transparent"
)

input_content.pack(
    fill="both",
    expand=True,
    padx=14,
    pady=10
)


# ============================================================
# PROFILE ROW
# ============================================================

profile_row = ctk.CTkFrame(
    input_content,
    fg_color="transparent",
    height=30
)

profile_row.pack(
    fill="x"
)

profile_row.pack_propagate(False)


ctk.CTkLabel(
    profile_row,
    text="PROFILE",
    text_color=MUTED,
    font=ctk.CTkFont(
        size=8,
        weight="bold"
    )
).pack(
    side="left",
    padx=(0, 7)
)


profile_combo = ctk.CTkComboBox(
    profile_row,
    width=120,
    height=27,
    fg_color=INNER_COLOR,
    border_color="#3B4A55",
    button_color="#18242C",
    button_hover_color="#22323C",
    text_color=TEXT,
    font=ctk.CTkFont(
        size=9
    ),
    values=[],
    command=on_profile_selected
)

profile_combo.pack(
    side="left"
)


profile_name_entry = ctk.CTkEntry(
    profile_row,
    width=100,
    height=27,
    fg_color=INNER_COLOR,
    border_color="#3B4A55",
    corner_radius=8,
    text_color=TEXT,
    placeholder_text="Tên profile",
    font=ctk.CTkFont(
        size=9
    )
)

profile_name_entry.pack(
    side="left",
    padx=(6, 5)
)


save_profile_button = ctk.CTkButton(
    profile_row,
    text="Lưu",
    width=42,
    height=27,
    corner_radius=8,
    fg_color="#153F29",
    hover_color="#1A5B3A",
    text_color=GREEN,
    font=ctk.CTkFont(
        size=9,
        weight="bold"
    ),
    command=save_current_profile
)

save_profile_button.pack(
    side="left"
)


delete_profile_button = ctk.CTkButton(
    profile_row,
    text="Xóa",
    width=42,
    height=27,
    corner_radius=8,
    fg_color="#322020",
    hover_color="#4D2B2B",
    text_color=DANGER,
    font=ctk.CTkFont(
        size=9,
        weight="bold"
    ),
    command=delete_current_profile
)

delete_profile_button.pack(
    side="left",
    padx=(5, 0)
)


# ============================================================
# TARGET + QUANTITY ROW
# ============================================================

target_quantity_row = ctk.CTkFrame(
    input_content,
    fg_color="transparent",
    height=31
)

target_quantity_row.pack(
    fill="x",
    pady=(4, 0)
)

target_quantity_row.pack_propagate(False)


ctk.CTkLabel(
    target_quantity_row,
    text="🛒  CẦU THỦ",
    text_color=TEXT,
    font=ctk.CTkFont(
        size=9,
        weight="bold"
    )
).pack(
    side="left"
)


target_entry = ctk.CTkEntry(
    target_quantity_row,
    width=70,
    height=29,
    fg_color=INNER_COLOR,
    border_color="#3B4A55",
    border_width=1,
    corner_radius=8,
    text_color=TEXT,
    font=ctk.CTkFont(
        size=11,
        weight="bold"
    )
)

target_entry.pack(
    side="left",
    padx=(7, 18)
)

target_entry.insert(
    0,
    "10"
)


ctk.CTkLabel(
    target_quantity_row,
    text="SỐ LƯỢNG / LẦN",
    text_color=MUTED,
    font=ctk.CTkFont(
        size=8,
        weight="bold"
    )
).pack(
    side="left"
)


quantity_entry = ctk.CTkEntry(
    target_quantity_row,
    width=70,
    height=29,
    fg_color=INNER_COLOR,
    border_color="#3B4A55",
    border_width=1,
    corner_radius=8,
    text_color=TEXT,
    font=ctk.CTkFont(
        size=11,
        weight="bold"
    )
)

quantity_entry.pack(
    side="left",
    padx=(7, 0)
)

quantity_entry.insert(
    0,
    "1"
)


def limit_quantity(event=None):
    value = quantity_entry.get().strip()
    if not value.isdigit():
        return

    if int(value) > 10:
        quantity_entry.delete(0, "end")
        quantity_entry.insert(0, "10")
        add_log(
            "⚠️ Số lượng tối đa là 10; đã tự động đổi về 10."
        )


quantity_entry.bind(
    "<KeyRelease>",
    limit_quantity,
    add="+"
)
quantity_entry.bind(
    "<FocusOut>",
    limit_quantity,
    add="+"
)


def enforce_quantity_limit():
    limit_quantity()
    app.after(100, enforce_quantity_limit)


app.after(100, enforce_quantity_limit)


# ============================================================
# FILTERS
# ============================================================

filters_row = ctk.CTkFrame(
    input_content,
    fg_color="transparent"
)

filters_row.pack(
    fill="x",
    pady=(7, 0)
)

filters_row.grid_columnconfigure(
    0,
    weight=1
)

filters_row.grid_columnconfigure(
    1,
    weight=1
)

filters_row.grid_columnconfigure(
    2,
    weight=1
)


def create_filter_field(
    parent,
    column,
    title,
    default
):
    box = ctk.CTkFrame(
        parent,
        fg_color="transparent"
    )

    box.grid(
        row=0,
        column=column,
        sticky="ew",
        padx=(
            0 if column == 0 else 4,
            4 if column < 2 else 0
        )
    )

    ctk.CTkLabel(
        box,
        text=title,
        text_color=MUTED,
        font=ctk.CTkFont(
            size=7,
            weight="bold"
        )
    ).pack(
        anchor="w"
    )

    entry = ctk.CTkEntry(
        box,
        height=29,
        fg_color=INNER_COLOR,
        border_color="#3B4A55",
        border_width=1,
        corner_radius=8,
        text_color=TEXT,
        font=ctk.CTkFont(
            size=10,
            weight="bold"
        )
    )

    entry.pack(
        fill="x",
        pady=(3, 0)
    )

    entry.insert(
        0,
        default
    )

    return entry


stat_min_entry = create_filter_field(
    filters_row,
    0,
    "CHỈ SỐ MIN",
    "97"
)

stat_max_entry = create_filter_field(
    filters_row,
    1,
    "CHỈ SỐ MAX",
    "99"
)

max_price_entry = create_filter_field(
    filters_row,
    2,
    "GIÁ TỐI ĐA / THẺ",
    "10,000"
)

max_price_entry.bind(
    "<FocusOut>",
    format_price_entry
)


ctk.CTkLabel(
    input_content,
    text="Profile chỉ lưu MIN / MAX / giá thẻ • Số lượng không lưu",
    text_color="#63717B",
    font=ctk.CTkFont(
        size=7
    )
).pack(
    anchor="w",
    pady=(7, 0)
)


# ============================================================
# STATUS CARD
# ============================================================

status_card = ctk.CTkFrame(
    cards,
    fg_color=CARD,
    border_width=1,
    border_color=BORDER,
    corner_radius=16,
    height=118
)

status_card.grid(
    row=1,
    column=0,
    columnspan=2,
    sticky="ew",
    pady=(7, 0)
)

status_card.grid_propagate(
    False
)


status_top = ctk.CTkFrame(
    status_card,
    fg_color="transparent"
)

status_top.pack(
    fill="x",
    padx=20,
    pady=(9, 2)
)


status_dot = ctk.CTkLabel(
    status_top,
    text="●",
    text_color=GREEN,
    font=ctk.CTkFont(
        size=16
    )
)

status_dot.pack(
    side="left"
)


status_label = ctk.CTkLabel(
    status_top,
    text="⚪ BOT: READY",
    text_color=TEXT,
    font=ctk.CTkFont(
        size=12,
        weight="bold"
    )
)

status_label.pack(
    side="left",
    padx=(7, 0)
)


status_hint = ctk.CTkLabel(
    status_card,
    text="Sẵn sàng hoạt động",
    text_color=MUTED,
    font=ctk.CTkFont(
        size=10
    )
)

status_hint.pack(
    anchor="w",
    padx=20
)


metric_row = ctk.CTkFrame(
    status_card,
    fg_color="transparent"
)

metric_row.pack(
    fill="x",
    padx=20,
    pady=(8, 0)
)


# ============================================================
# PLAYER COUNT
# ============================================================

player_box = ctk.CTkFrame(
    metric_row,
    fg_color="transparent"
)

player_box.pack(
    side="left",
    fill="x",
    expand=True
)


ctk.CTkLabel(
    player_box,
    text="ĐÃ MUA",
    text_color=MUTED,
    font=ctk.CTkFont(
        size=9,
        weight="bold"
    )
).pack(
    anchor="w"
)


player_count_value = ctk.CTkLabel(
    player_box,
    text="0 / 10",
    text_color=GREEN,
    font=ctk.CTkFont(
        size=21,
        weight="bold"
    )
)

player_count_value.pack(
    anchor="w"
)


# ============================================================
# TIMER
# ============================================================

timer_box = ctk.CTkFrame(
    metric_row,
    fg_color="transparent"
)

timer_box.pack(
    side="right"
)


ctk.CTkLabel(
    timer_box,
    text="THỜI GIAN",
    text_color=MUTED,
    font=ctk.CTkFont(
        size=9,
        weight="bold"
    )
).pack(
    anchor="e"
)


timer_value = ctk.CTkLabel(
    timer_box,
    text="00:00:00",
    text_color=TEXT,
    font=ctk.CTkFont(
        size=16,
        weight="bold"
    )
)

timer_value.pack(
    anchor="e"
)


# ============================================================
# PROGRESS
# ============================================================

progress_bar = ctk.CTkProgressBar(
    main,
    height=5,
    corner_radius=3,
    fg_color="#1B252C",
    progress_color=GREEN
)

progress_bar.pack(
    fill="x",
    pady=(0, 9)
)

progress_bar.set(
    0
)


# ============================================================
# LOG CARD
# ============================================================

log_card = ctk.CTkFrame(
    main,
    fg_color=CARD,
    border_width=1,
    border_color=BORDER,
    corner_radius=16,
    height=170
)

log_card.pack(
    fill="x"
)

log_card.pack_propagate(
    False
)


log_header = ctk.CTkFrame(
    log_card,
    fg_color="transparent",
    height=40
)

log_header.pack(
    fill="x",
    padx=18,
    pady=(7, 0)
)

log_header.pack_propagate(
    False
)


ctk.CTkLabel(
    log_header,
    text="▤  NHẬT KÝ HOẠT ĐỘNG",
    text_color=TEXT,
    font=ctk.CTkFont(
        size=12,
        weight="bold"
    )
).pack(
    side="left",
    anchor="center"
)


clear_button = ctk.CTkButton(
    log_header,
    text="Xóa log",
    width=75,
    height=28,
    corner_radius=8,
    fg_color="transparent",
    hover_color="#182229",
    text_color=MUTED,
    font=ctk.CTkFont(
        size=10
    ),
    command=clear_log
)

clear_button.pack(
    side="right"
)


status_text = ctk.CTkTextbox(
    log_card,
    fg_color=INNER_COLOR,
    border_width=1,
    border_color="#26353E",
    corner_radius=11,
    text_color="#D9E0E4",
    font=ctk.CTkFont(
        family="Consolas",
        size=10
    )
)

status_text.pack(
    fill="both",
    expand=True,
    padx=18,
    pady=(0, 12)
)


status_text.insert(
    "end",
    "FC Online Auto Buy\n"
    "----------------------------------------\n"
    "Status: READY\n"
    "Đã mua: 0 / 10\n"
    "Chỉ số: 117\n"
    "\n"
    "Chưa bắt đầu.\n"
)


# ============================================================
# BUTTONS
# ============================================================

button_frame = ctk.CTkFrame(
    main,
    fg_color="transparent",
    height=58
)

button_frame.pack(
    fill="x",
    pady=(6, 4)
)

button_frame.pack_propagate(
    False
)


button_frame.grid_columnconfigure(
    0,
    weight=1
)

button_frame.grid_columnconfigure(
    1,
    weight=1
)


start_button = ctk.CTkButton(
    button_frame,
    text="▶   START",
    height=44,
    corner_radius=12,
    fg_color="#16B95D",
    hover_color=GREEN_HOVER,
    text_color="#FFFFFF",
    font=ctk.CTkFont(
        size=15,
        weight="bold"
    ),
    command=start_bot
)

start_button.grid(
    row=0,
    column=0,
    sticky="ew",
    padx=(0, 7)
)


stop_button = ctk.CTkButton(
    button_frame,
    text="■   STOP",
    height=44,
    corner_radius=12,
    fg_color="#313A42",
    hover_color="#3C4750",
    text_color="#909AA2",
    font=ctk.CTkFont(
        size=15,
        weight="bold"
    ),
    command=stop_bot,
    state="disabled"
)

stop_button.grid(
    row=0,
    column=1,
    sticky="ew",
    padx=(7, 0)
)


# ============================================================
# FOOTER
# ============================================================

footer = ctk.CTkFrame(
    main,
    fg_color="transparent",
    height=14
)

footer.pack(
    fill="x"
)

footer.pack_propagate(
    False
)


# ============================================================
# LOAD SAVED PROFILES
# ============================================================

load_profiles()
refresh_profile_combo()
select_most_used_profile()


# ============================================================
# BACKGROUND SERVICES
# ============================================================

app.after(
    2000,
    auto_scan_fc_online
)

app.after(
    1000,
    update_timer
)


# ============================================================
# MAIN LOOP
# ============================================================

app.mainloop()