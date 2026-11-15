# listing-watcher

Bot theo dõi repo GitHub công khai và báo về Telegram khi có mã token mới xuất hiện trong pull request hoặc commit. Cấu hình mặc định theo dõi `BitGo/BitGoJS`, nơi BitGo (bên lưu ký) khai báo token mới dưới dạng `'chain:token'`.

Bot chỉ đọc dữ liệu công khai. Nó không giao dịch và không cần ví.

## Cần chuẩn bị

- Windows có Python 3.11 trở lên. Không cần cài thêm thư viện nào.
- Tất cả file đặt chung một thư mục.

## Cài đặt (khoảng 5 phút)

1. Trong Telegram, nhắn cho `@BotFather`, gõ `/newbot`, đặt tên. BotFather trả về một token.
2. Bấm đúp `setup.bat`. Dán token khi được hỏi, rồi nhắn một tin bất kỳ cho bot theo hướng dẫn trên màn hình. Chương trình tự tìm chat id, lưu vào file `.env`, gửi tin thử và chạy thử trên PR cashcat thật (#9357).
3. Bấm đúp `start.bat` để chạy bot. Đóng cửa sổ là bot dừng.

GitHub token không bắt buộc. Không có thì bot kiểm tra 3 phút một lần, có thì mỗi phút. Tạo ở GitHub → Settings → Developer settings → Personal access tokens, chỉ cần quyền đọc repo công khai.

File `.env` chứa token của bạn. Đừng gửi file này cho ai.

Lần chạy đầu, bot ghi nhận các PR đang mở làm mốc và không gửi cảnh báo. Từ đó trở đi, token mới sẽ được báo về Telegram. Muốn chạy 24/7, đặt bot trên một máy luôn bật (VPS giá rẻ là đủ).

Cài tay cũng được: đổi tên `env.example` thành `.env`, điền giá trị, rồi chạy `python watcher.py`.

## Chạy trên GitHub Actions (không cần bật máy)

GitHub chạy bot giúp bạn khoảng 5 phút một lần, miễn phí với repo công khai. Độ trễ thực tế thường từ 5 đến 15 phút.

1. Tạo một repo **công khai** trên GitHub và tải toàn bộ file trong thư mục này lên, kể cả thư mục `.github`. **Không tải file `.env` lên.**
2. Trong repo: Settings → Secrets and variables → Actions → New repository secret. Tạo hai secret:
   - `TELEGRAM_BOT_TOKEN`: token của bot.
   - `TELEGRAM_CHAT_ID`: số chat id (có trong file `.env` trên máy bạn).
3. Vào tab Actions, chọn workflow `listing-watcher`, bấm Run workflow để chạy lần đầu. Telegram sẽ nhận tin "Bắt đầu theo dõi".

Bot lưu những gì đã thấy vào `state.json` và tự cập nhật file này trong repo. Nếu một lần chạy bị lỗi, GitHub gửi email cho bạn. Không cần tạo GitHub token: Actions có sẵn.

Repo riêng tư cũng chạy được, nhưng hạn mức miễn phí (2.000 phút mỗi tháng) chỉ đủ cho khoảng 30 phút một lần. Khi đó sửa dòng `cron` trong `.github/workflows/watch.yml` thành `"7,37 * * * *"`.

Đã chạy trên GitHub thì tắt bot trên máy, nếu không bạn sẽ nhận mỗi cảnh báo hai lần.

## Tùy chỉnh (`config.toml`)

| Mục | Ý nghĩa |
|---|---|
| `watch_chains` | Chain được đánh dấu ƯU TIÊN CAO. Mặc định `hoodeth` (Robinhood Chain). |
| `alert_other_chains` | `false` để chỉ nhận tin về chain ưu tiên. |
| `path_globs` | Chỉ xét các file khớp mẫu này. |
| `identifier_regex` | Mẫu nhận diện mã token. |
| `poll_seconds` | Số giây giữa hai lần kiểm tra. |

Thêm repo khác bằng một khối `[[repos]]` mới. Mỗi repo khai báo token theo cách riêng, nên bạn cần xem vài PR cũ của repo đó để viết `path_globs` và `identifier_regex` cho đúng. Dùng `--replay` trên một PR cũ để kiểm tra.

## Kiểm thử

```
python -m unittest -v
```

## Giới hạn cần biết

- Tín hiệu này chỉ cho biết BitGo thêm hỗ trợ lưu ký cho một token. BitGo phục vụ nhiều khách hàng, nên đây không phải bằng chứng một sàn cụ thể sắp niêm yết.
- Riêng tín hiệu BitGo đã được nhiều người theo dõi sau vụ cashcat, nên lợi thế còn lại rất ít. Hãy coi bot này là khung để bạn gắn thêm nguồn tín hiệu của riêng mình.
- Nếu một PR sửa lại dòng chứa token đã có từ lâu, bot có thể báo nhầm là token mới.
- Bot xét 50 PR mở được cập nhật gần nhất và 20 commit mới nhất mỗi vòng.
