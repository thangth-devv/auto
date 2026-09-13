# Player Insert - Architecture

## 1. Mục tiêu

Player Insert tự động chèn cầu thủ theo giờ reset trong FC Online:

- Sort hàng đợi một lần khi bấm `START`.
- Giữ thứ tự đã sort trong suốt phiên chạy.
- Mỗi lần chỉ xử lý một cầu thủ hiện tại.
- Chọn cầu thủ bằng **VỊ TRÍ** trong game.
- Dùng **THỨ TỰ** để xác định cầu thủ nào được xử lý trước.
- Đọc giá tối đa sau khi mở popup `Mua cầu thủ`.
- Mua khi giá thay đổi.
- Nhấn `ESC` khi giá chưa đổi.
- Lưu GIF trong 3 giây sau khi mua thành công.

## 2. Khái niệm dữ liệu

Mỗi dòng cấu hình có dạng:

```text
{
    "position": 5,      # vị trí cầu thủ trong danh sách game
    "reset": "l32",     # giờ lẻ, phút 32
    "quantity": 10      # số lượng cần mua
}
```

### 2.1 THỨ TỰ

`THỨ TỰ` là thứ tự xử lý trong hàng đợi sau khi sort theo reset gần nhất.

Ví dụ tại thời điểm hiện tại:

```text
Thứ tự 1: vị trí 5, l32
Thứ tự 2: vị trí 3, l50
Thứ tự 3: vị trí 4, l43
```

Bot phải xử lý thứ tự 1 xong hoặc hết thời gian của thứ tự 1 trước khi
chuyển sang thứ tự 2.

### 2.2 VỊ TRÍ

`VỊ TRÍ` là dòng cầu thủ trong game để click:

```python
row_y = PLAYER_ROW_FIRST_Y + (position - 1) * PLAYER_ROW_STEP_Y
```

Sort không được thay đổi giá trị `position`.

### 2.3 Mã reset

```text
cNN = giờ chẵn, phút NN
lNN = giờ lẻ, phút NN
```

Ví dụ:

```text
l32 -> giờ lẻ, phút 32
c50 -> giờ chẵn, phút 50
```

Mã reset được chuẩn hóa bằng `normalize_reset_code()`.

## 3. Kiến trúc tổng quan

```text
CustomTkinter UI (app.py)
        |
        | entries, stop_event, log_callback
        v
Player Insert Worker (player_insert.py)
        |
        +--> Queue Sorter
        +--> Queue State Machine
        +--> Input Controller (auto_buy.py)
        +--> Screen Capture (capture_fco)
        +--> Price OCR (Tesseract)
        +--> GIF Recorder
        |
        v
FC Online window
```

### 3.1 UI

File: `app.py`

Trách nhiệm:

- Nhập vị trí, reset, số lượng.
- Sort và ghi lại thứ tự hiển thị khi `START`.
- Chạy worker thread.
- Hiển thị log.
- Lưu/tải profile `profiles.json`.
- Không tự click game hoặc chạy OCR.

### 3.2 Worker

File: `player_insert.py`

Trách nhiệm:

- Sort queue.
- Chọn vị trí cầu thủ hiện tại.
- Điều khiển popup mua.
- Đọc giá max.
- Chờ reset.
- Retry trong cửa sổ reset.
- Mua và ghi GIF.

### 3.3 Input

File: `auto_buy.py`

Các thao tác chính:

```python
click_client(hwnd, x, y)
key_press(hwnd, win32con.VK_ESCAPE)
double_click_input(hwnd, x, y)
type_text(hwnd, text)
```

Nút `Mua cầu thủ` dùng tọa độ cố định, không dùng template matching:

```python
BUY_PLAYER_X
BUY_PLAYER_Y
```

## 4. Queue sorting

Khi bấm `START`:

1. Đọc toàn bộ dòng có cấu hình.
2. Chuẩn hóa mã reset.
3. Tính reset gần nhất so với `datetime.now()`.
4. Sort tăng dần theo thời điểm reset.
5. Ghi dữ liệu đã sort ngược lại UI.
6. Gán lại số `THỨ TỰ` từ 1.
7. Truyền queue đã sort vào worker.

```python
sorted_entries = sort_entries_by_reset(entries)
```

Sau khi worker bắt đầu, không sort lại trong vòng lặp. Queue chỉ tiến về phía
trước sau khi cầu thủ hiện tại:

- mua thành công; hoặc
- hết 20 phút của cửa sổ reset mà giá không đổi.

Không được quét tất cả cầu thủ để chọn cầu thủ có reset đang mở. Điều này sẽ
làm cầu thủ phía sau nhảy lên trước.

## 5. State machine

```text
START
  |
  v
SORT_QUEUE
  |
  v
SELECT_CURRENT_PLAYER
  |
  v
OPEN_BUY_POPUP
  |
  v
READ_BASE_PRICE
  |
  v
CLOSE_POPUP
  |
  +--> WAIT_FOR_RESET
  |       |
  |       +--> PREPARE_ONE_MINUTE_BEFORE_RESET
  |       |       |
  |       |       +--> READ_BASE_PRICE
  |       |
  |       +--> RESET_WINDOW
  |               |
  |               v
  |          OPEN_BUY_POPUP
  |               |
  |               v
  |          READ_CURRENT_PRICE
  |               |
  |       +-------+-------+
  |       |               |
  |   PRICE_CHANGED   PRICE_UNCHANGED
  |       |               |
  |       v               v
  |   BUY_PLAYER       PRESS_ESC
  |       |               |
  |       v               |
  |   RECORD_GIF          |
  |       |               |
  |       +-------+-------+
  |               |
  |               v
  |        NEXT_QUEUE_ITEM
  |
  +--> AFTER_20_MINUTES_UNCHANGED
              |
              v
       NEXT_QUEUE_ITEM
```

## 6. Flow chi tiết

### 6.1 START

```text
START
 -> parse configuration
 -> sort by nearest reset
 -> update UI
 -> select first queue item
 -> click game position
 -> click Mua cầu thủ
 -> capture screen
 -> read Giá tối đa
 -> log and store base price
 -> press ESC
```

Không đọc giá trước khi mở popup mua.

### 6.2 Chuẩn bị trước reset

Với cầu thủ hiện tại, nếu còn không quá 60 giây đến reset:

```text
click lại vị trí hiện tại
 -> click Mua cầu thủ
 -> read Giá tối đa
 -> cập nhật base price
 -> press ESC
 -> chờ giờ reset
```

Chỉ chuẩn bị một lần cho mỗi thời điểm reset.

### 6.3 Cửa sổ reset

Cửa sổ xử lý kéo dài 20 phút kể từ reset. Chỉ thử trong 12 giây đầu mỗi phút:

```text
reset_minute <= current_minute < reset_minute + 20
current_second < 12
```

Mỗi lần thử:

```text
click Mua cầu thủ
 -> chờ popup render tối thiểu
 -> capture_fco(hwnd)
 -> OCR Giá tối đa
```

### 6.4 Giá không đổi

Nếu:

```python
current_price == base_price
```

thì:

```text
press ESC
 -> giữ nguyên cầu thủ hiện tại
 -> thử lại ở phút kế tiếp
```

Không chuyển sang cầu thủ tiếp theo chỉ vì cầu thủ tiếp theo cũng đang trong
cửa sổ reset.

### 6.5 Giá thay đổi

Nếu:

```python
current_price != base_price
```

thì:

```text
click dòng Giá tối đa
```

Nếu `quantity > 1`:

```text
double_click ô Số lượng mua
 -> nhập quantity
 -> click Mua cầu thủ
```

Nếu `quantity == 1`:

```text
click Mua cầu thủ
```

Sau đó:

```text
capture frame trong 3 giây
 -> save GIF
 -> chuyển sang queue item kế tiếp
```

## 7. Price OCR

### 7.1 Quy tắc bắt buộc

OCR chỉ chạy sau khi popup mua đã mở.

Giá cần đọc là số nằm cùng dòng với nhãn:

```text
Giá tối đa
```

Không đọc:

- `Giá tối thiểu`.
- `Giá cầu thủ` phía ngoài popup.
- Giá nhập trong ô `Giá`.
- Tổng giá trị.
- Số lượng mua.

### 7.2 ROI

ROI được cấu hình theo layout tham chiếu:

```python
REFERENCE_WIDTH = 1280
REFERENCE_HEIGHT = 752
MAX_PRICE_ROIS = (...)
```

ROI phải bao quanh dòng `Giá tối đa`, không bao gồm ô nhập giá bên dưới.

Nếu thay đổi scale hoặc kích thước game, phải điều chỉnh ROI hoặc thêm cấu hình
scale tương ứng.

### 7.3 Định dạng hợp lệ

