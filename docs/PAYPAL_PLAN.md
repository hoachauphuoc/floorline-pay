# Floorline Pay — kế hoạch ý tưởng cho PayPal AI Hackathon

## Context

- PayPal AI Hackathon: nộp trước **12:00 PT 12/11/2026 (03:00 13/11 giờ VN)** theo Official Rules (trang Devpost ghi 2:00pm PST, nhưng Rules thắng theo mục 11 → lấy mốc sớm hơn). ~20k người đăng ký.
- Bài Nebius (Floorline) hạn **30/10 10:00 PT = 00:00 31/10 VN**, video còn `YOUTUBE_URL_PENDING` → ưu tiên 1. PayPal build window thực tế: **31/10 → 12/11 (~12 ngày)**.
- Cả hai giải chấm **1–15/12**; Nebius đòi demo live, miễn phí, đúng như video đến hết 15/12 → bản PayPal phải **tách hẳn** (fork/branch + service riêng), bản Nebius đóng băng bằng tag.
- PayPal yêu cầu: PayPal sandbox là *lõi*, AI bất kỳ (giữ Nemotron/Token Factory), dự án cũ phải "significantly updated" sau 1/10 và giải trình; repo public + license; video < 3 phút; demo chạy được.

## Khảo sát (Tavily, 22/10/2026)

- **Chủ đề gợi ý chính thức (trang Resources)**: "AI agents that actually transact… *a scheduling assistant that pays contractors automatically when a job is marked complete*", "payout tool for gig workers that pays out the moment a task is approved", "marketplace checkout flow that splits payment", business tools cho merchant/marketplace. Hạng mục riêng: *Best Use of Agentic Commerce*, *Best Use of PayPal + AI*.
- **Công cụ PayPal liên quan**:
  - Orders v2 `intent: AUTHORIZE` → giữ tiền 29 ngày (honor 3 ngày, sandbox không hết hạn), `capture` / `void` / reauthorize.
  - Payouts API (`/v1/payments/payouts`) — trả hàng loạt cho người nhận; sandbox business account phải có số dư.
  - Add Tracking API (`v2/checkout/orders/{id}/track`) — gắn mã vận đơn vào giao dịch → khớp tự nhiên với logistics.
  - Braintree Marketplace `hold_in_escrow` / `release_from_escrow` (phương án thay thế, phức tạp hơn).
  - **PayPal Agent Toolkit** (`paypal-agent-toolkit` 1.11.0 trên PyPI, có Python) + remote MCP server: orders, invoices, disputes, shipment tracking, reporting.
- **Nỗi đau thực tế** (nguồn ngành 2026): carrier nhỏ chờ 30–60 ngày; quick-pay ăn 2–5%, factoring 1–4%; double brokering & giả mạo carrier (giả một carrier "chỉ tốn $300"); BEC "vendor bank change" → FBI IC3 2025: **$3.05 tỷ** thiệt hại, 24.768 vụ.
- **Bối cảnh agentic payments**: Visa Intelligent Commerce / Mastercard Agent Pay đều dựa trên spend cap + duyệt người → "guardrail" là chủ đề nóng; Floorline có sẵn câu chuyện guardrail mạnh hơn (floor không thể bị hạ).
- **Sponsor**: giải AG Grid thực chất là **"Best Use of AG Studio"** (dashboard + Studio Agent Framework, WebMCP) — 5 giải, 10k USD; chỉ được 1 sponsor prize/dự án.

## Ý tưởng đề xuất: **Floorline Pay** — "the agent pays the carrier the moment delivery is proven, and can never pay a shipment the rules held"

Luồng end-to-end (tất cả PayPal sandbox):
1. **Booking**: shipper thanh toán cước qua PayPal Checkout với `intent: AUTHORIZE` → tiền bị giữ (escrow-like), case Floorline được tạo, mã vận đơn gắn qua Add Tracking API.
2. **Delivery proof**: carrier upload POD/BOL qua link chứng từ có sẵn (vòng evidence loop hiện có) → Floorline đọc tài liệu (Model Armor + vision), tính floor, agent Nemotron chấm điểm + Tavily kiểm tra carrier/payee.
3. **Kiểm tra mới chuyên cho thanh toán (deterministic, đi vào floor)**: payee (email PayPal của carrier) bị đổi gần thời điểm trả; payee ≠ carrier trong registry; số tiền vượt cước đã book; carrier trùng/double-brokered; POD không khớp lô.
4. **Agentic release trong delegation boundary**: case sạch & dưới hạn mức → agent **capture** authorization rồi **Payout** cho carrier ngay (quick-pay 0% phí thay vì 2–5%). Case bị giữ → tiền vẫn bị giữ, người duyệt quyết; fraud xác nhận → **void** (trả lại shipper). Idempotency key hiện có chống trả hai lần.
5. **Audit + dashboard**: mỗi capture/payout/void ghi audit trail (ai, vì sao, receipt PayPal); bảng hàng chờ & sổ cái thanh toán trên **AG Studio**.

