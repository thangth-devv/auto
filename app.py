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

from PIL import Image, ImageTk

from auto_buy import run_auto_buy


# ============================================================
# CONFIG
# ============================================================

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

APP_TITLE = "AUTO BUY FCO"
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


# ============================================================
# GLOBAL STATE
# ============================================================

bot_thread = None
stop_event = None
is_running = False
current_hwnd = None
start_time = None


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

        is_fc_candidate = any(
            keyword in f"{title_upper} {class_upper}"
            for keyword in (
                "FC ONLINE",
                "EA SPORTS FC",
                "FIFA"
            )
        )

        if title_upper == "" or is_fc_candidate:
            candidates.append(
                (
                    1 if is_fc_candidate else 0,
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
    1,
    weight=1
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

def sidebar_item(
    icon,
    active=False
):

    frame = ctk.CTkFrame(
        sidebar,
        width=44,
        height=40,
        corner_radius=14,
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

    ctk.CTkLabel(
        frame,
        text=icon,
        text_color=(
            GREEN
            if active
            else MUTED
        ),
        font=ctk.CTkFont(
            size=14,
            weight="bold"
        )
    ).pack(
        expand=True
    )

    return frame


sidebar_item(
    "⌂",
    active=True
)

sidebar_item(
    "⚙"
)

sidebar_item(
    "ⓘ"
)


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
    column=1,
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


ctk.CTkLabel(
    topbar,
    text="AUTO BUY",
    text_color=GREEN,
    font=ctk.CTkFont(
        size=18,
        weight="bold"
    )
).pack(
    side="left",
    padx=(12, 0),
    pady=(5, 0)
)


ready_badge = ctk.CTkFrame(
    topbar,
    fg_color="#0C2318",
    border_width=1,
    border_color="#1B6740",
    corner_radius=10,
    width=120,
    height=34
)

ready_badge.pack(
    side="right",
    pady=8
)

ready_badge.pack_propagate(
    False
)


ctk.CTkLabel(
    ready_badge,
    text="●  READY",
    text_color=GREEN,
    font=ctk.CTkFont(
        size=10,
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