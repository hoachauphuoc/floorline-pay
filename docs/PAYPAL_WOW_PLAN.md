# Floorline Pay: kế hoạch nâng cấp để thắng PayPal AI Hackathon (còn ~20 ngày)

## Context

Floorline Pay đã có đủ vòng đời thanh toán và 7 lớp bảo mật tín dụng (PR #1, backend 2667 test, frontend 1855 test), nhưng
(1) chưa có cuộc gọi PayPal thật nào, (2) phần lớn giá trị nằm ở backend nên giám khảo không thấy, (3) giao diện là bản Nebius
cũ, (4) chưa có bản demo công khai cho giám khảo. Luật chính thức (Official Rules, bạn dán) và khảo sát Tavily (23/10) cho thấy
những điều sau quyết định kế hoạch:

**Luật ép buộc (phải làm, không phải "nên làm")**
- Hạn nộp **12/11/2026 12:00 PT**; sau đó không được sửa bài nộp. Chấm 1–15/12.
- Giám khảo phải **chạy hoặc tương tác được với bản chạy thật**: URL demo công khai hoặc hướng dẫn chạy đầy đủ + tài khoản test/khóa sandbox
  trong README; phải **miễn phí, không hạn chế, đến hết 15/12**. Mockup/prototype tĩnh không đạt.
- Repo **public** trên GitHub, giấy phép mở **hiện ở mục About**. Video **dưới 3 phút**, YouTube công khai, quay dự án chạy thật,
  **không logo bên thứ ba, không nhạc có bản quyền**. Mọi tài liệu bằng tiếng Anh.
- Dự án cũ phải được "significantly updated" sau 1/10 và **giải trình** (đã có phần trong README).
- Giám khảo có thể dùng **phân tích tự động bằng AI**: README phải có cấu trúc rõ, mỗi tuyên bố có bằng chứng.

**Cách chấm**: Vòng 1 đạt/không đạt (PayPal có là lõi không). Vòng 2: 5 tiêu chí **bằng trọng số**: Technological Implementation, Design,
Potential Impact, Innovation/Idea, Presentation. **Hòa điểm thì xét theo thứ tự tiêu chí đó, nên Technological Implementation (chạy thật,
không tầm thường) là tiêu chí quyết định.** Giải: Grand 12k/8k/5k; Honorable 5k mỗi giải (Most Creative, Most Impactful, **Best Demo Delivery**,
Best Use of PayPal + AI, Best Use of Agentic Commerce); sponsor (AG Grid 5k/2k/1k×3, APIMatic, Bryntum, Channel3, Render credit). Một dự án
thắng tối đa **một Grand hoặc Honorable cộng một sponsor**.

**Đối thủ**: có entry cùng mẫu ký quỹ (authorize→capture/payout/void + "AI judge", ví dụ `judgepay-paypal`), tự nhận chưa nhận webhook và chưa chạy thật.
Vòng đời PayPal riêng nó không còn là khác biệt. Khác biệt của ta: **bảo mật tín dụng đo được, bằng chứng chạy thật, và khả năng nhìn thấy chúng.**

**Quyết định của bạn**: hosting trên **Render** ($50 credit của cuộc thi, deploy Docker từ GitHub qua dashboard), sponsor **AG Grid Community** (MIT, không watermark),
phạm vi **cốt lõi + MCP và PayPal Agent Toolkit** (đã đồng ý thêm dependency).

## Nguyên tắc

- Master lập kế hoạch và review; Haiku sửa code qua runner như đã làm ở L4/L5/L7 (công thức trong `orchestrator/HANDOFF.md`). Mỗi lớp xanh thì commit một dòng + push.
- Mọi con số trong README do một lần chạy thật cho ra. Không nói "PayPal hỗ trợ AP2 API" (PayPal mô tả đó là năng lực dự kiến): chỉ nói "mandate theo tinh thần AP2, do Floorline ký và kiểm tra".
- Bất biến 7 giữ nguyên: mô hình chỉ nâng rủi ro/siết hạn mức; settler không gọi mô hình; mọi công cụ MCP/Toolkit là **chỉ-đọc hoặc "đề nghị"**, không có công cụ nào di chuyển tiền.
- Không đụng `vf-app`. Không bao giờ ghi khóa (Tavily, PayPal, Nebius) vào repo; khóa chỉ nằm ở biến môi trường của Render.

## Hạng mục (theo thứ tự thực hiện)

### W0. Dọn nhanh (ngày 0)
- `src/vf_logistics/payment_eval.py:4`: docstring còn ghi "84 payouts, 40 fraudulent" (corpus thật: 80, 36). Sửa một dòng.
- Lưu kế hoạch này vào `docs/PAYPAL_WOW_PLAN.md` (thay bản cũ).

### W1. Bằng chứng chạy thật trên sandbox (Technological Implementation; **cần khóa của bạn**)
- Mới: `scripts/sandbox_e2e.py` dùng `paypal_client.py` (create_order_for_approval → bạn duyệt trên trang sandbox → authorize_order → `payment_binding.record_authorization`
  → `settler.settle` với quyết định release → `get_payout_batch` → void ở nhánh block). In ra chỉ id và trạng thái, không bao giờ in khóa.
- Lưu **payload thật đã làm sạch** vào `tests/fixtures/paypal/` (order, authorization, capture, payout batch, webhook payout item, dispute) và thêm test đọc các fixture đó.
  Kết quả chốt các giả định chưa kiểm: link `payer-action`, `reauthorize` giữ id, `sender_batch_id` trong webhook payout, `seller_transaction_id` trong dispute.
- Sửa `paypal_webhook.py` / `payout_tracking.py` theo payload thật.

### W2. Bản demo công khai trên Render (bắt buộc theo luật)
- Mới: `render.yaml` (Blueprint, Docker từ `Dockerfile` hiện có; entrypoint đã nghe `$PORT`), `STORE_BACKEND=memory`, `VF_ONE_CLICK_JUDGE=true`.
- Mới: `scripts/seed_payment_demo.py` chạy lúc khởi động, **không gọi mô hình**: carrier đã verify, một ca chờ release, một ca release cần người thứ hai, một carrier đang bị đóng băng, một payout đã xác nhận.
  (Memory store mất dữ liệu khi khởi động lại, nên seed là bắt buộc.) Khung seed: tái dùng `scripts/seed_verification_demo.py` và `build_payment_corpus.py`.
- Tài khoản cho giám khảo: guest không release/approve được, nên cần **hai tài khoản mật khẩu demo** (reviewer A và B, để thấy duyệt kép) và một tài khoản sandbox PayPal personal;
  ghi vào README mục "Test access". Cơ chế tài khoản mật khẩu cần đọc lại ở `auth.py`/`/api/auth/login` khi làm.
- Chọn gói trả phí nhỏ của Render (credit $50 đủ đến 15/12) để không ngủ; đặt Tavily/Nebius/PayPal key trong dashboard; đặt trần chi tiêu mô hình (`budget.py` sẵn có) vì demo phải miễn phí cho giám khảo.
  **Không đặt Tavily key = mọi ca bị gắn "CARRIER_IDENTITY_UNVERIFIED"**: nhớ đặt.
- Bạn: tạo service Render, claim credit, đăng ký webhook PayPal với URL Render, đặt `PAYPAL_WEBHOOK_ID`.

### W3. Rạp tấn công `/defense` (Presentation + Design + Innovation; cảnh video chính)
- Mới `src/vf_logistics/defense_scenarios.py`: 10 kịch bản **thuần hàm, không đụng store hay PayPal**, tái dùng `payment_checks.check_payment`, `change_screen.screen`,
  `payment_ai.check_payment_ai`/`interpret`, `credit_profile.tighter/cap`, `dual_approval.needed`, `authorization_lifecycle.stage`, `payment_eval.measure`.
  Kịch bản: tráo payee; yêu cầu đổi tài khoản kiểu BEC; payee đổi 3 ngày trước; hóa đơn phồng 1,8 lần; carrier ma; mô hình "bảo bình thường/hạng A" nhưng sàn không hạ;
  một người release 9.000 USD một mình; authorization quá hạn; authorization không thuộc lô này; carrier đang bị tranh chấp. Mỗi kịch bản trả: lớp chặn, mã finding, "tiền di chuyển: 0,00 USD".
  Với kịch bản cần store (authorization không gắn, duyệt kép thật) hiển thị tên test chứng minh thay vì chạy sống.
- Mới `defense_routes.py` (mẫu `settlement_routes.register`), `GET /api/v1/defense/scenarios` (viewer); đăng ký trong `app.py`; thêm vào danh sách GET của `frontend/src/lib/proxy-policy.ts`;
  trang `frontend/src/app/defense/page.tsx` dùng AG Grid (W6).

### W4. Mandate ký theo tinh thần AP2 (Best Use of Agentic Commerce)
- Mới `src/vf_logistics/mandate.py`: Intent Mandate {mandate_id, shipper, max_payout_usd, carriers cho phép, hạn dùng, nonce}, ký HMAC-SHA256 bằng `VF_MANDATE_SECRET`
  (ghi rõ: ký bởi máy chủ, không phải W3C Verifiable Credential).
- Gắn lúc đặt hàng: `payment_routes.py` tạo mandate, `payment_binding.record_authorization` lưu hash, `paypal_client.create_order_for_approval` đưa hash vào `purchase_units[0].custom_id` để truy vết trên PayPal.
- `settler._unproven_authorization`: có mandate thì kiểm chữ ký, hạn, carrier ∈ danh sách, payout ≤ trần; sai thì HELD. Test: giả mạo, hết hạn, ngoài phạm vi, vượt trần.
- `/book` cho shipper đặt trần và danh sách carrier; thẻ Payment hiển thị "trong phạm vi mandate". Bảng đối chiếu AP2 trong README.

### W5. Bề mặt agent: MCP và PayPal Agent Toolkit (Agentic Commerce, PayPal + AI)
- Dependency mới (đã duyệt): `mcp` (Python SDK) và `paypal-agent-toolkit`; cập nhật `requirements.txt`, `requirements.lock`, Dockerfile.
- Mới `src/vf_logistics/mcp_server.py` (stdio, và HTTP nếu gọn): công cụ chỉ-đọc `check_payout_risk(shipment)` (payment_checks + check_payment_ai + credit_profile), `get_payment_state(case_id)`
  (payment_status.payment_state), `list_frozen_parties` (payment_freeze.frozen_parties), `explain_hold(case_id)`. **Không có công cụ nào capture/payout.** Dùng quay cảnh "một agent bên ngoài hỏi Floorline: được trả carrier này không?".
- Mới `src/vf_logistics/paypal_toolkit.py`: bọc PayPal Agent Toolkit với **danh sách hành động chỉ-đọc** (tra tracking, dispute, giao dịch). Câu chuyện: Toolkit không có cổng xác nhận của người; ta khóa nó bằng allowlist,
  và settler vẫn là nơi duy nhất di chuyển tiền. Dùng trong thẻ "PayPal says" ở `/payments`. Test bằng fake; kiểm với khóa thật ở W1.

### W6. Màn hình và AG Grid (Design; sponsor AG Grid)
- Dependency: `ag-grid-community`, `ag-grid-react` (MIT; **không dùng Enterprise**: bản trial 30 ngày sẽ hết trước ngày chấm và hiện watermark). Mới `frontend/src/components/grid/AgTable.tsx` (theme tối khớp console).
- Dùng AG Grid cho: sổ cái `/payments` (thay `components/ui/table.tsx` ở trang này), `/defense`, `/settlement`, bảng carrier, bảng số đo.
- Mới `/settlement`: bên bị đóng băng (`GET /api/v1/payments/freezes`, nút gỡ cho admin gọi `POST /api/v1/payments/freezes/lift`), trạng thái payout (thêm `payout_status` vào `payment_status.payment_ledger`), nút làm mới (`.../payout/refresh`).
- Thẻ "Rủi ro thanh toán" trên trang ca: `case.payment_ai`, hạng tín dụng (`GET /api/v1/payments/cases/<id>/risk` mới, gọi `credit_profile.profile`), finding AI, và cảnh báo "cần người duyệt thứ hai".
- Mới `GET /api/v1/payments/evaluation` (chạy `payment_eval.run` offline, có cache) và bảng số đo trong console: 24/36, 32/36, khoảng tin cậy, ô analyst "chưa đo". Nếu bạn chạy `--live-analyst` thì điền số thật.
- Mỗi route mới phải qua `tests/test_network_defence.py` (ghi chưa xác thực → 401/403) và `tests/test_rbac.py`; cập nhật `proxy-policy.ts` và test của nó.

### W7. PayPal Add Tracking và Disputes (PayPal là lõi)
- `paypal_client.add_tracking(order_id, capture_id, tracking_number, carrier)` (Orders v2 `/track`, chỉ sau khi capture). `settler` gọi theo kiểu cố gắng-hết-sức sau khi capture COMPLETED:
  thất bại chỉ được ghi vào bước, **không đổi kết quả thanh toán**. Cần trường mã vận đơn/BOL trên shipment (kiểm tra `schemas.py ShipmentBase`).
- `paypal_client.get_dispute(dispute_id)`; webhook `CUSTOMER.DISPUTE.CREATED` gọi nó để lấy `disputed_transactions` thay vì tin vào payload (xóa giả định chưa kiểm).

### W8. Gọn giao diện và đường đi demo
- `components/layout/Sidebar.tsx`: thêm nhóm đầu "Floorline Pay" gồm Overview, Book and pay, Payments, Settlement, Carriers, Payment controls, Defense; hai nhóm cũ dịch xuống, đổi tên "Screening".
- Mới `/pay` (Overview): 4 bước, KPI sống, nút vào luồng demo; đăng nhập chuyển hướng về `/pay`. Giữ `/` là Pipeline để không phá `page.test.tsx`.
- Lưu ý khi quay: không để logo Nebius/NVIDIA/Google/Tavily trong khung hình.

### W9. Hồ sơ nộp (Presentation, Best Demo Delivery)
- README viết lại cho giám khảo: tóm tắt 30 giây, "Try it" (URL, tài khoản test, sandbox login), bảng **tiêu chí → bằng chứng**, sơ đồ (mermaid), "significantly updated" (git diff nebius-baseline: 98 file, +15.830 dòng), Limits trung thực. Bỏ `YOUTUBE_URL_PENDING`.
- Video 2:45 (kịch bản trong `docs/PAYPAL_WOW_PLAN.md`): vấn đề → đặt hàng và PayPal giữ tiền → **rạp tấn công** → release có người thứ hai → payout trong tài khoản sandbox carrier → webhook xác nhận, dispute đóng băng → bảng số đo → agent ngoài hỏi MCP. Không nhạc có bản quyền.
- Devpost: mô tả theo 5 tiêu chí; repo public với LICENSE hiện ở About; kiểm tra chạy từ bản clone sạch.

## Lịch

| Ngày | Việc | Ai |
|---|---|---|
| 23–25/10 | W0; bạn tạo khóa sandbox, Render service, claim credit; bắt đầu W3, W4 | bạn / tôi + runner |
| 26–29/10 | W1 (bạn duyệt order trên sandbox), W2 deploy lần đầu, W6 | cả hai |
| 30/10 | hạn Nebius (video, demo): không đụng `vf-app` | bạn |
| 31/10–4/11 | W5, W7, W8; đăng ký webhook với URL Render; chạy `--live-analyst` | cả hai |
| 5–8/11 | sửa theo phản hồi của W1, đóng băng tính năng (8/11) | tôi |
| 9–10/11 | quay video, viết README, Devpost, mở public | bạn / tôi |
| 11/11 | nộp (đệm một ngày); không đổi code sau khi quay | bạn |

## Kiểm chứng

- Mỗi task: cổng runner (sha, test đỏ rồi xanh, mypy, ruff); mỗi lớp: toàn bộ backend + `tsc` + eslint + vitest + `npm run build`; trước khi nộp lại số liệu thật cho README.
- W1: chạy `scripts/sandbox_e2e.py` với khóa sandbox; kiểm payout xuất hiện trong tài khoản sandbox carrier; đối chiếu fixture.
- W2: mở URL Render bằng guest và bằng hai tài khoản reviewer, đi đủ luồng; webhook sandbox gửi thật tới Render và được xác minh chữ ký; khởi động lại service thì seed phục hồi.
- W3/W6: trang `/defense` chạy sống 10 kịch bản, kết quả khớp test; `tests/test_network_defence.py` xanh với route mới.
- Trước khi nộp: clone sạch repo public, làm theo README, xem video trên YouTube ở chế độ ẩn danh, kiểm About hiện MIT.

## Rủi ro

- Thiếu khóa sandbox hoặc Render thì W1/W2 chặn mọi thứ; làm trước.
- Chi phí mô hình/Tavily từ lượt dùng của giám khảo: đặt trần và hiển thị thông báo khi hết ngân sách (không giả vờ chạy).
- Dependency mới (MCP, Toolkit, AG Grid) có thể kéo theo lỗi build trên Render; thử build sớm (ngày 26).
- Phạm vi lớn: nếu trễ, cắt theo thứ tự W5 (Toolkit) → W7 → W4, giữ W1, W2, W3, W6, W8, W9.
- Corpus tổng hợp tạo từ chính các mẫu đã viết kiểm tra: luôn ghi "synthetic", không trình bày như tỷ lệ thật.

## File chính sẽ động đến

`scripts/sandbox_e2e.py`, `scripts/seed_payment_demo.py`, `render.yaml`, `src/vf_logistics/{defense_scenarios,defense_routes,mandate,mcp_server,paypal_toolkit}.py`,
`src/vf_logistics/{paypal_client,settler,payment_routes,payment_binding,payment_status,paypal_webhook,payout_tracking,app}.py`,
`frontend/src/app/{defense,settlement,pay,payments,book}`, `frontend/src/components/{grid,layout/Sidebar.tsx}`, `frontend/src/lib/{api.ts,proxy-policy.ts}`, `README.md`, `requirements*.txt`, `frontend/package.json`.
