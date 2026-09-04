# FC Online Auto Buy – Architecture

## 1. Mục tiêu

FC Online Auto Buy là desktop automation tool điều khiển FC Online thông qua:

- CustomTkinter: giao diện người dùng.
- Win32 API: gửi mouse events vào cửa sổ game.
- PIL/ImageGrab + NumPy: chụp màn hình game.
- OpenCV: nhận diện nút bằng template matching.
- Tesseract OCR: đọc số lượng cầu thủ thực tế mua được.
- winsound: phát âm thanh khi đạt đủ số lượng mục tiêu.

Mục tiêu kiến trúc là tách riêng UI, business flow, image detection, OCR, input và notification để code dễ debug, mở rộng và build thành EXE.

---

## 2. High-Level Architecture

```text
                          +----------------------+
                          |      FC ONLINE       |
                          |      Game Client     |
                          +----------+-----------+
                                     |
                              Screen Capture
                                     |
                                     v
+------------------------------------------------------------------+
|                    FC ONLINE AUTO BUY                            |
|                                                                  |
|  +--------------------+                                          |
|  |       UI Layer     |                                          |
|  |   CustomTkinter    |                                          |
|  |                    |                                          |
|  | - Target players   |                                          |
|  | - Start / Stop     |                                          |
|  | - Status           |                                          |
|  | - Progress         |                                          |
|  | - Logs             |                                          |
|  +---------+----------+                                          |
|            |                                                     |
|            v                                                     |
|  +--------------------+                                          |
|  |   Bot Controller   |                                          |
|  |                    |                                          |
|  | - Lifecycle        |                                          |
|  | - Start / Stop     |                                          |
|  | - Thread           |                                          |
|  | - Progress         |                                          |
|  +---------+----------+                                          |
|            |                                                     |
|            v                                                     |
|  +------------------------------------------------------------+  |
|  |                    State Machine                           |  |
|  |                                                            |  |
|  | BUY -> BUY_CONFIRM -> WAIT_RESULT                          |  |
|  |                         |                                  |  |
|  |               +---------+---------+                        |  |
|  |               |                   |                        |  |
|  |             FAILURE             SUCCESS                    |  |
|  |               |                   |                        |  |
|  |               v                   v                        |  |
|  |        FAILURE_CONFIRM         RECEIVE                     |  |
|  |               |                   |                        |  |
|  |               +-> BUY             v                        |  |
|  |                          FINAL_CONFIRM                      |  |
|  |                               |                            |  |
|  |                               v                            |  |
|  |                           OCR_COUNT                        |  |
|  |                               |                            |  |
|  |                               v                            |  |
|  |                         UPDATE_COUNTER                     |  |
|  |                               |                            |  |
|  |                    +----------+-----------+                |  |
|  |                    |                      |                |  |
|  |                 NOT_DONE              COMPLETED            |  |
|  |                    |                      |                |  |
|  |                    v                      v                |  |
|  |                   BUY                NOTIFICATION          |  |
|  +------------------------------------------------------------+  |
|                                                                  |
|  +----------------+  +----------------+  +-------------------+  |
|  | Image Detector |  |   OCR Service  |  | Input Controller  |  |
|  | OpenCV         |  | Tesseract      |  | Win32 API         |  |
|  +----------------+  +----------------+  +-------------------+  |
|                                                                  |
|  +--------------------+                                          |
|  | Notification       |                                          |
|  | winsound + audio   |                                          |
|  +--------------------+                                          |
+------------------------------------------------------------------+
```

---

## 3. State Machine

State Machine là phần trung tâm của bot. Flow không nên phụ thuộc vào việc `sleep` một khoảng cố định rồi giả định game đã chuyển trạng thái.

### States

```text
IDLE
  |
  v
BUY
  |
  v
BUY_CONFIRM
  |
  v
WAIT_RESULT
  |-----------------------------|
  |                             |
  v                             v
FAILURE                       SUCCESS
  |                             |
  v                             v
FAILURE_CONFIRM              RECEIVE
  |                             |
  |                             v
  +---------> BUY           FINAL_CONFIRM
                                |
                                v
                             OCR_COUNT
                                |
                                v
                         UPDATE_COUNTER
                                |
                    +-----------+-----------+
                    |                       |
                    v                       v
                 BUY AGAIN              COMPLETED
                                            |
                                            v
                                      NOTIFICATION
                                            |
                                            v
                                          STOPPED
```

### State rules

#### BUY

Tìm template:

```text
templates/buy_bulk.png
```

Khi tìm thấy:

```text
click Mua hàng loạt
-> BUY_CONFIRM
```

#### BUY_CONFIRM

Tìm:

```text
templates/confirm_bulk.png
```

Khi tìm thấy:

```text
click Xác nhận
-> WAIT_RESULT
```

#### WAIT_RESULT

Chỉ xác định hai trạng thái thực sự:

```text
fco_failure_title.png
receive_now.png
```

Không dùng `Mua hàng loạt` để xác định kết quả vì nút này có thể vẫn nhìn thấy phía sau popup.

```text
failure detected -> FAILURE
receive detected  -> SUCCESS
```

#### FAILURE

Không cộng cầu thủ.

```text
+0
```

Sau khi đóng popup:

```text
-> BUY
```

#### SUCCESS

Chờ:

```text
receive_now.png
```

Sau đó:

```text
click Nhận ngay
-> FINAL_CONFIRM
```

#### FINAL_CONFIRM

Tìm:

```text
confirm_received.png
```

Khi thấy popup:

```text
-> OCR_COUNT
```

#### OCR_COUNT

Đọc câu dạng:

```text
Bạn đã mua được tổng cộng X cầu thủ
```

Ví dụ:

```text
Ban da mua duge tong cong 2 cau tha
```

Kết quả:

```text
X = 2
```

#### UPDATE_COUNTER

Cập nhật:

```text
purchased_players += X
```

Không cộng `+1` theo round.

Ví dụ:

```text
Round 1: +2
Round 2: +0
Round 3: +3

Total = 5
```

Nếu:

```text
purchased_players >= target_players
```

thì:

```text
-> COMPLETED
```

ngược lại:

```text
-> BUY
```

#### COMPLETED

Phát:

```text
notify.mp3 / notify.wav
```

Sau đó stop bot.

---

## 4. Component Architecture

### 4.1 UI Layer

File đề xuất:

```text
ui/app.py
```

Trách nhiệm:

```text
- Nhập số cầu thủ mục tiêu
- START
- STOP
- Hiển thị status
- Hiển thị số đã mua
- Hiển thị log
```

UI không xử lý OpenCV/OCR trực tiếp.

UI chỉ giao tiếp với controller:

```python
start_bot()
stop_bot()

update_status(...)
update_player_count(...)
add_log(...)
```

---

### 4.2 Bot Controller

File:

```text
bot/controller.py
```

Trách nhiệm:

```text
- Start bot
- Stop bot
- Quản lý thread
- Lấy HWND FC Online
- Khởi tạo State Machine
- Truyền callback về UI
```

Controller không nên tự xử lý OCR hoặc template matching.

---

### 4.3 State Machine

File:

```text
bot/state_machine.py
```

Chứa:

```text
current_state
transition
run current state
```

Ví dụ:

```python
BUY
BUY_CONFIRM
WAIT_RESULT
FAILURE
FAILURE_CONFIRM
SUCCESS
RECEIVE
FINAL_CONFIRM
OCR_COUNT
UPDATE_COUNTER
COMPLETED
STOPPED
```

Lợi ích:

```text
Không phải dựa vào:
sleep -> đoán game đã sẵn sàng
```

Thay vào đó:

```text
check image -> xác định state thực tế
```

---

### 4.4 Player Counter

File:

```text
bot/counter.py
```

Ví dụ:

```python
class PlayerCounter:
    target = 10
    purchased = 0

    def add(self, count):
        self.purchased += count

    def is_completed(self):
        return self.purchased >= self.target
```

Không để logic counter rải rác trong flow.

---

### 4.5 Screen Capture

File:

```text
services/screen_capture.py
```

Trách nhiệm:

```text
HWND
  |
  v
GetWindowRect
  |
  v
ImageGrab
  |
  v
NumPy image
```

API:

```python
capture_window(hwnd)
```

---

### 4.6 Image Detector

File:

```text
services/image_detector.py
```

Sử dụng:

```text
OpenCV
cv2.matchTemplate
TM_CCOEFF_NORMED
```

API:

```python
find_template(screen, template)
```

Kết quả:

```python
{
    "found": True,
    "confidence": 0.992,
    "x": 717,
    "y": 606
}
```

Các template:

```text
templates/
├── buy_bulk.png
├── confirm_bulk.png
├── fco_failure_title.png
├── receive_now.png
└── confirm_received.png
```

---

### 4.7 Input Controller

File:

```text
services/input_controller.py
```

Trách nhiệm:

```text
Image coordinates
       |
       v
Client coordinates
       |
       v
WM_MOUSEMOVE
WM_LBUTTONDOWN
WM_LBUTTONUP
```

API:

```python
click(hwnd, x, y)
```

Có thể hỗ trợ:

```python
click(..., method="send")
click(..., method="post")
```

---

### 4.8 OCR Service

File:

```text
services/ocr_service.py
```

Flow:

```text
Final Confirmation Popup
        |
        v
Crop COUNT_ROI
        |
        v
Resize
        |
        v
Threshold
        |
        v
Tesseract
        |
        v
Text
        |
        v
Regex
        |
        v
X
```

Ví dụ:

```text
OCR:
Ban da mua duge tong cong 2 cau tha

Result:
2
```

API:

```python
read_purchase_count(hwnd)
```

Kết quả:

```text
2
```

Nếu OCR fail:

```text
0
```

---

### 4.9 Notification Service

File:

```text
services/notification.py
```

Dùng:

```python
winsound.PlaySound(...)
```

Khi hoàn thành:

```text
target reached
    |
    v
play notification
    |
    v
stop bot
```

Resource:

```text
notify.wav
```

---

### 4.10 Resource Manager

File:

```text
core/resource_manager.py
```

API:

```python
resource_path("templates/buy_bulk.png")
resource_path("notify.wav")
```

Phải hỗ trợ:

```text
Python execution
PyInstaller --onedir
PyInstaller --onefile
```

---

## 5. Project Structure

```text
FCOnlineAutoBuy/
│
├── main.py
├── config.py
├── requirements.txt
│
├── ui/
│   └── app.py
│
├── bot/
│   ├── controller.py
│   ├── state_machine.py
│   └── counter.py
│
├── services/
│   ├── screen_capture.py
│   ├── image_detector.py
│   ├── input_controller.py
│   ├── ocr_service.py
│   └── notification.py
│
├── core/
│   └── resource_manager.py
│
├── templates/
│   ├── buy_bulk.png
│   ├── confirm_bulk.png
│   ├── fco_failure_title.png
│   ├── receive_now.png
│   └── confirm_received.png
│
└── notify.wav
```

---

## 6. Runtime Flow

```text
User
 |
 | nhập target = 10
 |
 v
UI
 |
 | START
 v
Bot Controller
 |
 v
State Machine
 |
 v
BUY
 |
 | Image Detector
 v
Mua hàng loạt
 |
 v
BUY_CONFIRM
 |
 | Image Detector
 v
Xác nhận
 |
 v
WAIT_RESULT
 |
 +----------------------+
 |                      |
 v                      v
FAILURE               SUCCESS
 |                      |
 | +0                   v
 v                   RECEIVE
BUY                      |
                        v
                  FINAL_CONFIRM
                        |
                        v
                    OCR_COUNT
                        |
                        v
                  UPDATE_COUNTER
                        |
                        +----> purchased = 3
                        |
                        +----> target = 10
                        |
                        v
                    BUY AGAIN
```

Khi:

```text
purchased >= target
```

thì:

```text
COMPLETED
    |
    v
Notification
    |
    v
STOPPED
```

---

## 7. Detection Strategy

### Template Detection

Dùng OpenCV:

```python
cv2.matchTemplate(
    screen,
    template,
    cv2.TM_CCOEFF_NORMED
)
```

Threshold mặc định:

```text
0.80 - 0.85
```

Có thể dùng threshold riêng cho những template có độ ổn định thấp.

Ví dụ:

```text
BUY_THRESHOLD     = 0.85
CONFIRM_THRESHOLD = 0.85
RESULT_THRESHOLD  = 0.75
```

Không nên dùng một threshold cứng cho tất cả trạng thái nếu hình ảnh thực tế có độ ổn định khác nhau.

---

## 8. OCR Strategy

OCR chỉ được gọi khi:

```text
SUCCESS
+
FINAL_CONFIRM detected
```

Không chạy OCR liên tục khi đang chờ popup.

Điều này giúp giảm thời gian xử lý.

### COUNT_ROI

Hiện tại vùng test thành công:

```text
COUNT_ROI = (480, 535, 950, 660)
```

Window reference:

```text
1296 x 759
```

OCR preprocessing:

```text
Crop
 ↓
Grayscale
 ↓
Resize x4
 ↓
Gaussian Blur
 ↓
Otsu Threshold
 ↓
Tesseract
```

Ưu tiên:

```text
BINARY + PSM 6
```

sau đó fallback:

```text
GRAY + PSM 6
```

Regex:

```regex
tong\s+cong\s*(\d+)\D*cau
```

Fallback cho trường hợp OCR nhận số 1 thành ký tự:

```regex
tong\s+cong\s*([|ilI])\s*cau
```

---

## 9. Error Handling

### Game không phản hồi

Không nên:

```text
timeout -> stop bot ngay
```

Ưu tiên:

```text
check current screen
-> xác định state
-> retry state
```

### Template không tìm thấy

Log:

```text
confidence
current state
```

