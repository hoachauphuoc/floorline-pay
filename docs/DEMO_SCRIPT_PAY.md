# Demo video script for Floorline Pay: six scenes, 160 seconds

The Devpost video for the PayPal AI Hackathon. The older `docs/DEMO_SCRIPT.md` belongs to the Nebius submission and is not this video.

Every claim below names where the evidence is: a section of `README.md`, or a line of the live service. A scene marked **TO RECORD** shows something that has not run yet; do not shoot it until it has, and do not say its narration until the screen shows it.

## What the cut must satisfy

- Under three minutes (this cut is 160 seconds).
- Real footage of the real product on the PayPal sandbox. Say once, in scene 1, that it is the sandbox and no real money moves.
- The data is synthetic. No API key, password, upload token or sandbox buyer password is visible in frame or in the exported file; blur the address bar if it carries a token.
- No third-party marks other than PayPal's own logo as it appears in the product, and no copyrighted music.
- NVIDIA Nemotron and Nebius Token Factory are named once, in scene 2, because the case trace shows them.
- English narration and captions.

## Length table

| Scene | What it shows | Length | Status |
|---|---|---|---|
| 1 | Book, approve on PayPal, money held | 25s | can record now |
| 2 | The risk floor: findings that only raise | 30s | can record now |
| 3 | A person decides; the agent starts suspended | 30s | can record now |
| 4 | Settle: block voids, release captures and pays | 35s | can record now (real run done 5 Nov) |
| 5 | What was measured, and what was not | 25s | can record now |
| 6 | Close | 15s | after scene 4 |
| | | **160s** | |

## Scenes

### 1. Hold at booking (25s)

**Screen.** `/book`: the form with the PayPal logo, press *Pay with PayPal*, the PayPal sandbox page where the buyer approves, back to the case. Caption: "PayPal sandbox: no real money".
**Narration.** "Freight is paid on trust. A shipper books here and approves the payment on PayPal's own page. PayPal holds the money; it does not take it yet. Everything you will see runs on the PayPal sandbox, so no real money moves."
**Source.** README, *How the money moves*, step 1. The 1,300 USD hold ran on the real sandbox on 24 and 30 Oct 2026 (README, *Try it in two minutes*).

### 2. The risk floor only goes up (30s)

**Screen.** The case page: the Payment card reading *Funds held, awaiting a decision*, then the findings list (for example *Payee missing, floor 75*) and the model line in the trace (Nemotron on Nebius Token Factory).
**Narration.** "Floorline reads the paperwork as untrusted input and sets a risk floor with plain code. Nemotron agents, through Nebius Token Factory, may argue a case above that floor. Nothing they say can take it below. Here, no payee is on file, so the floor is seventy-five."
**Source.** README, *The design rule* and the findings table (`PAYEE_MISSING`, floor 75; `check_payment()` only adds findings).

### 3. A person decides (30s)

**Screen.** Governance page: *Agent suspended, no delegation boundary published*, then the published boundary (`BOUNDARY-v1`, auto release not permitted). Then the case page with the reviewer's Block button; then a guest session refused when it tries to release (*Releasing a shipment needs a password sign-in*).
**Narration.** "The agent starts suspended. It can analyse and propose; it cannot act until a person publishes what it may do, and ours may not release anything on its own. A person decides every disputed case, and releasing needs a password, so a guest can look but cannot pay."
**Source.** README, *Limits, stated* (the agent starts suspended) and *Signing in*; the refusal text is in `orchestrator.human_decide`.

### 4. Settle only what a person decided (35s) · run for real 5 Nov 2026, still to film

**Screen.** Case A: *Block*, then the Payment card changes to *returned to the shipper* with the PayPal void id, and `/payments` lists it. Case B: *Release* by a password account, then the card shows the capture id and the payout id, and `/payments` lists *paid*.
**Narration.** "A published block voids the hold, and the money goes back to the shipper. A published release makes the settler check the payment rules once more, capture the money and pay the carrier with PayPal Payouts. Each PayPal call carries a request id built from the decision, so a repeat cannot pay twice."
**Source.** README, *How the money moves*, step 4. A Block and a Release have each completed on the live service (`CASE-SHP-PAY-B1105A` voided, `CASE-SHP-PAY-R1105A` paid) and `/payments` shows them. Film fresh cases; say "accepted by PayPal", never "received by the carrier".

### 5. Measured limits (25s)

**Screen.** The README table *What the AI layers add, measured on invented data*, with the last row (*payment-risk analyst: not measured*) highlighted.
**Narration.** "On an invented set of eighty payouts, the deterministic checks catch twenty-four of thirty-six frauds and flag none of the forty-four honest ones. Adding a carrier look-up reaches thirty-two, at the cost of six false flags. Whether the AI analyst adds anything is not measured yet, and these rates are not a forecast."
**Source.** README table in *What the AI layers add, measured on invented data* (24 of 36, 0 of 44; 32 of 36, 6 of 44; analyst not measured).

### 6. Close (15s)

**Screen.** The case list with one voided and one paid payment side by side, then the end card: repository address and the live demo address.
**Narration.** "Money moves only on a person's decision, behind guardrails no model can lower. Floorline Pay: the agent that cannot pay what the rules held."
**Source.** README opening and *Credit security* (the AI layers may only raise risk or tighten a limit; the settler never calls a model).
**If scene 4 is not recorded,** drop the word "paid" and close on the held and blocked states only; say nothing about a payout.

## Before you press record

1. Deploy the latest commit and check `/health` reports `store.backend` as `firestore`.
2. Book two new cases with the same booking data and the sandbox payee registered and verified on the Carriers page. Use one for the block and one for the release.
3. Publish the delegation boundary once (scene 3 shows this); it cannot be shown if it is already published, so record that scene first or revoke and republish.
4. Record at 2560x1440 and export at 1920x1080. Cut waiting time and caption it "Time condensed".
5. Check the export frame by frame for tokens, addresses and passwords.
