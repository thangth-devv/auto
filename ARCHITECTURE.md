# AUTO FCO - Kiến trúc hệ thống

python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --name AutoFCO --icon icon.png --add-data "templates;templates" --add-data "tesseract;tesseract" --add-data "notify.mp3;." --add-data "icon.png;." --add-data "profiles.json;." app.py

## 1. Mục tiêu

AUTO FCO là ứng dụng tự động hóa tương tác với FC Online bằng Windows automation, bao gồm hai luồng chính:

- Auto Buy: lọc và mua cầu thủ theo chỉ số thống kê, giá tối đa và số lượng mục tiêu.
- Player Insert: xử lý hàng chờ mua theo reset, giữ đúng thứ tự, quản lý cửa sổ reset và xác nhận mua khi giá đã đổi.

Mục tiêu chung của hệ thống là:

- Tìm đúng cửa sổ FC ONLINE đang chạy.
- Tương tác với UI bằng tọa độ và template matching thay vì giả lập chuột rời rạc.
- Chia rõ UI thread và worker thread để không block giao diện.
- Dùng OCR và hình ảnh để xác định trạng thái popup và giá tối đa.
- Nhận diện cấp thẻ sở hữu bằng OCR trên badge ở góc dưới bên trái thẻ cầu thủ;
  không dùng template matching để quyết định cấp thẻ sở hữu.
- Luồng Đập cầu thủ (upgrade) chỉ lọc phôi theo OVR (MIN/MAX);
  không check cấp thẻ khi chọn phôi.
- Có cơ chế dừng an toàn (`stop_event`) khi cần ngắt hoạt động.

## 2. Cấu trúc thư mục

```text
AUTO_FCO/
├── app.py                  # UI + orchestration
├── auto_buy.py             # low-level automation + OCR + template matching
├── player_insert.py        # queue flow, reset logic, price validation
├── dev.py                  # dev/test helper
├── profiles.json           # profile lưu trữ nhanh của Auto Buy
├── templates/              # asset mẫu template cho tìm nút / popup
├── purchase_gifs/          # GIF được lưu khi mua thành công
├── ocr_debug/              # debug ảnh OCR / click debug
├── icon.png                # icon app
├── tesseract/               # dụng cụ OCR
└── ARCHITECTURE.md         # tài liệu kiến trúc
```

## 3. Kiến trúc tổng quan

```text
CustomTkinter UI (app.py)
        |
        +--> profile management
        +--> window detection
        +--> worker lifecycle
        +--> log/view updates via app.after(0, ...)
        |
        v
Worker threads
        |
        +--> Auto Buy flow (run_auto_buy)
        |       +--> screen capture
        |       +--> template match
        |       +--> click/scroll/input controls
        |       +--> purchase validation
        |
        +--> Player Insert flow (run_player_insert)
                +--> queue sort by reset
                +--> reset window state machine
                +--> price OCR on buy popup
                +--> GIF recorder / announcement
                +--> safe stop on timeout/error
        |
        v
FC ONLINE window
```

## 4. Thành phần chính

### 4.1 app.py

`app.py` là lớp orchestration và UI chính của hệ thống.

Trách nhiệm:

- Tạo giao diện CustomTkinter với các panel cấu hình, timer, progress bar, log.
- Tìm cửa sổ FC Online bằng `find_fc_online()` và theo dõi `current_hwnd`.
- Quản lý `profiles.json` tại `%LOCALAPPDATA%\AutoFCO` để giữ cấu hình qua
  các lần khởi động và cập nhật; nếu chưa có file người dùng thì nạp profile
  mặc định được đóng gói cùng ứng dụng.
- Kiểm tra GitHub Releases khi chạy bản đóng gói; hỏi người dùng trước khi tải
  và thay thế executable.
- Khởi chạy background worker cho Auto Buy hoặc Player Insert.
- Gửi log và trạng thái về UI bằng `app.after(0, ...)` để tránh lỗi Tkinter thread.
- Chạy vòng lặp `auto_scan_fc_online()` để tìm lại cửa sổ nếu cần.

Các biến trạng thái quan trọng:

```python
bot_thread = None
stop_event = None
is_running = False
current_hwnd = None
player_insert_window = None
player_insert_thread = None
player_insert_stop_event = None
```

