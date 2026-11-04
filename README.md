# Floorline Pay — the agent pays the carrier the moment delivery is proven, and can never pay a shipment the rules held

> **A proof of concept for the PayPal AI Hackathon, running on the PayPal sandbox: no real money moves.** It starts from Floorline as submitted to the Nebius x NVIDIA Global AI Hackathon (tag `nebius-baseline`, commit `5690ea3`). Everything after that tag is the PayPal work; `git diff nebius-baseline` shows it. What has run against the real sandbox and what has not is listed under [Limits, stated](#limits-stated).

Freight payments run on trust. A shipper's money is **held** at booking with PayPal (`intent: AUTHORIZE`). The carrier is **paid** through PayPal Payouts only after a person has released the shipment. If the shipment is blocked, the hold is **voided** and the money goes back to the shipper.

Deterministic checks set a risk **floor** for every shipment, including new payment checks: a payee changed just before payment, a payee that is not the one on file, a payout above the booked freight. AI agents may argue a case above that floor; nothing they say can take it below. A held shipment keeps its money held, and a person decides every disputed case. Around the money sit seven credit-security layers (below): the AI ones may only raise risk or tighten a limit, never lower or loosen one, and the settler, the only code that moves money, never calls a model.

- **Hackathon:** PayPal AI Hackathon · **Targets:** Best Use of Agentic Commerce, Best Use of PayPal + AI
- **Live demo:** https://floorline-pay.onrender.com (the console on Render; the case store and the decision queue are Firestore and Pub/Sub in a separate Google Cloud project; the Nebius service `vf-app` is not changed)
- **Demo video (under 3 minutes):** YOUTUBE_URL_PENDING
- **Licence:** MIT · **Full reference of the inherited system:** [REFERENCE.md](REFERENCE.md) · **Architecture:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · **Plan:** [docs/PAYPAL_PLAN.md](docs/PAYPAL_PLAN.md)

## Try it in two minutes

1. Open the live demo and choose **Continue as guest judge** (one click, no password). A guest can book and pay in the sandbox, look at every screen, and block a case; a guest cannot release or approve.
2. **Book and pay** (`/book`): fill the booking, press *Pay with PayPal*, approve with a PayPal **sandbox** buyer account, and you come back to a case whose money is *held*, not taken.
3. Open the case: the Payment card says *Funds held, awaiting a decision*, and the page lists every finding that raised the risk floor and why.
4. A person decides. Releasing needs a password account, so a guest sees the held state and the refusal rather than a payment. A block returns the held money to the shipper.

| Step | Status on the real PayPal sandbox |
|---|---|
| Create an order, approve it as a buyer, authorize (money held) | **Run for real** on 24 and 30 Oct 2026 (a 1,300 USD hold) |
| Block, then void the hold | **RESULT PENDING**: a person's block was not being published to the settler until `0c7cebe`; the rerun is not done |
| Release, then capture and pay the carrier with Payouts | **RESULT PENDING**: not run against the sandbox yet |
| Webhook (payout, refund and dispute events) | **RESULT PENDING**: not registered yet |

## How the money moves

1. **Book and pay** (`/book`). The shipper fills the booking and is sent to PayPal to approve the payment. PayPal returns them to `/book/return`, which authorizes the order and submits the shipment with the PayPal authorization id. The money is held, not taken.
2. **Screen.** Floorline reads the paperwork as untrusted input, computes the deterministic floor, and lets Nemotron agents with live Tavily search score above it.
3. **Decide.** A person releases or blocks the shipment. The decision is published once per idempotency key.
4. **Settle** (`settler.py`, the only code that moves money). On a published release it re-checks the payment rules, captures the authorization, and pays the carrier with PayPal Payouts. On a published block it voids the authorization. Every refusal is a named status (`skipped`, `held`, `retry`, `failed`) and nothing is paid. An unknown outcome is answered with 503 so Pub/Sub delivers the decision again; each PayPal call carries a request id derived from the decision key, so a repeat cannot pay twice.
5. **Watch.** The case page shows a Payment card (held, paid, returned, or held back, with the PayPal ids). `/payments` lists the newest outcome of every payment.

### The design rule

`payment_checks.check_payment()` only ever **adds** findings to the same floor arithmetic as the rest of Floorline, so it can raise a floor and never lower one. The settler does not trust the release alone: if the payment checks still hold the payout, a person's release does not pay it. The browser can create an order and authorize it, and nothing else; capturing and paying out are not reachable from the console.

| Check | Floor | When |
|---|---|---|
| `PAYEE_MISMATCH` | 80 | the payee is not the one on file for the carrier |
| `PAYEE_MISSING`, `PAYOUT_ABOVE_FREIGHT` | 75 | no payee on a payout; payout above the booked freight |
| `PAYEE_RECENTLY_CHANGED` | 70 | the payee changed in the last 14 days (or in the future) |
| `PAYEE_UNVERIFIED`, `PAYEE_CHANGE_TIME_UNREADABLE`, `PAYOUT_AMOUNT_MISSING`, `PAYOUT_AMOUNT_UNREADABLE`, `PAYOUT_FREIGHT_UNVERIFIED` | 45 | something needed to check the payment is missing or unreadable: unverified, never fine |

The checks run only on a payment case (a shipment with a payout amount, a PayPal authorization or a carrier payee). No finding quotes a payee address, so none reaches a model prompt or the console.

## Credit security: what stands between a fraudster and the money

Paying a carrier is a credit decision made on paperwork, so each layer below takes away one way to be paid wrongly. Every layer is deterministic code that a person configures; the AI layers sit on top and **may only raise risk or tighten a limit, never lower or loosen one**. The settler never calls a model: it reads what the analysis stored on the case.

| Layer | What it stops | Where |
|---|---|---|
| **1. Booking-bound authorization** | an authorization id typed into a shipment event that is not this shipment's money: the booking writes the PayPal-reported reference and amount, and the settler checks them, asks PayPal again, and pays only after the capture is `COMPLETED` | `payment_binding.py`, `settler.py` |
| **2. Carrier payee registry** | a payee the sender names: payees are registered by one person and verified by a **different** person after a callback; the sender's claim about the payee on file is overwritten from the registry at intake, on the case page and in the settler | `carrier_registry.py`, `/carriers` |
| **3. Limits and a pause switch** | one large payout, a draining day: per payout, per carrier per day (amount and count), per tenant per day; an operator can pause, only a governance admin resumes or publishes limits, all with a password; a block still voids while paused | `payment_controls.py`, `/controls` |
| **4. Dual approval** | one compromised reviewer releasing a large payout: a release at or above `PAYMENT_DUAL_APPROVAL_USD` (default 2,500; an unreadable value means every payout) is not executable until a **different** reviewer approves it with a password and a note; the settler holds it again | `dual_approval.py`, `evidence.py`, `settler.py` |
| **5. Settlement integrity** | trusting "accepted" as "paid", and paying into a dispute: a webhook receiver PayPal itself must vouch for (`verify-webhook-signature`), payout status (confirmed, failed or pending, never assumed), authorization lifecycle (renewed after the 3-day honor period, refused after 29 days or when its age is unknown), and a **freeze** on the carrier and shipper of any dispute, reversed capture or failed payout that only a governance admin lifts | `paypal_webhook.py`, `payout_tracking.py`, `authorization_lifecycle.py`, `payment_freeze.py` |
| **7. AI layers (raise only)** | what the rules cannot see: a payment-risk analyst (a model that sees numbers and categories, never a name or an address), a web check that the carrier exists (a search that did not run is *unverified*, not clean), a screen of the message that asked to change a payee (business-email-compromise wording and Model Armor), and a credit standing A to D that caps one payout (the applied cap is the smaller of the person's limit and the standing's) | `payment_ai.py`, `change_screen.py`, `credit_profile.py`, `payment_checks.py` |

### What the AI layers add, measured on invented data

[`scripts/evaluate_payment_ai.py`](scripts/evaluate_payment_ai.py) runs the checks over a **synthetic** corpus of 80 payouts (36 fraudulent, 44 legitimate; invented carriers and recorded search results; [`data/payment_fraud_corpus.json`](data/payment_fraud_corpus.json), rebuilt by `scripts/build_payment_corpus.py` from a fixed seed). The rates describe that corpus only, which was built from the patterns the checks were written for; they are not a forecast. Flagged means a risk floor of 40 or more.

| Arm | Catches (recall) | False flags on legitimate payouts |
|---|---|---|
| Deterministic payment checks and the request screen | 24 of 36 (66.7%, 95% CI 50.3-79.8%) | 0 of 44 (0%, 95% CI 0-8.0%) |
| + carrier look-up | 32 of 36 (88.9%, 74.7-95.6%) | 6 of 44 (13.6%, 6.4-26.7%) |
| + payment-risk analyst | **not measured**: it needs a model key and is run with `--live-analyst` | not measured |

The look-up's 8 extra catches are shell carriers that have no web presence by construction in the corpus, and its 6 false flags are small real carriers with none: it trades one for the other. The four impersonated-carrier cases (a real carrier whose registered payee is a free-mail account) are caught by none of the measured arms; they are what the analyst is for, and whether it catches them is the number to measure before relying on it. If it adds nothing, the report says "added nothing".

## PayPal and AI in the project

| | |
|---|---|
| PayPal REST, sandbox only (`api-m.sandbox.paypal.com`) | Orders v2 (create with `AUTHORIZE`, authorize), authorization capture and void, Payouts |
| Where | `src/vf_logistics/paypal_client.py` (every call returns a receipt and never raises), `payment_routes.py` (booking), `settler.py` (capture, payout, void), `payment_status.py` (state and ledger) |
| AI | Nemotron 3 Nano and Super through Nebius Token Factory screen every case; MiniCPM-V reads scanned documents; Tavily gives live web search to compliance, investigation, the zero-day radar and the debate |
| AI on payments | a payment-risk analyst (Nemotron, shown numbers and categories and never a name or an address), a web check that the carrier exists (Tavily), a screen of payee-change requests (business-email-compromise wording and Model Armor), a credit standing A to D; each can only raise risk or lower a cap |
| Not used | PayPal Agent Toolkit and MCP server; the checkout JS SDK (the shipper approves on PayPal's own page) |

## What was significantly updated during the Submission Period

The baseline is Floorline as submitted to the Nebius x NVIDIA hackathon, a shipment-fraud screen that releases or holds cargo. It had no payment code. The PayPal work, all after the `nebius-baseline` tag:

| Added | Where |
|---|---|
| Sandbox PayPal client | `paypal_client.py`, `tests/test_paypal_client.py` |
| Payment fields, validated at the edge, on the B2B audit request | `schemas.py`, `tests/test_paypal_fields.py` |
| Deterministic payment checks in the risk floor | `payment_checks.py`, `verifier.py`, `tests/test_payment_checks.py` |
| Settler that captures and pays on a human release, voids on a block | `settler.py`, `decision_receiver.py`, `tests/test_settler.py` |
| Booking routes with a guarded return address | `payment_routes.py`, `app.py`, `tests/test_payment_routes.py`, `tests/test_payment_approval.py` |
| Payment state per case and the payment ledger | `payment_status.py`, `tests/test_payment_status.py`, `tests/test_payment_ledger.py` |
| Booking, return, Payments pages, Payment card, proxy entries, sidebar links | `frontend/src/app/book`, `frontend/src/app/payments`, `frontend/src/components/payments`, `frontend/src/lib/booking.ts`, `proxy-policy.ts`, `api.ts` |
| Credit security L1-L3 and L6: booking-bound authorizations, a two-person carrier payee registry, limits and a pause switch | `payment_binding.py`, `carrier_registry.py`, `carrier_routes.py`, `payment_controls.py`, `controls_routes.py`, `frontend/src/app/carriers`, `frontend/src/app/controls` |
| L4: dual approval of a large release | `dual_approval.py`, `evidence.py` (`second_approve`), `settler.py`, the Review panel |
| L5: settlement integrity | `paypal_webhook.py`, `settlement_routes.py`, `payout_tracking.py`, `authorization_lifecycle.py`, `payment_freeze.py`, `paypal_client.py` |
| L7: AI layers that only raise risk, a synthetic corpus and its evaluation | `payment_ai.py`, `change_screen.py`, `credit_profile.py`, `payment_eval.py`, `scripts/build_payment_corpus.py`, `scripts/evaluate_payment_ai.py` |

```bash
git clone https://github.com/hoachauphuoc/floorline-pay.git   # a plain clone, not --depth
cd floorline-pay
git diff --stat nebius-baseline HEAD
```

The inherited Floorline measurements (synthetic corpus, 95% intervals) are in [REFERENCE.md](REFERENCE.md#measured-results). They measure fraud detection, not payments; the payment checks are measured only on the invented corpus described under Credit security, and the analyst model not at all yet.

## Run it locally

Prerequisites: Python 3.11+, Node.js 20+, a Nebius Token Factory key (https://tokenfactory.nebius.com/), optionally a Tavily key, and for payments a PayPal developer account (https://developer.paypal.com/, free) with a sandbox **business** account (the merchant, which needs a sandbox balance for payouts) and a sandbox **personal** account (the shipper who approves the payment). Sandbox money is not real money.

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # Windows: copy .env.example .env
```

Edit `.env`: set `NEBIUS_API_KEY`, optionally `TAVILY_API_KEY`, `STORE_BACKEND=memory` for a local run, and the payment settings:

| Variable | Meaning |
|---|---|
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | the REST app of your sandbox business account. Unset means payments are off: every payment path answers `not_configured` and pays nothing |
| `PAYMENT_RETURN_ORIGINS` | comma-separated origins PayPal may send a shipper back to, for example `http://localhost:3000`. Unset means no return address is accepted |
| `PAYMENT_DUAL_APPROVAL_USD` | from this payout a release needs a second approver (default 2500). A value that cannot be read means every payout needs two people |
| `PAYPAL_WEBHOOK_ID`, `PAYPAL_WEBHOOK_TENANT_ID` | the id of the webhook registered in the PayPal developer dashboard, and the tenant its events belong to (multi-tenant only). Unset means the webhook receiver is off and answers 503 |
| `PAYMENT_AI_MODEL` | the model of the payment-risk analyst (defaults to the project's model) |

```bash
PYTHONPATH=src python -m vf_logistics.app                       # API on http://localhost:8080 (PowerShell: $env:PYTHONPATH = "src"; python -m vf_logistics.app)
cd frontend && npm ci
NEXT_PUBLIC_DEMO_MODE=false FLASK_API_BASE=http://127.0.0.1:8080 npm run dev   # console on http://localhost:3000
```

Open `/book`, book a shipment, approve the payment as the sandbox personal account, and open the case.

**Settling needs the decision to reach the receiver.** The settler runs when a published human decision is pushed to `POST /api/v1/receiver/decision` (Pub/Sub push, accepted only with a Google-signed token for `RECEIVER_PUSH_SA` and `RECEIVER_PUSH_AUDIENCE`; see [docs/RECEIVER_CONTRACT.md](docs/RECEIVER_CONTRACT.md)). A local run without that wiring can book, hold, screen and decide, but no money is captured or paid. Deployment to Cloud Run is described in [REFERENCE.md](REFERENCE.md).

## Signing in

The console asks everyone to sign in. **Continue as guest judge** signs in with one click and no password: the guest can book and pay in the sandbox, look around, inject shipments, block a case and simulate a policy, but cannot release or approve. Releasing cargo, and so paying a carrier, needs a password account. `GET /api/v1/auth/policy` (after sign-in) returns what every route enforces.

## Reproducible testing

```bash
# Backend (2,894 passed, 47 skipped without the Firestore and Pub/Sub emulators)
pip install "pytest>=8.0.0" "pytest-asyncio>=0.23.0" "pytest-cov>=4.1.0" "pytest-mock>=3.12.0"
python -m pytest tests/ -q
python -m mypy src/vf_logistics/ --ignore-missing-imports
python -m ruff check src/ tests/ scripts/ --select E,F,W --ignore E501,E402,W291,W293

# Frontend (117 files, 1,903 passed) and build
cd frontend && npm ci && npx tsc --noEmit && npx eslint src && npx vitest run && npm run build
```

The figures are from full runs on the development machine on 30 Oct and 4 Nov 2026 (`ruff` clean; `mypy` clean on 70 files, run with `--python-version 3.13` locally because the numpy stubs there need it; `tsc` clean, `eslint` 0 errors). CI on this repository runs the same checks and passed on the commit before this text (`0c7cebe`). Every PayPal call in the tests goes through a fake or a recorded sandbox answer: no test calls PayPal.

## Limits, stated

- **Only the booking half has met the real sandbox.** Creating an order, approving it as a sandbox buyer and authorizing it (a 1,300 USD hold) ran for real on 24 and 30 Oct 2026. Capture, payout, void and the webhook are covered with fakes and with PayPal answers recorded from the sandbox in `tests/fixtures/paypal`; no capture, payout or void has run against the sandbox yet (see the status table above).
- **A person's release or block used to stay in the store.** For a case booked through `/book` the decision was recorded and the shipment held or released, but nothing was published, so the settler never saw it and a block never voided the hold. Found on the first real booking and fixed in `0c7cebe`; `tests/test_human_decide_publishes.py` pins it. The fix has not yet been shown end to end on the live service.
- **The agent starts suspended.** Until a person publishes a delegation boundary (Governance page) every protected action, including publishing a decision, is refused and the case goes to a person. The demo runs under `BOUNDARY-v1`, which does not allow the agent to release by itself.
- **Settling depends on Pub/Sub delivery** to the receiver, as described above.
- **The webhook receiver** (`/api/v1/paypal/webhook`) is off until `PAYPAL_WEBHOOK_ID` is set. It assumes payout events carry `sender_batch_id` (our `<key>-payout`), dispute events carry the capture id in `disputed_transactions[].seller_transaction_id`, and that renewing an authorization gives a new id the settler then uses. None of this has met the sandbox.
- **Two settlements at the same moment can together pass the daily limit**: the check and the write are not one step. Known, not fixed.
- **"Payout accepted" is not "money received".** The settler records a payout as settled when PayPal accepts the batch; the tracking that confirms the money arrived is best effort.
- **There is no console screen yet for frozen parties or payout status**: they are API only (`GET /api/v1/payments/freezes`, `POST /api/v1/payments/freezes/lift`, `POST /api/v1/payments/cases/<id>/payout/refresh`; the payment state of a case carries `payout_status`).
- **A new carrier's first payouts are capped** (credit standing C: 2,000 USD) and nothing but being paid without trouble raises the cap; there is no human override yet.
- **The payment-risk analyst has not been measured.** The synthetic corpus shows what the deterministic checks and the carrier look-up catch (above); whether the model adds anything is the number `scripts/evaluate_payment_ai.py --live-analyst` produces, and it has not been run. The corpus is invented from the patterns the checks were written for, so its rates are not a forecast.
- **The carrier look-up trades one error for another**: it catches carriers with no web presence and flags small real carriers that have none.
- The settler captures the whole authorization and pays `payout_amount`; if the shipper authorized more than the payout, the difference stays with the merchant account.
- A decision made by the pipeline itself (an auto-cleared case) does not start a payment; only a person's release or block does.
- Fraud in a forwarder's book is rare, so almost every hold is honest; the inherited screen is not precise, and no real customer traffic has been measured ([REFERENCE.md](REFERENCE.md#measured-results)).

## Requirements of the hackathon rules

| Rule | Where it is met |
|---|---|
| PayPal developer platform (sandbox) and an AI tool, with PayPal central | `paypal_client.py`, `settler.py`; Nemotron agents on Nebius Token Factory |
| Significantly updated after 1 Oct 2026, with an explanation | the section above; `git diff nebius-baseline` |
| Working demo | the hosted URL once deployed, or the local steps above |
| Public repository with a detectable open source licence | MIT (`LICENSE`); the repository is made public before submission |
| Demo video under 3 minutes on YouTube | link at the top |
| English | all materials |

## More

[REFERENCE.md](REFERENCE.md) (API, environment variables, measured results of the inherited screen, repository layout) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/RECEIVER_CONTRACT.md](docs/RECEIVER_CONTRACT.md) · [docs/PAYPAL_PLAN.md](docs/PAYPAL_PLAN.md)

Released under the MIT licence; see [LICENSE](LICENSE).
