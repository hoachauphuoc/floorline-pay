# Floorline Pay: kế hoạch nâng ảnh hưởng của AI lên phần PayPal

Lập ngày 6/11/2026. Hạn nộp 12:00 PT 12/11/2026. Đề xuất chốt code trước hết ngày 10/11 để còn thời gian quay video và kiểm tra sau deploy.

## 1. Hiện trạng: AI đang ảnh hưởng tới đâu

| Chỗ yếu | Bằng chứng trong repo | Hệ quả |
|---|---|---|
| Analyst không có thông tin mới | `payment_ai.features()` đưa đúng các dữ kiện mà luật đã xét (tỷ lệ tiền, đổi payee, khớp registry). README: chạy thật ngày 5/11, 32/36 gian lận, 6/44 báo nhầm, trùng hệt carrier look-up | AI không thêm gì so với luật |
| Bỏ sót carrier bị mạo danh | Cả 4 ca `impersonated_carrier_free_mail` lọt: web tìm thấy công ty thật nên look-up báo "found", không ai so payee với website đó | Đúng kiểu gian lận tốn tiền nhất lại không bắt được |
| Sàng lọc tin nhắn đổi payee chỉ là regex tiếng Anh | `change_screen._PHRASES` cộng Model Armor; không có mô hình đọc nội dung | Viết lại câu chữ hoặc viết tiếng Việt là qua |
| AI chỉ chạy một lần | `orchestrator` gọi `payment_ai.assess` lúc phân tích. Sự kiện PayPal về sau chỉ kích hoạt freeze cố định trong `paypal_webhook._apply` | Dispute, refund, payout bị trả không làm AI xem lại các ca đang chờ |
| Người duyệt ít thấy AI | `PaymentCard` không hiện nhận định của analyst; nó chỉ xuất hiện dưới dạng một finding | Giám khảo và người duyệt khó thấy AI làm gì |
| AI không khởi động giao dịch nào | README: ca auto-clear không bắt đầu thanh toán, chỉ quyết định của người mới có | Lệch với chủ đề "AI agents that actually transact" |

## 2. Mục tiêu đo được

- Bắt được ít nhất 3/4 kiểu gian lận mà analyst hiện bỏ sót, trên bộ dữ liệu **chưa dùng để thiết kế**, và báo nhầm không tăng quá 5 điểm phần trăm.
- Bảng "mỗi lớp thêm được gì" trên `/defense` và README, kể cả lớp không thêm được gì.
- Một sự kiện PayPal làm AI xem lại ca liên quan, thấy được trong video.
- Người duyệt đọc được lý do của AI ngay trên thẻ thanh toán.

## 3. Ràng buộc không đổi

- Mô hình chỉ nâng rủi ro hoặc siết hạn mức; chỉ người mới nới lỏng (bất biến 1 và 7).
- Settler không gọi mô hình, chỉ đọc những gì đã lưu trên case. Tiền chỉ di chuyển trong settler.
- Mọi nhánh mới fail closed. Mô hình không gọi được thì không thêm finding và không bỏ finding nào.
- Nội dung từ web, tin nhắn và PayPal là dữ liệu không tin cậy. Vì chỉ nâng được rủi ro, prompt injection tệ nhất chỉ gây giữ tiền thừa, không gây chi tiền.
- Mô hình không thấy tên, địa chỉ hay email, trừ tin nhắn đổi payee ở AI2 (xem mục đó).
- Không đụng `vf-app`. Không tự deploy. Việc tạo giao dịch Sandbox do bạn làm.

## 4. Các đợt việc (theo thứ tự ưu tiên)

Mỗi hành vi là một cặp task `<ID>t` (đỏ) rồi `<ID>c` (xanh) qua runner, như đợt trước.