Ví dụ:

```text
[WAIT_RESULT]
Failure = 0.21
Receive = 0.63
```

### OCR fail

Không crash:

```text
OCR fail
   |
   v
+0
   |
   v
continue
```

Có thể retry OCR một số lần giới hạn.

### Stop

Ở mọi state cần kiểm tra:

```python
stop_event.is_set()
```

để người dùng có thể dừng bot.

---

## 10. Threading

UI thread:

```text
CustomTkinter
```

không được chạy bot trực tiếp vì OCR/OpenCV có thể block UI.

Kiến trúc:

```text
Main Thread
    |
    +---- UI
    |
    +---- Bot Thread
             |
             +---- State Machine
             +---- OpenCV
             +---- OCR
```

Callback từ bot về UI:

```python
log_callback(...)
player_count_callback(...)
status_callback(...)
```

UI update nên thông qua:

```python
app.after(...)
```

---

## 11. Configuration

File:

```text
config.py
```

Ví dụ:

```python
WINDOW_TITLE = "FC ONLINE"

IMAGE_THRESHOLD = 0.85

RESULT_THRESHOLD = 0.75

CAPTURE_INTERVAL = (0.04, 0.07)

CLICK_RETRY = 2

COUNT_ROI = (
    480,
    535,
    950,
    660
)

NOTIFY_SOUND = "notify.wav"
```

Như vậy sau này muốn chỉnh tốc độ/threshold không phải đi sửa nhiều chỗ.

---

## 12. Build EXE

Khuyến nghị dùng PyInstaller `--onedir`.

```bat
pyinstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --name FCOnlineAutoBuy ^
  --add-data "templates;templates" ^
  --add-data "notify.wav;." ^
  main.py
```

Output:

```text
dist/
└── FCOnlineAutoBuy/
    ├── FCOnlineAutoBuy.exe
    ├── notify.wav
    └── templates/
        ├── buy_bulk.png
        ├── confirm_bulk.png
        ├── fco_failure_title.png
        ├── receive_now.png
        └── confirm_received.png
```

---

## 13. Dependencies

```text
customtkinter
opencv-python
numpy
Pillow
pywin32
pytesseract
```

Tesseract OCR engine là external dependency trên Windows.

Expected path hiện tại:

```text
C:\Program Files\Tesseract-OCR\tesseract.exe
```

---

## 14. Design Principles

### Single Responsibility

Mỗi module chỉ nên có một nhiệm vụ:

```text
UI              -> UI
Controller      -> lifecycle
State Machine   -> flow
Detector        -> image
OCR             -> text/count
Input           -> click
Counter         -> counting
Notification    -> sound
Resource        -> files
```

### Image-driven

Bot quyết định dựa trên:

```text
hình ảnh thực tế của game
```

không dựa chủ yếu vào:

```text
sleep 1 giây
sleep 2 giây
```

Sleep chỉ dùng để tránh polling quá nhanh.

### No round-based counting

Không được:

```python
purchased_players += 1
```

Thay vào đó:

```python
count = ocr.read_purchase_count()
purchased_players += count
```

Failure:

```python
purchased_players += 0
```

### UI độc lập với Bot

UI không biết OpenCV/Tesseract hoạt động thế nào.

Bot không biết layout UI.

Hai bên giao tiếp qua callback/interface.

---

## 15. Current Architecture → Target Architecture

### Current

```text
app_clean_fixed.py
        |
        v
auto_buy.py
        |
        +-- capture
        +-- OpenCV
        +-- click
        +-- OCR
        +-- counter
        +-- notification
        +-- state flow
```

### Target

```text
app.py
   |
   v
controller.py
   |
   v
state_machine.py
   |
   +--> screen_capture.py
   +--> image_detector.py
   +--> input_controller.py
   +--> ocr_service.py
   +--> counter.py
   +--> notification.py
   +--> resource_manager.py
```

Target architecture giúp code dễ mở rộng hơn khi thêm:

```text
- Pause / Resume
- Multiple accounts
- Different templates
- Sound selection
- OCR profiles
- Statistics
- Export logs
- Auto retry
- Error screenshots
```

---

## 16. Recommended Next Step

Không cần refactor toàn bộ ngay một lần.

Thứ tự nên làm:

```text
1. Tách Resource Manager
2. Tách Screen Capture
3. Tách Image Detector
4. Tách Input Controller
5. Tách OCR Service
6. Tách Counter
7. Tách Notification
8. Tách State Machine
9. Giữ UI làm layer cuối
```

Sau khi tách xong, `auto_buy.py` hiện tại có thể được thay bằng một controller/state machine nhỏ hơn nhiều, dễ đọc và dễ debug.