```text
18.3M -> 18,300,000
20.1M -> 20,100,000
1.2B  -> 1,200,000,000
```

Hiển thị log:

```text
18.3M
20.1M
1.2B
```

Không được suy diễn giá từ số trong ô nhập `20.100.000`.

## 8. GIF recorder

GIF bắt đầu khi giá đã thay đổi và kết thúc sau thao tác mua:

```text
price changed
 -> capture frame
 -> click giá tối đa
 -> nhập quantity nếu cần
 -> click Mua cầu thủ
 -> capture tiếp trong 3 giây
 -> save GIF
```

Thư mục:

```text
purchase_gifs/
```

Tên file:

```text
position_{position}_{reset}_{timestamp}.gif
```

GIF chỉ được tạo cho lần mua có giá thay đổi.

## 9. Logging

Log phải phân biệt thứ tự xử lý và vị trí game:

```text
[Thứ tự 1][Vị trí 5] Đang chọn cầu thủ.
[Thứ tự 1][Vị trí 5] Giá max mốc: 18.3M.
[Thứ tự 1][Vị trí 5] Giá hiện tại: 20.1M.
[Thứ tự 1][Vị trí 5] Đã gửi lệnh mua 10 cầu thủ.
[Thứ tự 1][Vị trí 5] Đã lưu GIF: ...
```

Khi giá không đổi:

```text
[Thứ tự 1][Vị trí 5] Giá không đổi; nhấn ESC và thử lại.
```

Khi chuyển queue:

```text
[Thứ tự 1][Vị trí 5] Hoàn tất; chuyển sang thứ tự 2.
```

## 10. Timing và hiệu năng

Các hằng số:

```python
PLAYER_SELECTION_WAIT
PRICE_DIALOG_WAIT
GIF_CAPTURE_INTERVAL
GIF_RETURN_WAIT = 3.0
```

Không dùng thời gian chờ dài cố định. Chỉ dùng delay tối thiểu để popup kịp
render trước khi capture.

Mọi vòng chờ phải kiểm tra:

```python
stop_event.is_set()
```

## 11. Threading

```text
Main thread:
    CustomTkinter UI

Worker thread:
    queue flow
    click
    capture
    OCR
    GIF
```

Worker không được cập nhật trực tiếp widget Tkinter. Log phải gửi về UI bằng:

```python
app.after(0, ...)
```

## 12. Error handling

### Không tìm thấy game

Không start worker và log lỗi rõ ràng.

### Không đọc được giá

```text
log cảnh báo
press ESC
giữ cầu thủ hiện tại
thử lại ở vòng kế tiếp
```

Không được coi lỗi OCR là giá thay đổi hoặc giá không đổi.

### Popup không mở

Không click mua hoặc nhập quantity tiếp. Đóng/khôi phục trạng thái rồi retry
ở vòng kế tiếp.

### STOP

STOP phải:

```text
set stop_event
worker thoát tại điểm an toàn
không tiếp tục click hoặc nhập text
```

## 13. Files liên quan

```text
app.py
    UI, parse input, sort hiển thị, profile, worker startup

player_insert.py
    queue flow, reset logic, price OCR, buy flow, GIF

auto_buy.py
    capture_fco, click_client, key_press, double_click_input, type_text

profiles.json
    profile Player Insert

purchase_gifs/
    GIF giao dịch thành công
```

## 14. Checklist kiểm thử

- [ ] Sort đúng reset gần nhất khi START.
- [ ] UI đổi đúng thứ tự nhưng giữ nguyên VỊ TRÍ game.
- [ ] Chỉ click cầu thủ thứ tự hiện tại.
- [ ] Popup được mở bằng tọa độ nút mua cố định.
- [ ] Giá `20.1M` được đọc đúng, không đọc `16.6M`.
- [ ] Giá không đổi nhấn ESC và giữ nguyên cầu thủ.
- [ ] Cầu thủ sau không được xử lý trước cầu thủ hiện tại.
- [ ] Chuẩn bị lại giá trước reset 1 phút.
- [ ] Chỉ thử trong 12 giây đầu mỗi phút.
- [ ] Hết 20 phút mới chuyển cầu thủ.
- [ ] Quantity bằng 1 không double-click ô số lượng.
- [ ] Quantity lớn hơn 1 nhập đúng số lượng.
- [ ] GIF có đủ 3 giây sau khi mua.
- [ ] STOP dừng được worker.