### 4.2 auto_buy.py

`auto_buy.py` là module xử lý thao tác màn hình và OCR thấp mức.

Trách nhiệm:

- `capture_fco(hwnd)`: chụp toàn bộ cửa sổ FC Online.
- `wait_for_valid_window(hwnd)`: đảm bảo cửa sổ đã sẵn sàng trước khi thao tác.
- `find_template(screen, template)`: so khớp hình ảnh bằng OpenCV (`cv2.matchTemplate`).
- `load_template`, `create_bold_template`: load asset mẫu từ `templates/`.
- `click_client`, `double_click_input`, `type_text`, `key_press`: thực thi hành động trên game
  bằng click message nền; không được di chuyển con trỏ hoặc phát input chuột thật.
  Nhập text dùng keyboard event sau khi ô đã được double-click để FC Online nhận
  đúng focus của ô nhập.
- Player Insert dùng click bằng Windows message để không di chuyển/chiếm con trỏ thật;
  Auto Buy, Player Insert và Upgrade đều sử dụng Windows message để chạy nền
  và không chiếm con trỏ người dùng. Các thao tác của chính quy trình Upgrade
  (chọn cầu thủ, đổi tab, bấm Tiếp/Nâng cấp) cũng đi qua `_upgrade_click`.
- `interruptible_sleep` / `random_sleep`: ngủ an toàn, có thể được dừng ngay khi `stop_event` được set.
- `run_auto_buy` truyền `stop_event` xuyên suốt bước thiết lập/xác minh filter
  và vòng mua phôi. Event lưu theo thread để các lần Auto Buy đồng thời không
  ghi đè tín hiệu dừng của nhau.
- `play_notification`: phát âm thanh khi mua thành công hoặc cảnh báo.

Cấu hình và template chính:

```python
TEMPLATE_DIR = resource_path("templates")
BUY_BULK = resource_path(os.path.join("templates", "buy_bulk.png"))
CONFIRM_BULK = resource_path(os.path.join("templates", "confirm_bulk.png"))
FAILURE = resource_path(os.path.join("templates", "fco_failure_title.png"))
RECEIVE_NOW = resource_path(os.path.join("templates", "receive_now.png"))
CONFIRM_RECEIVED = resource_path(os.path.join("templates", "confirm_received.png"))
```

Đây là layer nền để các flow khác không cần biết chi tiết của Win32 API hay OCR.

### 4.3 player_insert.py

`player_insert.py` là layer logic nghiệp vụ cho hành vi mua theo reset và thứ tự queue.

Trách nhiệm:

- `normalize_reset_code(reset_code)`: chuẩn hóa mã reset như `c50` / `l32`.
- `sort_entries_by_reset(entries, now=None)`: sắp xếp hàng đợi theo reset gần nhất.
- `run_player_insert(hwnd, entries, stop_event, log_callback)`: chạy state machine mua theo reset.
- Player Insert lặp liên tục qua toàn bộ hàng đợi; sau khi chèn thành công,
  đánh dấu reset hiện tại đã xử lý và chờ reset kế tiếp thay vì tự dừng.
- Tạo GIF khi mua thành công, lưu ảnh debug OCR của giá max vào `ocr_debug/`.
- Quản lý cửa sổ reset và giá ghi nhớ `reset_base_price` cho từng reset.

Các hằng số ảnh hưởng đáng chú ý:

```python
REFERENCE_WIDTH = 1280
REFERENCE_HEIGHT = 752
PLAYER_ROW_X = 430
PLAYER_ROW_FIRST_Y = 230
PLAYER_ROW_STEP_Y = 32
BUY_PLAYER_X = 875
BUY_PLAYER_Y = 672
MAX_PRICE_ROIS = ((950, 312, 1070, 347), (935, 305, 1090, 350))
MAX_PRICE_X = 1000
MAX_PRICE_Y = 332
QUANTITY_X = 995
QUANTITY_Y = 460
GIF_CAPTURE_INTERVAL = 0.20
POST_POPUP_CAPTURE_DURATION = 2.0
POPUP_CLOSE_TIMEOUT = 10.0
POPUP_CLOSE_STABLE_READS = 2
```

