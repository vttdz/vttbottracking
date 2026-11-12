# 📦 Express Tracking Bot (Bản Rút Gọn Siêu Nhẹ)

Hệ thống tra cứu và tự động theo dõi đơn hàng từ **SPX Express**, **GHN** (Giao Hàng Nhanh), và **J&T Express** qua **Telegram Bot**, tích hợp tính năng **quản lý và check thông tin tài khoản Shopee qua Cookie**.

- Chạy trực tiếp 100% bằng Python có sẵn trên máy.
- Không cần cài Node.js, không cần máy chủ MySQL, không cần Docker.
- Dữ liệu lưu trong file SQLite nhẹ tênh (`tele_tracking.db`).

---

## 🚀 Cách Chạy Bot Telegram

Chỉ cần **nhấp đúp chuột vào file**:
👉 **`Chay_Bot_Telegram.bat`**

Bot sẽ tự động kết nối và chạy ngầm trên máy tính của bạn.

### Các lệnh điều khiển trên Telegram:

#### 1. Tra cứu & Theo dõi đơn hàng:
* `/start` hoặc `/help` : Xem hướng dẫn
* **Thêm đơn theo dõi**:
  * Đơn SPX: `/add SPXVN063015366786 [ghi chú]`
  * Đơn GHN: `/add ghn GY8RA4EL 0987654321 [ghi chú]` *(kèm SĐT nhận)*
  * Đơn J&T: `/add jnt 862195772225 9613 [ghi chú]` *(kèm 4 số cuối SĐT)*
* `/list` : Xem danh sách đơn đang theo dõi
* `/check` : Quét kiểm tra trạng thái tất cả đơn ngay lập tức
* `/del <mã_vận_đơn>` : Xóa theo dõi đơn hàng

> 🔔 **Tự động thông báo**: Bot chạy nền và tự động quét mỗi 5 phút. Khi đơn hàng có mốc hành trình mới (đang giao, đến bưu cục, giao thành công...), bot sẽ tự động gửi tin nhắn báo cho bạn.

#### 2. Tiện ích Shopee qua Cookie:
* `/check_voucher <mã>` hoặc `/cv <mã>` : **Kiểm tra thông tin chi tiết voucher** (mức giảm, đơn tối thiểu, % đã dùng, trạng thái còn lượt hay hết lượt, hạn dùng, tên chương trình). *Hoặc bạn có thể nhắn tin trực tiếp `check <mã>` vào bot.*
* `/voucher <mã_voucher>` : Lưu mã giảm giá vào ví Shopee (hỗ trợ nhập cùng lúc nhiều mã, vd: `/voucher MA1 MA2 MA3`).
* `/shopee <cookie>` : Kiểm tra thông tin tài khoản Shopee (Username, Nickname, SĐT, Email, Mật khẩu, Số lượng Voucher trong ví, Đơn đang giao). Cookie sẽ được tự động lưu cho các lần sau!
* `/shopee` : Xem lại thông tin tài khoản Shopee bằng Cookie đã lưu.
* `/shopee_orders` : Liệt kê tất cả các đơn hàng Shopee đang giao của bạn và **tự động thêm các mã vận đơn SPX vào hệ thống theo dõi của bot** mà không cần nhập tay!
* `/shopee_del` : Xóa Cookie Shopee đã lưu khỏi bot.

---

## 🌐 Công Cụ Bổ Trợ (Tùy Chọn)

* **Giao diện Web máy tính**: Nhấp đúp vào file **`Chay_Truc_Tiep.bat`** để mở web tra cứu trên trình duyệt (`http://localhost:8080`) — có sẵn tab **Lưu & Check Voucher Shopee** tiện lợi.
* **Tra cứu nhanh đơn hàng qua CLI**:
  ```bash
  python tra_cuu_nhanh.py SPXVN063015366786
  ```
* **Kiểm tra voucher qua dòng lệnh (CLI)**:
  ```bash
  python shopee_tool.py check_voucher "<cookie>" NEWUSERIZ6YN
  ```
* **Lưu mã voucher Shopee qua dòng lệnh (CLI)**:
  ```bash
  python shopee_tool.py voucher "<cookie>" MA_VOUCHER_1 MA_VOUCHER_2
  ```

---

## 📁 Cấu Trúc Thư Mục

* `bot_tele.py` : Mã nguồn Telegram Bot
* `shopee_tool.py` : Module kiểm tra tài khoản Shopee & đồng bộ đơn hàng qua Cookie
* `tracking_core.py` : Module tra cứu API SPX, GHN, J&T
* `Chay_Bot_Telegram.bat` : File nhấp đúp để chạy Bot Telegram
* `Chay_Truc_Tiep.bat` : File nhấp đúp để mở Giao diện Web (tùy chọn)
* `tra_cuu_nhanh.py` : Công cụ tra cứu dòng lệnh nhanh
* `tele_tracking.db` : Cơ sở dữ liệu SQLite lưu đơn hàng và cài đặt
* `.env` : File cấu hình Telegram Bot Token
