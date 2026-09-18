# Player Insert - Architecture

## 1. Mục tiêu

Player Insert tự động chèn cầu thủ theo giờ reset trong FC Online:

- Sort hàng đợi một lần khi bấm `START`.
- Giữ thứ tự đã sort trong suốt phiên chạy.
- Mỗi lần chỉ xử lý một cầu thủ hiện tại.
- Chọn cầu thủ bằng **VỊ TRÍ** trong game.
- Dùng **THỨ TỰ** để xác định cầu thủ nào được xử lý trước.
- Đọc giá tối đa sau khi mở popup `Mua cầu thủ`.
- Ghi nhớ một giá mốc cho từng thời điểm reset.
- Mua chỉ khi giá trong popup khác giá ghi nhớ.
- Nhấn `ESC` khi giá chưa đổi.
- Chờ popup đóng rồi mới xác nhận mua thành công và phát thông báo.

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
WAIT_FOR_RESET
  |
  v
READ_RESET_BASE_PRICE (một lần)
  |
  v
RESET_WINDOW
  |
  v
OPEN_BUY_POPUP
  |
  v
READ_CURRENT_PRICE
  |
+-------------------+
|                   |
PRICE_UNCHANGED   PRICE_CHANGED
|                   |
v                   v
PRESS_ESC       SELECT_MAX_PRICE
|                   |
|             quantity > 1?
|                   |
|          +--------+--------+
|          |                 |
|         YES               NO
|          |                 |
|    INPUT_QUANTITY     BUY_PLAYER
|          |                 |
|          +--------> BUY_PLAYER
|                            |
|                    WAIT_POPUP_CLOSE
|                            |
|                  +---------+---------+
|                  |                   |
|               CLOSED             TIMEOUT
|                  |                   |
|             NOTIFY              WARN_ONLY
|                  |
+------------------+
          |
   retry in same reset
          |
   reset window ends
          |
   NEXT_QUEUE_ITEM
```

## 6. Flow chi tiết

### 6.1 Bắt đầu xử lý cầu thủ

```text
select current player
 -> press ESC and confirm stale popup is closed
 -> nếu không đóng được popup: cảnh báo và dừng an toàn
 -> click configured player position
 -> chờ game cập nhật vị trí đã chọn
 -> chờ cửa sổ reset
 -> khi bắt đầu reset, mở popup Mua cầu thủ
 -> đọc Giá tối đa đúng một lần
 -> lưu reset_at và reset_base_price
 -> press ESC
```

Nếu đang ở giữa cửa sổ reset còn hiệu lực, phải dùng đúng `reset_at` của
reset đã bắt đầu, kể cả khi reset đi qua giờ tiếp theo. Không được nhảy sang
reset kế tiếp chỉ vì `now.hour` đã thay đổi.

### 6.2 Ghi nhớ giá theo reset

Mỗi thời điểm reset có một giá ghi nhớ riêng:

```text
reset_base_price = giá đọc tại đầu cửa sổ reset
```

Trong suốt 20 phút của cửa sổ đó, `reset_base_price` không được cập nhật lại.
Khi cửa sổ kết thúc và bước sang reset mới, bot đọc giá mới một lần và thay thế
giá ghi nhớ cũ.

### 6.3 Cửa sổ reset

Cửa sổ xử lý kéo dài 20 phút kể từ reset. Chỉ thử trong 15 giây đầu mỗi phút:

```text
reset_at <= current_time < reset_at + 20 minutes
current_second < 15
```

Mỗi lần thử:

```text
click Mua cầu thủ
 -> chờ popup render tối thiểu
 -> capture_fco(hwnd)
 -> OCR Giá tối đa
 -> so sánh với reset_base_price
```

### 6.4 Giá không đổi

Nếu:

```python
current_price == reset_base_price
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
current_price != reset_base_price
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
chờ popup mua biến mất ổn định
 -> nếu popup chưa đóng trong timeout: cảnh báo, chưa xác nhận mua
 -> nếu popup đã đóng: ghi nhận thành công
 -> phát thông báo
 -> save GIF
 -> chuyển sang queue item kế tiếp
```

Không được click dòng giá tối đa, nhập quantity hoặc click mua khi
`current_price == reset_base_price`.

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
20.1M / 20,1M -> 20,100,000
1.2B / 1,2B  -> 1,200,000,000
```

Hiển thị log:

```text
18.3M
20.1M
1.2B
```