Logic này dựa trên layout chuẩn 1280x752 và scale theo cửa sổ thực tế.

## 5. Luồng Auto Buy

### 5.1 Mục tiêu

Auto Buy chọn cầu thủ dựa trên:

- `stat_min`
- `stat_max`
- `max_card_price`
- số lượng mục tiêu `target_players`

### 5.2 Flow cơ bản

```text
START
  |
  v
find FC ONLINE
  |
  v
scan / filter candidate players
  |
  v
check stat range and price threshold
  |
  v
buy / confirm / repeat until target met or stop requested
```

`app.py` gọi `run_auto_buy(...)` trong `bot_worker`, sau khi đã xây sẵn `stop_event` và callback log.

### 5.3 State được dùng trong auto flow

- Window xác thực: cửa sổ FC Online phải hợp lệ và tồn tại.
- Template matching: xác định nút mua bulk / confirm / failure / receive now.
- Capture + OCR: nếu cần đọc giá hoặc xác nhận popup.
- Sử dụng user profile từ `profiles.json`.

## 6. Luồng Player Insert

### 6.1 Khái niệm dữ liệu

Mỗi item `entries` có dạng tương tự:

```python
{
    "position": 5,
    "reset": "l32",
    "quantity": 10,
}
```

- `position`: dòng cầu thủ trong danh sách game.
- `reset`: mã reset cần xử lý (`c50`, `l32`, ...).
- `quantity`: số lượng mua cần đặt cho lần đó.

### 6.2 Sắp xếp hàng đợi

`sort_entries_by_reset()` giữ nguyên giá trị `position` nhưng sắp xếp lại thứ tự xử lý theo reset gần nhất.

```python
sorted_entries = sort_entries_by_reset(entries)
```

Điều quan trọng:

- Chỉ sort tối đa một lần khi bắt đầu `START`.
- Sau đó không quét lại toàn bộ hàng đợi.
- `THỨ TỰ` là thứ tự đang chạy, `VỊ TRÍ` là vị trí game thực.

### 6.3 State machine của Player Insert

```text
START
  |
  v
SORT_QUEUE
  |

SELECT_CURRENT_PLAYER
  |
  v
WAIT_FOR_RESET
  |
  v
READ_BASE_PRICE_ONCE
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
  +--> if equal => ESC and retry
  |
  +--> if changed => click max price row, buy/quantity if needed
  |
  +--> if popup closes => confirm success; notify; save GIF
  |
  +--> if timeout => warning; keep current item for retry
  |
  +--> when reset ends => next queue item
```

### 6.4 Cửa sổ reset

`player_insert.py` định nghĩa cửa sổ xử lý theo thời gian reset:

```text
reset_at <= now < reset_at + 20 minutes
current_second < 15
```

Điều này bảo đảm:

- không thử quá sớm
- không vượt quá 20 phút của reset
- không đọc giá hoặc mua khi popup cũ chưa đóng

### 6.5 Ghi nhớ giá theo reset

```text
reset_base_price = current_price đọc được ở đầu cửa sổ reset
```

Trong suốt reset đó, `reset_base_price` không đổi. Khi reset mới bắt đầu, mới thay giá mốc mới. Đây là chiến lược chính để tránh mua nhầm do giá đang cao hoặc đã tăng lên rồi lại giảm.

## 6.5 Luồng Đập cầu thủ (Upgrade)

Ngoài Auto Buy và Player Insert, `app.py` còn có luồng Đập cầu thủ
(`open_upgrade`) dùng để nâng cấp thẻ cầu thủ lên mức mục tiêu
(`target_level`, ví dụ +5).

Thiết kế lọc phôi:

- Luồng chỉ kiểm tra OVR của từng dòng phôi thuộc phạm vi
  `stat_min` - `stat_max` do người dùng nhập.
- KHÔNG check cấp thẻ khi chọn phôi: hàm `_select_player_in_stat_range`
  không còn tham số `allowed_card_levels` và không đọc badge cấp thẻ
  (`_read_player_card_level`) khi quét danh sách.
