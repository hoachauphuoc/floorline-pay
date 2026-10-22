# Floorline Pay — the agent pays the carrier the moment delivery is proven, and can never pay a shipment the rules held

> **Work in progress for the PayPal AI Hackathon (submissions close 12 Nov 2026, 12:00 PT).** This repository starts from Floorline as submitted to the Nebius x NVIDIA Global AI Hackathon (tag `nebius-baseline`, commit `5690ea3`). Everything after that tag is the PayPal work; `git diff nebius-baseline` shows it.

Freight payments run on trust. A shipper's money is **held** at booking with PayPal (`intent: AUTHORIZE`). The carrier is **paid** through PayPal Payouts only after a person has released the shipment. If the shipment is blocked, the hold is **voided** and the money goes back to the shipper.

Deterministic checks set a risk **floor** for every shipment, including new payment checks: a payee changed just before payment, a payee that is not the one on file, a payout above the booked freight. AI agents may argue a case above that floor; nothing they say can take it below. A held shipment keeps its money held, and a person decides every disputed case.

- **Hackathon:** PayPal AI Hackathon · **Targets:** Best Use of Agentic Commerce, Best Use of PayPal + AI
- **Live demo:** pending (a separate Cloud Run service; the Nebius service `vf-app` is not changed)
- **Demo video (under 3 minutes):** YOUTUBE_URL_PENDING
- **Licence:** MIT · **Full reference of the inherited system:** [REFERENCE.md](REFERENCE.md) · **Architecture:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · **Plan:** [docs/PAYPAL_PLAN.md](docs/PAYPAL_PLAN.md)

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

## PayPal and AI in the project

| | |
|---|---|
| PayPal REST, sandbox only (`api-m.sandbox.paypal.com`) | Orders v2 (create with `AUTHORIZE`, authorize), authorization capture and void, Payouts |
| Where | `src/vf_logistics/paypal_client.py` (every call returns a receipt and never raises), `payment_routes.py` (booking), `settler.py` (capture, payout, void), `payment_status.py` (state and ledger) |
| AI | Nemotron 3 Nano and Super through Nebius Token Factory screen every case; MiniCPM-V reads scanned documents; Tavily gives live web search to compliance, investigation, the zero-day radar and the debate |
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

```bash
git clone https://github.com/hoachauphuoc/floorline-pay.git   # a plain clone, not --depth
cd floorline-pay
git diff --stat nebius-baseline HEAD
```

The inherited Floorline measurements (synthetic corpus, 95% intervals) are in [REFERENCE.md](REFERENCE.md#measured-results). They measure fraud detection, not payments; the payment checks have unit tests and no measured detection rate.

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
# Backend (2,205 passed, 47 skipped without the Firestore and Pub/Sub emulators)
pip install "pytest>=8.0.0" "pytest-asyncio>=0.23.0" "pytest-cov>=4.1.0" "pytest-mock>=3.12.0"
python -m pytest tests/ -q
python -m mypy src/vf_logistics/ --ignore-missing-imports
python -m ruff check src/ tests/ scripts/ --select E,F,W --ignore E501,E402,W291,W293

# Frontend (1,794 passed) and build
cd frontend && npm ci && npx tsc --noEmit && npx eslint src && npx vitest run && npm run build
```

The figures are from the last full runs on the development machine, not from CI on this repository. Every PayPal call in the tests goes through a fake: no test has called PayPal.

## Limits, stated

- **No real PayPal call has been tested yet**: the client, the booking routes and the settler are covered with fakes. The approval flow assumes PayPal's Orders v2 reply carries a `payer-action` link for an order made with `payment_source.paypal.experience_context`; it has not been run against the sandbox.
- **Settling depends on Pub/Sub delivery** to the receiver, as described above.
- The settler captures the whole authorization and pays `payout_amount`; if the shipper authorized more than the payout, the difference stays with the merchant account.
- A decision made by the pipeline itself (an auto-cleared case) does not start a payment; only a person's release or block does.
- Fraud in a forwarder's book is rare, so almost every hold is honest; the inherited screen is not precise, and no real customer traffic has been measured ([REFERENCE.md](REFERENCE.md#measured-results)).

## Requirements of the hackathon rules

| Rule | Where it is met |
|---|---|
| PayPal developer platform (sandbox) and an AI tool, with PayPal central | `paypal_client.py`, `settler.py`; Nemotron agents on Nebius Token Factory |
| Significantly updated after 1 Oct 2026, with an explanation | the section above; `git diff nebius-baseline` |
| Working demo | the hosted URL once deployed, or the local steps above |
| Public repository with a detectable open source licence | MIT (the repository is private until submission) |
| Demo video under 3 minutes on YouTube | link at the top |
| English | all materials |

## More

[REFERENCE.md](REFERENCE.md) (API, environment variables, measured results of the inherited screen, repository layout) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/RECEIVER_CONTRACT.md](docs/RECEIVER_CONTRACT.md) · [docs/PAYPAL_PLAN.md](docs/PAYPAL_PLAN.md)

Released under the MIT licence; see [LICENSE](LICENSE).