### AI0. Bộ đo mới, làm trước tiên (6/11)
- Corpus hiện tại được viết từ chính các mẫu mà luật kiểm tra, nên không chứng minh được gì. Thêm `data/payment_fraud_holdout.json` và mở rộng `scripts/build_payment_corpus.py`, `payment_eval.py`.
- Bổ sung những ca khó: carrier thật có website nhưng nhận tiền bằng Gmail (hợp lệ), domain gần giống (`lagoon-freight.co` so với `lagoonfreight.com`), tin BEC viết lại hoặc tiếng Việt, tin đổi tài khoản thật có chữ "gấp", payee dùng chung cho nhiều carrier.
- Chốt số nền trước khi sửa AI1 đến AI3.

### AI1. So payee với website thật của carrier (6-7/11)
- `payment_ai.identity`: trong các kết quả nêu tên carrier, chọn kết quả có domain giống tên carrier; so với domain của payee. Lọc trang danh bạ, LinkedIn và tương tự, nếu không sẽ báo nhầm nhiều với dữ liệu web thật.
- Trạng thái mới `payee_off_site`, thêm finding `PAYEE_NOT_ON_CARRIER_DOMAIN` trong `payment_checks.check_payment_ai`.
- Thử nhanh trên corpus cũ: bắt 4/4 ca mạo danh, thêm 6/6 ca `payee_mismatch`, 0/44 báo nhầm. Con số 0 chưa đáng tin vì corpus không có carrier thật dùng Gmail; AI0 sẽ đo lại.
- Đây là lợi ích lớn nhất với chi phí nhỏ nhất, và không cần gọi thêm mô hình.

### AI2. Mô hình đọc tin nhắn đổi payee (7/11)
- `change_screen.screen`: thêm một lượt mô hình, đọc được nhiều ngôn ngữ, trả lý do. Chỉ thêm cờ; lỗi mô hình không thêm cờ, regex vẫn chạy như cũ.
- Tin nhắn có thể chứa tên người, nên đây là ngoại lệ duy nhất của quy tắc không gửi tên: bỏ email và số tài khoản trước khi gửi, không lưu văn bản (như hiện nay), chỉ lưu lý do.
- Tính token vào chi phí giống RV12.

### AI3. Cho analyst dữ kiện luật không có (8/11)
- `payment_ai.features` thêm: tiền so với các lần trả trước của chính carrier, tỷ lệ dispute hoặc payout bị trả của carrier, payee này có dùng cho carrier khác trong tenant không, số ngày từ lúc đặt tới lúc trả. Vẫn chỉ là số và phân loại.
- Quy tắc dừng: nếu trên bộ AI0 analyst vẫn không thêm được gì, giữ nguyên câu thừa nhận trong README, không tô vẽ.

### AI4. Thẻ "AI nói gì" cho người duyệt (8/11)
- `PaymentCard` và `/settlement`: hiện nhận định analyst, độ tin cậy, kết quả so payee, hạng tín dụng AI đề xuất, và việc cần làm ("gọi lại số trên website carrier"). Chỉ đọc dữ liệu đã lưu, không gọi mô hình thêm.
- `/defense`: thêm bảng từng lớp từ kết quả AI0.

### AI5. AI soạn hồ sơ dispute (9/11)
- Khi có `CUSTOMER.DISPUTE.CREATED`, agent lấy `paypal_client.get_dispute` cùng tracking, POD, phê duyệt, audit, rồi soạn bản nháp lý lẽ và danh sách chứng từ còn thiếu.
- Chỉ là bản nháp cho người đọc; không gửi gì lên PayPal, không chạm tiền. Chạy ngoài luồng webhook để webhook vẫn trả lời nhanh.
- Giới hạn cần nói rõ: README ghi webhook chưa từng thấy dispute thật. Cần bạn tạo một dispute trên Sandbox, nếu không thì chỉ demo bằng sự kiện ghi sẵn và phải ghi rõ như vậy.

