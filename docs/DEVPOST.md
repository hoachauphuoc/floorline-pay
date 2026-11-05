# Devpost text for Floorline Pay

Paste each section into the matching Devpost field. Every number is from `README.md`; where something has not run yet it says so. Replace `RESULT PENDING` lines only with numbers you have seen.

## Name

Floorline Pay

## Tagline

An agent that pays the carrier only after a person releases the shipment, and can never pay what the rules held.

## Targets

Best Use of Agentic Commerce · Best Use of PayPal + AI

## Inspiration

Freight payments run on trust. A forwarder pays a carrier on paperwork it cannot fully check, and the loss from one wrong payment is the whole freight bill. We started from Floorline, our shipment-fraud screen from the Nebius x NVIDIA hackathon, which already set a risk floor with plain code and let AI agents argue only upward. It had no payment code. This project connects it to real money movement on PayPal.

## What it does

A shipper books a shipment and approves the payment on PayPal's own page. PayPal **holds** the money (`intent: AUTHORIZE`); nothing is taken. Floorline screens the case. A person then decides:

- **Release:** the settler checks the payment rules once more, **captures** the money and **pays the carrier** with PayPal Payouts.
- **Block:** the settler **voids** the hold and the money goes back to the shipper.

Deterministic checks (a payee that is not the one on file, a payee changed in the last 14 days, a payout above the booked freight, a missing payee) add to a risk floor. AI agents may raise a case above the floor; no model, learning adjustment or debate verdict can lower it. A guest session can look and book but cannot release or approve; a large release needs a second approver. The settler is the only code that moves money, and it never calls a model.

This is a proof of concept on the PayPal sandbox: no real money moves.

## How we built it

- **PayPal (sandbox only):** REST Orders v2 (create with `AUTHORIZE`, authorize), authorization capture and void, and Payouts. Every call returns a receipt and never raises; each carries a request id derived from the decision key, so a repeat cannot pay twice.
- **Settlement path:** a person's decision is published to Google Pub/Sub; Pub/Sub pushes it to a receiver that accepts only a Google-signed token; the settler captures, pays or voids. An unknown outcome is answered with 503 so the decision is delivered again.
- **AI:** Nemotron 3 Nano and Super through Nebius Token Factory screen every case; Tavily gives live web search; a payment-risk analyst sees numbers and categories, never a name or an address. Each AI layer may only raise risk or tighten a limit.
- **Credit security in seven layers:** booking-bound authorizations, a two-person carrier payee registry, limits and a pause switch, dual approval of a large release, settlement integrity (webhook, payout tracking, freezes), and the AI layers above.
- **Console and API:** Python and Flask backend, Next.js console, Firestore for the case store. Hosted on Render, with Firestore and Pub/Sub in a separate Google Cloud project.
- **Tests:** 2,894 backend tests pass (47 skipped without the Firestore and Pub/Sub emulators) and 1,903 frontend tests pass; `ruff`, `mypy` and `tsc` are clean. No test calls PayPal: they use fakes and recorded sandbox answers.

## What changed during the Submission Period

The baseline is the Nebius submission (tag `nebius-baseline`), which had no payment code. Everything after that tag is the PayPal work: the PayPal client, the payment checks, the settler, the booking and payments pages, and credit-security layers one to seven. `git diff --stat nebius-baseline HEAD` shows it; the README lists each part and its files.

## Challenges

- **Never paying by accident.** Every release path fails closed: a missing, unreadable or placeholder value means "not verified", never "fine". Each refusal is a named status and pays nothing.
- **The first real booking found a gap.** A person's release or block, for a case booked through the console, was recorded but not published, so the settler never saw it and a block could not void the hold. We fixed it and pinned it with tests. The fix is now shown end to end on the live service.
- **An agent that starts suspended.** Until a person publishes what the agent may do, it can analyse and propose but not act. That is the safe default, and it caught us out when nothing was being published.

## Accomplishments we are proud of

- The core rule holds in code and in tests: a model can raise risk and never lower it, and the code that moves money never calls a model.
- A real sandbox booking: an order created, approved by a sandbox buyer and authorized, holding 1,300 USD.
- Measured, with the limits stated. On an invented set of 80 payouts (36 fraudulent, 44 honest), the deterministic checks catch 24 of 36 with 0 of 44 false flags; adding a carrier look-up reaches 32 of 36 with 6 of 44 false flags. These rates describe that invented set only and are not a forecast.

- Run for real on the live service on 5 Nov 2026, sandbox money only: a Block voided a 1,300 USD hold (PayPal answered 204), and a Release captured it (201) and paid the carrier through Payouts (201). The webhook is registered and verified real signed events from PayPal (authorization created, capture completed, payout batch, payout item succeeded), and the second payout moved from `unconfirmed` to `confirmed` from the payout-item event. No refund or dispute event has arrived yet.

## What we learned

A payment agent is mostly a set of refusals. The useful work was deciding what it must never do, writing each refusal down as a named status, and testing the refusals more than the happy path.

## What's next

- Get real refund and dispute events through the webhook against the sandbox.
- Measure the payment-risk analyst (`scripts/evaluate_payment_ai.py --live-analyst`); it is not measured yet.
- Close the known gap that two settlements at the same moment can together pass the daily limit.
- Separate "payout accepted by PayPal" from "money received".

## Built with

Python · Flask · Next.js · TypeScript · PayPal REST (Orders v2, Authorizations, Payouts) · Nebius Token Factory (Nemotron 3 Nano and Super) · Tavily · Google Firestore · Google Pub/Sub · Render

## Links

- Live demo: https://floorline-pay.onrender.com (Continue as guest judge)
- Repository: https://github.com/hoachauphuoc/floorline-pay (MIT licence; made public before submission)
- Demo video: YOUTUBE_URL_PENDING