- UI không còn khối "MỨC THẺ ĐẬP (CHỌN NHIỀU)"; profile `upgrade`
  trong `profiles.json` chỉ còn `stat_min`, `stat_max`, `target_level`,
  `purchase_quantity`, `purchase_price`.
- Nút START bị disable trong suốt thời gian worker Upgrade chạy và được bật
  lại khi worker kết thúc, kể cả khi dừng do STOP hoặc lỗi.
- Ngay sau START, tool đọc cấp thẻ hiện tại trên badge của cầu thủ đang chọn.
  Nếu OCR không đọc được thì dừng an toàn; nếu cấp hiện tại đã đạt/vượt mục tiêu
  thì dừng mà không bắt đầu đập.
- Đọc kết quả sau khi quay lại danh sách vẫn dùng `_read_owned_card_level` để
  so với `target_level` và quyết định dừng hay đập tiếp nếu OCR đọc được. Nếu
  OCR không đọc được, luồng tiếp tục chọn phôi; việc bấm Tiếp chỉ dựa vào trạng
  thái hiển thị của nút Tiếp. Cấp thẻ đạt hoặc vượt mục tiêu thì dừng tool.
- Khi thiếu phôi, Upgrade gọi `run_auto_buy` với cùng `upgrade_stop_event`;
  STOP hủy các bước chờ, nhập/xác minh filter và ngăn click mua/xác nhận tiếp
  theo. Sau khi dừng mua, Upgrade không quay lại quy trình đập.

Mỗi vòng đập (`run_upgrade_cycle`):

1. Ngay sau khi nhấn START, đọc cấp thẻ badge hiện tại; nếu không đọc được thì
   dừng an toàn, nếu đã đạt/vượt `target_level` thì dừng và không đập tiếp.
2. Sắp xếp danh sách OVR tăng dần (`_sort_ovr_ascending`).
3. Chọn phôi có OVR trong phạm vi MIN/MAX (bỏ qua cấp thẻ).
4. Nếu thiếu phôi => chuyển tab Mua hàng loạt, mua bằng `run_auto_buy`
   rồi quay lại tab Cầu thủ đang sở hữu.
5. Bấm Tiếp => Nâng cấp => Bỏ qua (Space); chờ nút Tiếp màu xanh ổn định rồi
   bấm một lần. Sau đó kiểm tra lại nút: nếu vẫn hiện ổn định thì bấm thêm một
   lần; nếu nút biến mất thì tiếp tục đọc mức thẻ ở danh sách cầu thủ. Nếu nút
   vẫn còn sau hai lần bấm thì dừng để tránh thao tác lên màn hình chưa xác nhận.
   Không dùng OCR cấp thẻ trên màn hình kết quả để quyết định số lần bấm Tiếp.
6. Nếu đạt/vượt `target_level` => dừng; ngược lại lặp lại từ bước 2.

## 7. OCR và template matching

### 7.1 Template matching

`auto_buy.py` dùng OpenCV để tìm các nút xác nhận trên màn hình:

- `buy_bulk.png`
- `confirm_bulk.png`
- `confirm_bulk_bold.png`
- `fco_failure_title.png`
- `receive_now.png`
- `confirm_received.png`

Mục đích là tìm đúng đối tượng UI dù kích thước hay vị trí thay đổi theo window state.

### 7.2 OCR

OCR được dùng để đọc giá, số lượng cầu thủ trong popup mua và cấp thẻ sở hữu.
Detector cấp thẻ chỉ đọc badge ở góc dưới bên trái thẻ cầu thủ đang chọn:

```python
OWNED_CARD_LEVEL_ROI = (199, 257, 234, 281)
```

ROI tính theo layout tham chiếu `1280x752` và được scale theo ảnh chụp cửa sổ.
Ảnh được chuyển xám, threshold, phóng to theo nhiều tỷ lệ và padding nền
đen/trắng trước khi thử nhiều PSM Tesseract (`10`, `8`, `7`, `13`); chấp nhận
kết quả khi ít nhất hai lần đọc đồng thuận trong khoảng cấp hợp lệ.

### 7.3 OCR cho giá

`player_insert.py` dùng ROI cố định theo layout chuẩn 1280x752 để đọc dòng `Giá tối đa`:

```python
MAX_PRICE_ROIS = (
    (950, 312, 1070, 347),
    (935, 305, 1090, 350),
)
```