### AI6. Sự kiện PayPal làm AI xem lại ca đang chờ (9/11, cắt đầu tiên nếu trễ)
- Sự kiện refund, dispute, payout bị trả đưa các ca đang mở của cùng carrier, shipper hoặc payee vào hàng xem lại; `payment_ai.assess` chạy lại trong runtime phân tích và chỉ có thể nâng rủi ro.
- Settler chỉ thêm một kiểm tra đọc: payee đã đổi sau lần đánh giá cuối thì `HELD`, yêu cầu phân tích lại. Đây là đường release, cần test đầy đủ và bạn đọc kỹ diff.

### AI7. Agent tự chi trả ca sạch (đã làm, xem mục 4c)
- Mặc định tắt. Chỉ chạy khi một người đã công bố giới hạn agent. Điều kiện: không có finding, AI không nâng rủi ro, payee so khớp website, carrier hạng A, dưới giới hạn agent, mandate hợp lệ, không có freeze. Thiếu bất kỳ điều kiện nào là chuyển cho người.
- Đây là thay đổi lớn nhất về độ ảnh hưởng và cũng rủi ro nhất: AI lần đầu khởi động dòng tiền, dù không hạ được rủi ro.
- Làm ngay sau AI1 và AI2, vì nó dùng kết quả so payee làm điều kiện. Vì giải chấm theo chủ đề AI, đây là việc ưu tiên chứ không phải việc làm thêm.

## 4b. Kết quả đã đo (6/11)

| Bộ dữ liệu | Luật + look-up | + so payee với website (AI1) |
|---|---|---|
| Corpus cũ | 32/36 gian lận, 6/44 báo nhầm | 36/36, 6/44 |
| Holdout (AI0) | 0/36, 6/44 | 12/36, 16/44 |

- Bảng trên đo không gọi mô hình. Chạy thật một lần trên holdout (6/11, Nemotron 3 Nano, cộng dồn từng lớp): luật + look-up 0/36, 6/44; + analyst 9/36, 11/44; + so website 16/36, 18/44; + reader 26/36, 18/44 (reader chạy lại sau khi nâng giới hạn token lên 2.000). Reader bắt 10/10 tin lừa đổi tài khoản mà không luật nào bắt, và không báo nhầm yêu cầu thật nào trong 12 ca. Kết quả lưu ở `analyst_holdout.json`, `reader_holdout.json`; `--replay` và `--replay-reader` in lại báo cáo mà không tốn token.
- Một câu của analyst (`F-LOOKALIKE-PAYEE-DOMAIN-003`) bị cắt ở giới hạn 800 token nên tính là không gắn cờ. Lần chạy reader đầu cắt một câu (`F-BEC-PARAPHRASE-026`) ở giới hạn 300 token, và đó là ca BEC duy nhất sót; sau khi nâng giới hạn lên 2.000 và chạy lại, không câu nào bị cắt (dài nhất 526 token) và reader bắt đủ 10/10.
- Mục tiêu "báo nhầm tăng không quá 5 điểm" **không đạt** trên holdout: tăng 27,3 điểm (12 ca). Đó là carrier thật nhận tiền qua Gmail hoặc domain công ty mẹ, về mặt dữ liệu không phân biệt được với mạo danh. Hậu quả là một cuộc gọi xác minh, không phải mất tiền. Không chỉnh bộ đo để đẹp số.
- Mục tiêu "bắt ít nhất 3/4 kiểu analyst bỏ sót" **không đạt**: trong 6 kiểu analyst không bắt được ca nào, các lớp khác bắt 3 (payee domain giả, BEC viết lại, BEC tiếng Việt). Ba kiểu còn lại vẫn 0/10: site clone, inflated booking, payee dùng chung.
- Holdout do cùng người viết trước khi làm AI1, nên vẫn là dữ liệu bịa, không phải kiểm định độc lập.
- `analyst.json` ở thư mục gốc không dùng được: cả 80 câu trả lời đều là "could not be reached".
- Lệnh đo thật (người có key chạy):

```bash
python scripts/evaluate_payment_ai.py --corpus data/payment_fraud_holdout.json --live-analyst --live-reader --save analyst_holdout.json --save-reader reader_holdout.json
```

