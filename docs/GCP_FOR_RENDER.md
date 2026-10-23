# The Google Cloud side of the Render demo

Render runs the app. A Google Cloud project of its own holds two things: **Firestore**, the store, so a restart of Render loses nothing,
and **Pub/Sub**, which carries a released decision to the settler. Without Pub/Sub the settler never runs: a person's release is published to the
`case-decisions` topic, and Pub/Sub pushes it back to `POST /api/v1/receiver/decision`, which is where the money is captured and paid
(`src/vf_logistics/decision_receiver.py`, `settler.py`). The contract of that endpoint is [RECEIVER_CONTRACT.md](RECEIVER_CONTRACT.md).

Use a **new project**. The Nebius submission `vf-app` lives in another project and must not change. Run the commands below in a folder
outside the repository: the key file in step 6 must never be committed.

PowerShell, one line each. Replace `floorline-pay-demo` with your project id (it must be unique across Google Cloud).

## 1. Project, billing and APIs

```powershell
gcloud projects create floorline-pay-demo
gcloud config set project floorline-pay-demo
```

Google may ask you to link a billing account before it lets you use Firestore and Pub/Sub (Console, Billing). The free quotas cover this demo; set a
budget alert (Console, Billing, Budgets and alerts) all the same.

```powershell
gcloud services enable firestore.googleapis.com pubsub.googleapis.com iam.googleapis.com
```

## 2. Firestore

```powershell
gcloud firestore databases create --location=asia-southeast1 --type=firestore-native
```

The indexes are in `infra/firestore.indexes.json`. Create them with the Firebase CLI, from a scratch folder:

```powershell
mkdir fs-indexes; cd fs-indexes
copy <path to the repository>\infra\firestore.indexes.json .
'{"firestore":{"indexes":"firestore.indexes.json"}}' | Out-File -Encoding ascii firebase.json
firebase deploy --only firestore:indexes --project floorline-pay-demo
```

The CI emulator does not check indexes, so this is the first time they are tested. If a page later fails with a message that a query needs an
index, the message carries a link that creates it; add the same index to `infra/firestore.indexes.json`.

## 3. The topic

```powershell
gcloud pubsub topics create case-decisions
```

## 4. A service account for Render

```powershell
gcloud iam service-accounts create floorline-render --display-name="Floorline Pay on Render"
gcloud projects add-iam-policy-binding floorline-pay-demo --member="serviceAccount:floorline-render@floorline-pay-demo.iam.gserviceaccount.com" --role="roles/datastore.user"
gcloud pubsub topics add-iam-policy-binding case-decisions --member="serviceAccount:floorline-render@floorline-pay-demo.iam.gserviceaccount.com" --role="roles/pubsub.publisher"
```

Keep the quotes: unquoted, PowerShell turns some characters into separators.

## 5. A key for it

```powershell
gcloud iam service-accounts keys create gcp-key.json --iam-account=floorline-render@floorline-pay-demo.iam.gserviceaccount.com
```

If this is refused, an organisation policy blocks key creation for the project; use a project outside that organisation.

In the Render dashboard, open the service, Environment, Secret Files, add a file named `gcp-key.json` and paste the content of this file. Render
mounts it at `/etc/secrets/gcp-key.json`, which is the value of `GOOGLE_APPLICATION_CREDENTIALS` in `render.yaml`. Then delete the local
`gcp-key.json`.

## 6. The Render environment

In the dashboard (see [RENDER_DEPLOY.md](RENDER_DEPLOY.md)):

| Variable | Value |
|---|---|
| `PROJECT_ID` | `floorline-pay-demo` (your project id) |
| `RECEIVER_PUSH_SA` | `floorline-render@floorline-pay-demo.iam.gserviceaccount.com` |
| `RECEIVER_PUSH_AUDIENCE` | `https://<your Render address>/api/v1/receiver/decision` |

Do not set `ANONYMOUS_ROLE`: with Firestore and a role that grants writes, the app refuses to start.

## 7. The push subscription

It needs the Render address, so do it after the first deploy. `<number>` is the project number:

```powershell
gcloud projects describe floorline-pay-demo --format="value(projectNumber)"
gcloud beta services identity create --service=pubsub.googleapis.com
gcloud iam service-accounts add-iam-policy-binding floorline-render@floorline-pay-demo.iam.gserviceaccount.com --member="serviceAccount:service-<number>@gcp-sa-pubsub.iam.gserviceaccount.com" --role="roles/iam.serviceAccountTokenCreator"
gcloud pubsub subscriptions create case-decisions-receiver --topic=case-decisions --push-endpoint=https://<your Render address>/api/v1/receiver/decision --push-auth-service-account=floorline-render@floorline-pay-demo.iam.gserviceaccount.com --push-auth-token-audience=https://<your Render address>/api/v1/receiver/decision --ack-deadline=60
```

The acknowledgement deadline is 60 seconds because a release makes the settler call PayPal twice (capture, payout) before it answers.

## 8. Check it

1. Book freight on `/book` with the sandbox buyer account, sign in as a reviewer, release the case.
2. `/payments` shows the capture and the payout. If it stays at "awaiting a decision", the push did not arrive: in the Console, Pub/Sub,
   Subscriptions, `case-decisions-receiver`, look at the undelivered messages and the response codes. 403 means the service account or the audience in
   Render does not match the subscription; 503 means the settler asked to be retried.
3. Repeat delivery is safe: `GET /api/v1/receiver/applied/<idempotency key>` returns `applied_once: true` and counts each delivery.
4. Restart the Render service: the carrier registered by the seed and the frozen party are still there.

## What is not turned on

Model Armor (Google's screen for prompt injection) and Cloud Storage (document archive, a bigger sanctions index) are not set up. Without them
the app keeps working: documents are not archived and the sanctions screen uses the index shipped in the image. Whether the missing Model Armor
changes the verdict of a case that carries an uploaded document has not been measured.

## Keeping it running

The demo has to stay open until judging ends on 15 Dec 2026. The service account key does not expire by itself, but deleting the project, the key
or the billing account stops the demo. Delete the key and the project afterwards if you do not need them.