OCR phải giữ dấu phân cách thập phân mà game hiển thị (`.` hoặc `,`).
Không được bỏ dấu này vì `24,2M` phải được đọc là `24.2M`, không phải `242M`.
Không được suy diễn giá từ số trong ô nhập `20.100.000`.
Nếu các lần OCR trên cùng ROI cho nhiều kết quả khác nhau, chọn giá trị có
nhiều kết quả đồng thuận nhất; không chọn giá trị lớn nhất.

## 8. GIF recorder

GIF bắt đầu khi giá đã thay đổi và kết thúc sau khi popup đóng:

```text
price changed
 -> capture frame
 -> click giá tối đa
 -> nhập quantity nếu cần
 -> click Mua cầu thủ
 -> capture cho đến khi popup đóng ổn định
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

Sau khi phát hiện giá đã reset và bot click vào hàng `Giá tối đa`, bot lưu
ảnh crop quanh tọa độ click, có đánh dấu điểm click bằng dấu đỏ. Ảnh được lưu
trong thư mục:

```text
ocr_debug/
```

Mỗi lần giá đã reset, bot lưu một ảnh sau khi click vào tọa độ cố định giữa
dòng giá tối đa để kiểm tra trực quan.

## 9. Logging

Log phải phân biệt thứ tự xử lý và vị trí game:

```text
[Thứ tự 1][Vị trí 5] Đang chọn cầu thủ.
[Thứ tự 1][Vị trí 5] Giá ghi nhớ reset: 18.3M.
[Thứ tự 1][Vị trí 5] Giá trên game: 20.1M.
[Thứ tự 1][Vị trí 5] Đã mua được 10 cầu thủ với giá 20.1M/cầu thủ.
[Thứ tự 1][Vị trí 5] Đã lưu GIF: ...
```

Khi giá không đổi:

```text
[Thứ tự 1][Vị trí 5] Giá ghi nhớ: 24.2M; giá trên game: 24.2M.
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
PRICE_DIALOG_TIMEOUT
POPUP_CLOSE_BEFORE_SELECTION_TIMEOUT
GIF_CAPTURE_INTERVAL
POPUP_CLOSE_TIMEOUT
POPUP_CLOSE_STABLE_READS
```

`PLAYER_SELECTION_WAIT` dùng để chờ game cập nhật vị trí sau khi click.
`POPUP_CLOSE_BEFORE_SELECTION_TIMEOUT` dùng để xác nhận popup cũ đã đóng trước
khi click vị trí mới. `PRICE_DIALOG_TIMEOUT` chỉ chờ popup mua xuất hiện.
Không được đọc giá hoặc click mua khi popup cũ chưa được xác nhận đóng.

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

### Popup cũ chưa đóng

Trước khi chọn một vị trí mới:

```text
press ESC
 -> capture màn hình
 -> xác nhận vùng popup không còn sáng
 -> chỉ khi xác nhận đóng mới click vị trí
```

Nếu hết timeout mà popup vẫn còn, log cảnh báo và dừng an toàn để không đọc
nhầm giá của cầu thủ trước.

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
- [ ] Popup cũ được đóng và xác nhận trước khi click vị trí mới.
- [ ] Không đọc giá mốc khi vị trí mới chưa được cập nhật.
- [ ] Popup được mở bằng tọa độ nút mua cố định.
- [ ] Giá `20.1M` được đọc đúng, không đọc `16.6M`.
- [ ] Giá không đổi nhấn ESC và giữ nguyên cầu thủ.
- [ ] Cầu thủ sau không được xử lý trước cầu thủ hiện tại.
- [ ] Đọc và lưu giá đúng một lần ở đầu mỗi reset.
- [ ] Không cập nhật giá ghi nhớ trong cùng cửa sổ reset.
- [ ] Giá đi qua giờ tiếp theo vẫn giữ đúng cửa sổ reset cũ.
- [ ] Chỉ thử trong 12 giây đầu mỗi phút.
- [ ] Hết 20 phút mới chuyển cầu thủ.
- [ ] Giá trên game bằng giá ghi nhớ thì không mua.
- [ ] Quantity bằng 1 không double-click ô số lượng.
- [ ] Quantity lớn hơn 1 nhập đúng số lượng.
- [ ] Chỉ phát thông báo sau khi popup đóng.
- [ ] Popup không đóng thì không xác nhận mua thành công.
- [ ] STOP dừng được worker.