## 4c. Thiết kế AI7 sau khi đọc code

- `settler.settle` hiện bỏ qua mọi decision không phải `RELEASED_BY_HUMAN` hoặc `BLOCKED_BY_HUMAN`. AI7 phải thêm đúng một đường mới, và đó là thay đổi trên đường chi tiền.
- Đường mới: `AUTO_CLEARED` dưới khóa `agent-<case>` chỉ được settle khi có một giới hạn agent do người đăng nhập mật khẩu công bố; mặc định tắt. `AUTO_CLEARED` dưới khóa khác hoặc không khóa vẫn không chi.
- Mọi kiểm tra hiện có trong `_release` vẫn chạy nguyên: pause, freeze, `check_payment`, hạn mức, tier cap, mandate, authorization. Thêm điều kiện đọc từ case: `payment_ai` có mặt, không có finding, `payee_site` là `on_site` hoặc payee đã được chứng minh, carrier hạng A, số tiền dưới giới hạn agent và dưới ngưỡng duyệt hai người.
- Thiếu hoặc không đọc được bất kỳ điều kiện nào thì ca chuyển cho người. Settler vẫn không gọi mô hình. Sau khi đã capture thì ca giữ khóa của agent và người retry, vì release của người sẽ capture dưới khóa mới và PayPal từ chối.
- Không ai release được ca `AUTO_CLEARED`, nên ca thanh toán sạch mà agent không được chi đi `HELD_FOR_REVIEW`, kể cả khi agent tắt. Luật lọc trước không còn tự clear ca thanh toán, chỉ còn có thể từ chối.
- Để agent chi thật cần thêm: boundary cho phép tự release (`BOUNDARY-v1` không cho), carrier hạng A, chủ hàng có lịch sử, và analyst trả lời normal với độ tin cậy từ 0,6.
- Audit ghi rõ người công bố giới hạn và lý do agent được chi.
- Cần sửa dòng mô tả trong CLAUDE.md ("chỉ sau một RELEASE đã công bố") và câu trong README ("auto-cleared không bắt đầu thanh toán").

## 5. Lịch và điểm cắt

| Ngày | Việc | Nếu trễ |
|---|---|---|
| 6-7/11 | AI0, AI1, AI2 | Không cắt: đây là phần có bằng chứng |
| 8/11 | AI7, AI4 | Không cắt: giải chấm theo chủ đề AI, đây là phần giám khảo thấy AI hành động |
| 9/11 | AI5, AI3, rồi AI6 | Cắt AI6, rồi AI3, rồi AI5 |
| 10/11 | Cổng kiểm tra tổng, README, CI, đo live lại | Dừng thêm tính năng |
| 11/11 | Deploy (bạn), quay video | |

Chi phí: AI1 không thêm lượt gọi mô hình; AI2 thêm một lượt cho mỗi tin đổi payee; AI5 một lượt cho mỗi dispute; AI6 một lượt cho mỗi ca xem lại. Tất cả đều phải tính vào ngân sách tenant.

## 6. Quyết định cần bạn

1. **AI7, agent tự chi trả ca sạch: làm hay không?** Đề xuất: làm dưới dạng tùy chọn mặc định tắt, chỉ khi các đợt trước xanh đúng hạn. Nếu không làm, câu chuyện nộp bài vẫn là "AI siết, người nới".
2. **Tạo dispute thật trên Sandbox cho AI5?** Cần bạn thao tác.
3. **Gửi tin nhắn đổi payee tới mô hình cho AI2** (tin có thể chứa tên người) có chấp nhận được không? Đề xuất: có, sau khi bỏ email và số tài khoản.

## 7. Ngoài phạm vi

PayPal Agent Toolkit (thêm phụ thuộc mà không thêm năng lực cần cho các việc trên); mô hình quyết định void hoặc capture; mô hình hạ rủi ro hay nới hạn mức.