Nếu OCR trả về giá có format như:

- `18.3M`
- `20.1M`
- `1.2B`

hệ thống chuyển về dạng số nguyên như 18,300,000 / 1,200,000,000.

Nếu cùng ROI có nhiều kết quả không đồng nhất, hệ thống ưu tiên giá có số lượng đồng thuận cao nhất, không chọn giá lớn nhất một cách ngẫu nhiên.

## 8. Logging và debug

### 8.1 Log UI

`app.py` cung cấp `add_log()` để ghi vào text widget của giao diện. Tất cả worker gửi log qua callback. Không cập nhật trực tiếp widget từ thread worker.

### 8.2 Debug ảnh

Khi cần kiểm tra, `player_insert.py` lưu hình ảnh vào:

- `ocr_debug/`: crop quanh click max-price và ảnh ROI đã đánh dấu
- `purchase_gifs/`: lưu GIF từ khi đổi giá đến khi popup đóng

Cơ chế này rất có ích để gỡ lỗi khi OCR sai hoặc layout bị lệch.

## 9. Threading và an toàn

### 9.1 Main thread

Main thread chịu trách nhiệm:

- UI
- profile loading
- trigger bot
- log rendering
- periodic checks

### 9.2 Worker thread

Worker thread thực hiện phần automation nặng:

- click
- capture
- OCR
- loop mua/nhập
- save GIF
- check timeout

### 9.3 Dừng an toàn

`stop_event` là cơ chế dừng của Auto Buy, Player Insert và Upgrade. Mọi vòng
chờ, bước nhập filter, và logic click đều phải kiểm tra:

```python
stop_event.is_set()
```

Khi `STOP` được nhấn:

```text
set stop_event
worker thoát tại điểm an toàn
không tiếp tục click / nhập / OCR
```

Trong lúc Upgrade mua phôi, event được truyền xuống `run_auto_buy`, bao gồm
bước chờ cửa sổ, đọc/nhập/xác minh filter và vòng xử lý popup mua. Khi nhận
STOP, worker bỏ qua các bước tiếp theo và không bắt đầu click mua hoặc xác
nhận mới; một thao tác capture/OCR hoặc lệnh Windows đang thực thi có thể hoàn
tất trước khi worker thoát. Các khoảng chờ dùng event để thức dậy ngay khi
STOP được set.

## 10. Error handling

Các lỗi chính cần được xử lý:

- Không tìm thấy `FC ONLINE`: log rõ, không khởi tạo worker.
- Popup cũ chưa đóng: `press ESC` và chờ xác nhận trước khi click vị trí mới.
- OCR không đọc được giá: cảnh báo, `ESC`, giữ nguyên cầu thủ, retry ở vòng sau.
- Popup không mở: không click mua tiếp, retry ở vòng kế tiếp.
- STOP được nhấn: dừng ngay, không mua tiếp.

## 11. Dữ liệu và trạng thái

### 11.1 Profile

`%LOCALAPPDATA%\AutoFCO\profiles.json` lưu dữ liệu cấu hình Auto Buy theo tên profile:

```json
{
  "124": {
    "stat_min": 124,
    "stat_max": 124,
    "max_card_price": 12000,
    "usage_count": 36
  }
}
```

### 11.2 Runtime state

Hệ thống duy trì trạng thái động bằng biến toàn cục trong `app.py` như:

- `current_hwnd`: cửa sổ FC Online đang được chọn
- `is_running`: trạng thái bot đang chạy
- `start_time`: thời gian bắt đầu UI timer
- `stop_event`: biên báo dừng toàn bộ automation

## 12. Ràng buộc thiết kế quan trọng

- UI không được trực tiếp click game; chỉ worker mới thao tác với window.
- Auto Buy và Player Insert có thể chạy riêng lẻ trong cùng app nhưng tách rõ module logic.
- Toàn bộ thao tác nên được dựa trên trạng thái thực tế của cửa sổ và không dựa vào suy diễn từ giờ hệ thống chỉ.
- Tất cả module đều phải xử lý `stop_event` và retry an toàn.
- Khi layout thay đổi, cần cập nhật ROI / template tương ứng thay vì sửa logic nghiệp vụ quá sâu.