Vì sao thắng điểm:
- *Technological*: PayPal ở đúng điểm quyết định (authorize→capture→payout→void + tracking), không phải gắn thêm.
- *Innovation*: "agent có thể chi tiền nhưng không thể hạ mức rủi ro" — guardrail chứng minh bằng test/eval, khác các shopping agent.
- *Impact*: số liệu cụ thể (30–60 ngày, 2–5% phí, $3.05 tỷ BEC).
- *Hạng mục nhắm*: Best Use of Agentic Commerce / Best Use of PayPal + AI (honorable) + Best Use of AG Studio (sponsor).

## Điểm móc trong code hiện có (đã rà)

- Hiện **không có** code thanh toán, trường payee/bank, check thanh toán hay ag-grid.
- Release do người duyệt: `evidence.py:1223` `execute_approval()` (claim → send → confirm); bước send gọi `tools.release_shipment` rồi `tools.publish_decision(..., idempotency_key=f"exec-{approval_id}")` (:1346–1358).
- Release do agent: `governance.py:843` `execute()` + `_check_release()` (:649); hành động mới phải vào `PROTECTED_ACTIONS` (:47) và `_ACTION_IMPLS` (:833).
- Publish: `tools.py:256` `publish_decision` (đã chống trùng theo key) → Pub/Sub → `decision_receiver.py:87` `receive()` → `store.apply_decision_once()`. `docs/RECEIVER_CONTRACT.md` đã nêu "billing" là consumer hạ nguồn.
- Executor allow-list: `app.py:2169` `internal_execute()` chỉ cho `publish_decision`.
- Mẫu HTTP client: `tavily_client.py` (httpx.AsyncClient dùng chung, status string, key đọc mỗi lần gọi); test bằng `httpx.MockTransport` (`tests/test_executor_client.py`), fixture autouse `tests/conftest.py:37` (STORE_BACKEND=memory).
- Dữ liệu: `schemas.py:77` `ShipmentBase` (declared_value, shipping_cost, currency, shipper_*, receiver_*) — cần thêm carrier/payee.
- Check danh tính để bắt chước: `verifier.py:1559` `check_counterparty`, `:437` `check_whitelist`; `validate()` :1702.
- Frontend: hàng chờ `frontend/src/app/review/page.tsx`, worklist `app/today/page.tsx` (card list, TanStack Query); bảng duy nhất là shadcn `components/ui/table.tsx`.

**Quyết định thiết kế đề xuất**: tiền chỉ di chuyển ở **consumer hạ nguồn** (`decision_receiver` → payment settler) khi nhận quyết định RELEASE đã publish — runtime phân tích/agent *không giữ credential PayPal*, giống ranh giới IAM hiện có cho Pub/Sub. Idempotency: `apply_decision_once` + `PayPal-Request-Id = idempotency_key`. BLOCK đã publish → void authorization. Cần kiểm tra khi lập task: đường auto-release của agent có publish quyết định không (nếu không, thêm publish trước khi settle).

## Kế hoạch thực thi (sau khi nộp Nebius)

0. **Trước 31/10 (không đụng code)**: tag commit nộp Nebius; tạo PayPal developer + sandbox (business có số dư, personal làm shipper, 2–3 tài khoản carrier); đăng ký hackathon PayPal.
1. **Tách bản** (đã chốt): **repo public mới** (vd `floorline-pay`, MIT) từ tag Nebius, deploy **service Cloud Run thứ hai** — `vf-app` và repo Nebius không đổi đến 15/12.
2. **Backend PayPal client** (`src/vf_logistics/paypal_client.py`, httpx như các client hiện có): OAuth token, create order AUTHORIZE, capture, void, payouts, add tracking; fail-closed; mock trong test.
3. **Checks thanh toán vào verifier** (payee change / payee mismatch / over-amount) → đi vào floor, có test.
4. **Nối release → payment**: điểm release/execute hiện có gọi capture+payout; held → giữ; reject → void; ghi receipt vào audit; idempotent.
5. **Frontend**: nút PayPal Checkout (JS SDK v6) cho booking; trạng thái thanh toán trên case; **AG Studio** (đã chốt) cho dashboard hàng chờ thanh toán + sổ cái capture/payout/void (kiểm tra license/trial AG Studio trước khi thêm dependency).

Lịch gợi ý: 31/10–3/11 bước 1–2; 4–6/11 bước 3–4; 7–9/11 bước 5; 10–11/11 bước 6; nộp chậm nhất 11/11.
6. **Docs + nộp**: README (luật PayPal, giải trình "significantly updated" dựa trên diff từ tag Nebius), video < 3 phút, Devpost.

Task chi tiết sẽ được viết vào `orchestrator/execution_plan.md` theo quy trình CLAUDE.md (Master lập kế hoạch, sub-agent sửa code).

## Verification

- `python -m pytest tests/ -q`, mypy, ruff, frontend `tsc/lint/test` (CLAUDE.md §7) — không giảm baseline.
- Test mới: release sạch → capture + payout được gọi đúng một lần; held → không capture; reject → void; payee change → floor tăng, không auto-release; lỗi PayPal → không release (fail closed).
- Chạy thật trên sandbox: booking → upload POD → payout xuất hiện trong sandbox carrier account; quay lại thành video.
