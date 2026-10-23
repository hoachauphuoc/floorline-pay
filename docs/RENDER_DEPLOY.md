# Deploying the Floorline Pay demo on Render

One Docker web service, built from `Dockerfile`, described by `render.yaml`. The store is in memory: a restart empties it and
`VF_SEED_PAYMENT_DEMO` puts the demo's starting state back (a carrier with a verified payee, a second carrier frozen as demo data).
Every payment a judge sees is one that judge made. The demo is PayPal sandbox only (`api-m.sandbox.paypal.com` is fixed in the code).

The hackathon rules ask for a free, unrestricted demo until judging ends on 15 Dec 2026. `VF_ONE_CLICK_UNTIL` is set to 16 Dec 2026 for that reason.

## 1. Create the service

1. Push the branch you want to deploy to GitHub.
2. Render dashboard, New, Blueprint, pick this repository. Render reads `render.yaml` and creates `floorline-pay`.
3. Claim the hackathon credit under Billing before the first paid instance is created. The Starter plan keeps the service awake.
4. Fill the `sync: false` values (below). Nothing secret is in the repository.
5. After the first deploy, copy the service address (for example `https://floorline-pay.onrender.com`) into
   `PAYMENT_RETURN_ORIGINS`, then redeploy once.

## 2. Values to enter in the dashboard

| Variable | What to put | Notes |
|---|---|---|
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | the sandbox app's keys | sandbox only |
| `PAYPAL_WEBHOOK_ID` | the id PayPal shows after step 4 | webhook receiver stays off until set |
| `VF_DEMO_PAYEE_EMAIL` | the sandbox personal account that receives payouts | the seed registers it as the carrier's verified payee |
| `PAYMENT_RETURN_ORIGINS` | the service address | where PayPal may send a shipper back |
| `VF_OPERATORS` | records for the demo reviewers | see step 3 |
| `ADMIN_EMAILS`, `OPERATOR_EMAILS`, `REVIEWER_EMAILS` | the same people, comma separated | roles are granted by these lists |
| `NEBIUS_API_KEY`, `TAVILY_API_KEY` | the model and search keys | without `TAVILY_API_KEY` every case carries `CARRIER_IDENTITY_UNVERIFIED` |
| `VF_TENANT_SPEND_CEILING_USD` | in `render.yaml`, 20 by default | model spend stops at this figure; change it to what you will pay |

## 3. The demo reviewers

A release at or above 2500 USD needs a second person, so the demo needs two people who sign in with a password. A one-click guest can
look around and book but can never release or approve.

```bash
node frontend/scripts/make-operator.mjs reviewer-a@floorline.example --out ./tmp-reviewer-a
node frontend/scripts/make-operator.mjs reviewer-b@floorline.example --out ./tmp-reviewer-b
```

Each directory then holds `record.txt` (goes into `VF_OPERATORS`, records separated by `;`) and `password.txt` (goes into the README's
"Test access" section, since judges need it). Delete both directories afterwards. Add both addresses to `ADMIN_EMAILS` (one of them
needs to lift a freeze) and `OPERATOR_EMAILS`.

## 4. The PayPal webhook

PayPal developer dashboard, the sandbox app, Webhooks, Add webhook:

- URL: `https://<service address>/api/v1/paypal/webhook`
- Events: `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.REVERSED`, `PAYMENT.CAPTURE.REFUNDED`, `CUSTOMER.DISPUTE.CREATED`,
  `PAYMENT.PAYOUTS-ITEM.SUCCEEDED`, `PAYMENT.PAYOUTS-ITEM.FAILED`, `PAYMENT.PAYOUTS-ITEM.RETURNED`, `PAYMENT.PAYOUTS-ITEM.BLOCKED`

Copy the webhook id into `PAYPAL_WEBHOOK_ID` and redeploy. The receiver answers 401 to anyone PayPal does not vouch for.

## 5. Check it

- `/health` answers 200.
- Sign in as the guest judge: Floorline Pay overview, Defense (ten attacks, none moves money), Settlement (the demo-frozen carrier).
- Sign in as reviewer A: book freight on `/book` with the sandbox buyer account, release, see the payout.
- Restart the service: the carrier and the frozen party come back.

The web service address is the only thing that has to be public. Do not open the Flask port; the container keeps it on 127.0.0.1.