## 13. Phát hành và cập nhật

- `APP_VERSION` trong `app.py` phải khớp với tag GitHub Release dạng `vX.Y.Z`.
- Build executable bằng lệnh PyInstaller ở đầu tài liệu, sau đó tạo SHA-256:

  ```powershell
  $hash = (Get-FileHash .\dist\AutoFCO.exe -Algorithm SHA256).Hash
  "$hash  AutoFCO.exe" | Set-Content .\dist\AutoFCO.exe.sha256 -Encoding ascii
  ```

- Tạo GitHub Release mới và đính kèm đúng hai asset `AutoFCO.exe` và
  `AutoFCO.exe.sha256`. Ứng dụng kiểm tra `releases/latest`, xác minh SHA-256,
  rồi dùng tiến trình PowerShell riêng để thay executable sau khi ứng dụng thoát.
- Người dùng xác nhận cập nhật trong hộp thoại; không bắt đầu cài cập nhật khi
  Auto Buy, Player Insert hoặc luồng đập cầu thủ đang chạy.
- Chỉ các bản PyInstaller (`--onefile`) tự kiểm tra cập nhật; chạy từ mã nguồn
  thì bỏ qua bước này.
- Bản cũ chưa có updater phải được cài thủ công một lần; sau đó mới tự nhận
  được các release mới.

## 14. Files liên quan

```text
app.py
    UI, window detection, profile management, bot lifecycle, log dispatch

auto_buy.py
    Win32 input, capture, template matching, OCR support, notification

player_insert.py
    queue sorting, reset timing, max-price parsing, buy flow, GIF output

%LOCALAPPDATA%\AutoFCO\profiles.json
    user profile storage for Auto Buy, persists across releases

templates/
    image models for game UI recognition

purchase_gifs/
    GIF for successful purchases

ocr_debug/
    debug screenshots of value reads and click points
```

## 15. Checklist kiểm thử

- [ ] Tìm đúng cửa sổ FC ONLINE khi khởi động.
- [ ] UI cập nhật log và trạng thái không block bởi worker.
- [ ] Auto Buy lọc đúng cầu thủ theo stat và giá.
- [ ] Player Insert sort hàng chờ đúng reset gần nhất.
- [ ] `position` và `reset` được giữ đúng trong khi sort.
- [ ] Popup cũ được đóng trước khi chọn vị trí mới.
- [ ] Giá `20.1M` được đọc đúng, không đọc nhầm `Giá cầu thủ` hay `Giá nhập`.
- [ ] Khi giá không đổi, bot nhấn `ESC` và không mua.
- [ ] Khi giá đổi, bot click đúng dòng giá tối đa và mua đúng số lượng.
- [ ] GIF được lưu chỉ với lần mua có giá thay đổi.
- [ ] Cấp thẻ sở hữu được OCR tại badge góc dưới trái của thẻ được chọn.
- [ ] Khi START, cấp thẻ ban đầu được đọc trước lần đập đầu; OCR lỗi thì dừng
      an toàn, cấp đã đạt/vượt mục tiêu thì không đập tiếp.
- [ ] Nút `Tiếp` được phát hiện ổn định dù vị trí contour dao động nhẹ giữa
      các frame animation.
- [ ] Sau khi bấm `Tiếp theo`, nếu popup xác nhận nâng cấp xuất hiện thì
      tool tick tùy chọn không hiển thị lại trong lần đăng nhập này và bấm
      `Tiến hành`; nếu popup không xuất hiện thì tiếp tục bình thường.
- [ ] Đập cầu thủ chỉ chọn phôi theo OVR MIN/MAX, không lọc theo cấp thẻ.
- [ ] Luồng đập dừng khi mức thẻ đạt/vượt `target_level`.
- [ ] START bị disable khi worker Upgrade đang chạy và bật lại sau khi worker
      thoát do STOP, lỗi hoặc hoàn tất.
- [ ] `STOP` trong lúc mua phôi dừng các bước nhập filter/chờ popup và không
      phát sinh click mua hoặc xác nhận tiếp theo.
- [ ] OCR debug và log hiển thị rõ ràng khi lỗi xảy ra.