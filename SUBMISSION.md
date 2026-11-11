# Devpost Submission — Floorline

> This file is the Nebius submission (service `vf-app`, frozen). Floorline Pay, the PayPal submission, is described in `README.md` and `docs/DEVPOST.md`.

Copy-paste material for the Devpost form. The shooting script for the demo video is
`docs/DEMO_SCRIPT.md`, and the narration it is timed to lives in
`scripts/build_narration.py`.

---

## Project name

Floorline

Named for the design rule: deterministic checks set a risk floor for every shipment,
and the agents may argue a case above that line but never below it. (VF Logistics is
the freight operator in the demo; the repository and the Cloud Run service keep that
name.)

## Elevator pitch (200 char limit on Devpost)

<!--
194 characters. Counted, not estimated -- check before editing:
python -c "print(len(open('SUBMISSION.md', encoding='utf-8').read().split(chr(10))[N-1]))"

It now names Tavily, the bonus target, and no longer says the debate's verdict "decides":
it can only send a disputed case to an investigation it would otherwise skip, and a person
decides every dispute. The models are named in the Built on paragraph directly below.

It now opens with the name and names NVIDIA as well as Nebius: the organisers ask that
the required tools be impossible to miss, and the previous pitch said "Nemotron" without
ever saying "NVIDIA".

The previous version spent its whole budget on mechanism ("Senior Auditor debates Junior
Analyst with function calling. A Delegation Boundary, not the agents, decides.") and never
said WHY the models are split that way. The delegation boundary is carried by the Track
section, the video's longest clip and the whole governance screen; the floor rule was
stated nowhere a judge reads first, which made the one non-obvious decision in the system
invisible at the point of first contact.
-->

Floorline: shipment-fraud agents on NVIDIA Nemotron 3 via Nebius Token Factory, with live Tavily search. Rules set a risk floor the agents may raise, never lower; a person decides every dispute.

**Built on:** **Nebius Token Factory** for every model call (seven agents), running
**NVIDIA Nemotron 3 Nano** (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`) on fraud,
compliance, HS classification and the zero-day radar, and **NVIDIA Nemotron 3 Super**
(`nvidia/nemotron-3-super-120b-a12b`) on investigation and the Senior Auditor debate.
Nemotron 3 Ultra was measured against Super on the debate and not adopted. Scanned documents are
read by MiniCPM-V-4.5, also on Token Factory, because no NVIDIA vision model is offered
there. **Tavily** live web search is called at runtime by compliance screening, route
validation, investigation, the zero-day radar, the debate and the Governance page's
sanctions scan; in the zero-day radar and the debate, Nemotron calls the search as a tool
and chooses the query.

## Track

Best Apps and Agents

## Bonus award targeted

Best Use of Tavily ($3,000) — every case that reaches the models gets a Tavily search
on its shipper and receiver before compliance screening decides. The search is live
unless the same name was searched in the last hour, when that result is reused, and the
case trace marks which searches came from the cache. Five more call sites use Tavily, and
in the debate and the zero-day radar Nemotron calls it as a tool and chooses the query
itself.

## Hosted project URL

**Live demo:** https://vf-app-350828852747.asia-southeast1.run.app
**API:** the same URL, under `/api/v1` — `GET /health`, `GET /api/v1/openapi.json`

One Cloud Run service runs the console and the API in one container. The console is
signed-in only: an anonymous visitor is sent to the sign-in screen, and **Continue as guest judge** signs in with one click. Recording a review
decision needs a password account; credentials are in the private testing-instructions field below.

The live service is Cloud Run revision `vf-app-00047-228`, deployed from
commit `76d015e` (`GET /health` reports the revision). It includes the 21 Oct deploy (the
debate on Nemotron 3 Super after the measured comparison, the model-comparison card on the
Evaluation page, a development CORS origin removed from every response), the N01 audit-row
fix and the receiver's handling of status notifications (22 Oct), and the changes of 14 Oct 2026:
the 95% intervals and the operating curve on the Evaluation page (`896bbae`), the stricter
checks on automated release and the registry-only learning discount (`711e3e6`), the
shipper history read fresh before each risk adjustment (`b5cd237`), the tenant-scoped
document bucket (`17864e0`), and the Firestore store's refusal of an unversioned write
over another tenant's case (`d76e64e`); and the verification-loop fixes of 16–17 Oct
(`3e6b1dd` to `443c524`) and the work up to 20 Oct.

---

## Text description

### Summary

**Floorline** screens shipments for a freight forwarder's compliance desk. Deterministic checks
set a risk **floor** for every shipment; Nemotron agents on **Nebius Token Factory**, with live
**Tavily** search, may argue a case above that floor and never below it, and a person decides
every disputed case. (`reconcile()` takes the higher of the model's score and the floor;
`validate()` raises if an agent's findings ever lowered it.)

- **The problem.** Fraud in a forwarder's book is rare (0.18% of Vietnam's 2024 customs
  declarations), so almost every hold is honest. The job is to hold the right shipments and
  make each decision quick and defensible.
- **What was measured**, on a synthetic corpus, with 95% intervals. The rules alone catch
  93.5% [90–96] of fraud and hold 38.4% [28–49] of honest shipments. Where the rules score a
  case 0, Nemotron 3 Nano flagged 46 of 52 frauds and the larger Super 47 of 52, at 4.3x the
  cost, so fraud screening stays on Nano. The debate began on Nemotron 3 Ultra; across 46
  labelled cases it was no better than Super and cost 5.2x as much, so it runs on Super.
- **The limits, stated.** The corpus is synthetic and no customer traffic has been measured.
  Adding the model layer raises the share of honest holdout shipments held to about 73%. The
  debate does not tell fraud from honest trade (DISAGREE on 73% of frauds and 72% of honest
  cases on Super), because the auditor sees the rules' findings; it routes a held case to an
  investigation and can never clear one.
- **Try it.** The console is at the hosted URL above: **Continue as guest judge** signs in
  with one click. Upload `sample_docs/injected_bol.pdf` on the DevOps page to watch Model Armor
  block a prompt injection before any model reads it, and open `/evaluation` for every figure
  above. The sections below hold the detail.

### The idea: agents that can raise risk, never lower it

Most autonomous-agent systems get safer by making the model better. This one gets safer by
making the model's opinion unable to help in one direction.

`verifier.py` computes a **deterministic risk floor** from arithmetic and list lookups — no
model calls, no network. An agent may **raise** risk above that floor. It may never lower
it. The asymmetry is the point: escalating on model judgement is acceptable, exonerating on
model judgement is not, because a wrong exoneration releases contraband while a wrong
escalation costs a human ten minutes.

Two consequences, and both were measured rather than assumed:

- **Nemotron 3 Nano on fraud and compliance is a choice the floor makes safe, and Super did
  not beat it.** A weaker model there can fail to add a hold, but it can never release a
  shipment the rules stopped: auto-clear needs a floor of zero. Where the floor is zero the
  model is the only check, so that is where a larger model could have added something. On
  the 179 corpus cases the rules scored 0 (52 fraud, 127 honest), Nano flagged 46 of the 52
  frauds (88%, 95% interval 77–95) and Super 47 of 52 (90%, 79–96): the sample shows no
  difference, and Super costs 4.3x as much per case ($0.00114 against $0.00026). The cost
  of that layer is also measured: on the holdout half Nano flagged 21 of the 24 frauds the
  rules missed and held 30 of the 53 honest shipments the rules had passed, which with the
  rules' own 33 honest holds is 63 of 86 honest holdout shipments (73%) against 38.4% for
  the rules alone. A reply that failed to parse or validate counts as a hold in those
  figures, because the orchestrator refuses auto-clear after a model failure (5 of 179 on
  Nano, 1 of 179 on Super).
- **The debate is the one hop whose output is a verdict on a dispute rather than a score
  the floor overrides, so it was the one place a stronger model might matter; Ultra did
  not earn its rate there.** It fires when the floor exceeds the model's score by 15
  points or more, so auto-clear is already impossible and a person decides the case either
  way. It emits a reasoned CONFIRM or DISAGREE on whether the junior analyst missed
  something. CONFIRM changes nothing. A genuine DISAGREE at confidence 0.7 or above sends
  the case to a deep investigation on Super that it would otherwise skip (when effective
  risk is under 70 and compliance is not already `BLOCKED` or `REVIEW_REQUIRED`), and may
  raise its risk, never lower it. We debated 46 disputed corpus cases (25 fraud, 21
  honest) on both models. Ultra reached a verdict on 37 of 46 against Super's 40 of 46,
  cost $0.0108 per debate against $0.0021 (5.2x) and took 13.5 seconds against 6.4. Neither
  separated fraud from honest trade: Super said DISAGREE on 16 of 22 frauds (73%) and 13 of
  18 honest cases (72%), Ultra on 18 of 19 (95%) and 17 of 18 (94%). Ultra simply
  disagrees more. So the debate runs on Super. The result is also a limit on the debate
  itself: the auditor is shown the rules' findings and every honest case in the sample was
  one the rules held, so DISAGREE largely tracks the rules. It routes a held case to an
  investigation; we do not claim it judges independently.

The invariant is checked rather than asserted. `reconcile()` sets effective risk to the
higher of the model's score and the floor, and `validate()` computes the floor twice, with
and without the HS classifier's and zero-day radar's findings, and raises
`AssertionError` if they lowered it. That check runs on every validation.

### The problem

Fraud in a forwarder's book is rare, and that is what makes it expensive to find. In 2024
Vietnam Customs processed 16.84 million declarations, found a violation in 29,849 of
them (0.18%), and sent 3.45% to physical inspection to find them
([customs.gov.vn](http://customs.gov.vn:8228/index.jsp?pageId=2&aid=208927&cid=24)):
even the authority's own targeting holds about nineteen declarations for every one that
is wrong. A forwarder's compliance desk lives inside the same ratio. Whatever it holds is
mostly honest, and every hold is a person's time and a customer's delay.

The signals are weak alone and damning together. A freight charge 60% under the lane's
history looks like a promotion; a shipper with two lifetime shipments looks like a new
customer. A rules engine reads them one at a time, and an analyst has no time to read them
together for every booking.

So the job is two jobs: hold the right shipments, and **make every hold quick and
defensible to decide**. The second is where the time goes, and it is what a
due-diligence dossier with a regulation behind every finding is for.

And detecting fraud is still only half the problem. The harder question is: **who
decides what the agent may do about it?** An agent that reasons well is not the
same as an agent you can hand authority to.

### Why now

- Vietnam's **Decree 259/2025/ND-CP** on strategic trade control (dual-use goods) has been
  in force since 10 Oct 2025, and **Decree 169/2026/ND-CP** on customs penalties since
  1 Jul 2026. A customs broker carries the declarant's obligations (Customs Law
  54/2014/QH13, Art. 20(4)).
- **Regulators are naming forwarders.** OFAC settled with Toll Holdings (2022), C.H.
  Robinson's non-US subsidiaries (2024), Fracht FWO (2025) and Pegasus Worldwide Logistics
  (9 Oct 2026) over shipments they moved. Every one was a screening failure.
- The BIS orders behind our public test cases route controlled US goods through the UAE,
  Hong Kong, Turkey, the Maldives and Central Asia — this corridor's transhipment hubs.

### Who it is for

**The compliance desk at a freight forwarder or customs broker** — the few people who
must screen every shipment that crosses their books before it moves, against sanctions
lists, export controls and their own fraud history, and who answer for each release to a
customs authority afterwards.

What Floorline changes for them, in figures measured in this repository rather than
estimated:

- **The clear cases close themselves, inside limits the desk sets.** The agent releases
  only what the published delegation boundary allows, and every release records the
  boundary version and rule that permitted it.
- **What stops reaches a person with its evidence attached** — the findings with the floor
  each one forces, the sanctions list it was screened against and that list's date, the
  live sources the compliance screen read, and the document itself. **One click turns the
  case into a due-diligence dossier (PDF)** for the compliance file: every finding with the
  action it calls for, the regulation it rests on, and a comparable public enforcement
  case. On a held-out synthetic split the deterministic layer catches 93.5% of fraud
  [90–96] at 91.2% precision, and the HS classifier catches 11 of 12 disguised controlled
  goods on a holdout while flagging 0 of 12 honest ones. It still holds 38.4% of clean
  shipments [28–49] for a person — at 0.2% fraud, about 206 holds per real case — which
  is the open weakness and is reported as one. Brackets are 95% Wilson intervals, served
  with every report at `/evaluation` by a build that includes `896bbae`.
- **Tested on real cases, not only our own.** 19 public enforcement cases from BIS, OFAC,
  US Commerce, EPPO and OLAF, each rebuilt as the shipment the paperwork showed and paired
  with the honest trade it imitated, screened against the real OFAC SDN and UN lists
  (18,525 records): the rules plus the HS classifier and zero-day radar held 14 of the 19
  frauds (73.7% [51–89]; rules alone 11, 57.9% [36–77]), and 9 of the 19 honest
  counterparts with them (rules alone 7), for $0.034. Origin fraud, decided by where the
  inputs came from, is invisible at booking, and we say so.
- **Screening costs cents, not analyst hours.** Model spend on a 20-case run is
  `$0.068`–`$0.088`, under half a cent per case; the binding cost is web search, and the
  pre-filter resolves the plainest cases with no model call at all.
- **The record stands up afterwards.** Every decision names the signed-in person or the
  boundary that allowed it, refusals are recorded like successes, and the audit trail is
  append-only.

The synthetic corpus is stated as such: these are the system's measured behaviour on
held-out data and on reconstructed public cases, not field results from a customer.

### What we built

A multi-agent system on Cloud Run where **seven** specialised agents share one
shipment record — six running NVIDIA Nemotron models on **Nebius Token
Factory**, one running a vision model for document reading — and a
**governance layer** that determines which of those agents' recommendations
actually execute.

One case, end to end, with no person in the loop until the boundary says so:

> scanned document → **Model Armor** → document intake (MiniCPM-V) → deterministic
> **risk floor** and pre-filter → fraud + compliance in parallel (**NVIDIA Nemotron 3
> Nano**, live **Tavily** search) → HS classifier and zero-day radar (Nano) → auto-debate
> when the floor exceeds the model's score by 15+ (**Nemotron 3 Super**, calling tools:
> re-ask Nano, search again) → investigation when risk, compliance or a confident
> DISAGREE calls for it (**Nemotron 3 Super**, Tavily) → **delegation gate** → tools:
> release, hold, assign analyst, draft SAR, webhook, Pub/Sub → audit trail with per-hop
> lineage

The case trace in the console opens on exactly this chain for the case in front of
you, with the model that ran each hop, so it can be checked rather than believed.

**Document Intake Agent** — reads a real shipping document (bill of lading,
commercial invoice, packing list). Rasterises the PDF's first page and sends
it to **MiniCPM-V-4.5**, a vision model on Token Factory (NVIDIA does not
publish a vision model there yet). Transcribes into the structured shipment
record the rest of the pipeline understands. Runs at `temperature=0.0` because
this is transcription, not generation.

**Fraud Detection Agent** — screens seven fraud families (price manipulation,
route fraud, weight/dimension fraud, document fraud, identity fraud, duplicate
billing, time fraud) on **NVIDIA Nemotron 3 Nano**. Returns `risk_score`
0–100, `risk_level`, a `flags[]` array with per-finding severity, and an
explicit `confidence`.

**Compliance Screening Agent** — trade and regulatory compliance and AML indicators on
**NVIDIA Nemotron 3 Nano**, grounded by a **real Tavily search** for the shipper and
receiver names before scoring. Beneath it, sanctions screening is deterministic: every
party is matched by name and identifier against the **OFAC SDN and UN Security Council
lists** (18,525 records, rebuilt by `scripts/refresh_sanctions.py` from the published
files). A full sanctions designation floors the case at 100, and an export-control,
debarment or sanctions-linked match floors it at 85. Both are above the investigation
bar of 70, so the case is investigated and waits for a person whatever the model scores.

**AI Investigation Agent** — multi-step case investigation across related
shipments, pattern analysis, network mapping and consolidated reporting, on
**NVIDIA Nemotron 3 Super**. It runs only where compliance returned `BLOCKED` or
`REVIEW_REQUIRED`, effective risk reached 70, or the Senior Auditor confidently
disagreed, so it is a low-volume call that can afford a larger model than the screening
hops.

**Multi-Agent Debate (Senior Auditor)** — **NVIDIA Nemotron 3 Super** reviews the
fraud agent's assessment through native function calling, with three tools it may
choose to invoke: request re-evaluation, search Tavily, render verdict. It fires
**automatically** when the deterministic floor exceeds the model's score by 15 points
or more, and a reviewer can also run it by hand via *Deep Review* on any case awaiting
review. The automatic verdict can change routing in one direction only: a DISAGREE the
model genuinely rendered, at confidence 0.7 or above, sends the case to a deep
investigation it would otherwise skip (effective risk under 70, compliance not already
`BLOCKED` or `REVIEW_REQUIRED`), while a CONFIRM, a low-confidence verdict or a forced
default leaves the routing to the floor. It can escalate a case and can never clear one,
and a person decides every disputed case. A manual Deep Review is recorded for the
reviewer and reroutes nothing.

**HS Classification Agent** — **NVIDIA Nemotron 3 Nano**. The deterministic checks
in `verifier.py` can compare a declared HS code against a dual-use prefix list, but
they cannot tell whether the code matches the cargo actually described. This agent
can, which is what catches a correctly-formatted code on the wrong goods.

**Zero-Day Radar** — **NVIDIA Nemotron 3 Nano**, adverse-media screening for
entities the official sanctions lists have not caught up with yet. A list is always
behind the news; this closes that window.

### Model selection, chosen per task

The model is not a single global setting. Fraud detection and compliance
screening run on **Nemotron 3 Nano** because every shipment that reaches the models is
scored by both — this is the highest-volume call in the pipeline, and it needs to be
fast and cheap. Investigation runs on **Nemotron 3 Super** because by the time
a case reaches it, the fraud and compliance findings already exist —
investigation synthesises rather than makes the primary judgement, but it
still benefits from a stronger model since the reasoning is multi-hop.

The debate began as the one place **Nemotron 3 Ultra** ran, for an architectural reason
rather than a budgetary one. Everywhere else `verifier.py` computes a deterministic risk
floor that an agent may raise but never lower, so a weaker model can miss a hold but can
never release what the rules stopped. The debate is the exception: it runs only when the
floor exceeds the model's score by 15 or more, so a person decides the case either way,
and what it emits is not a score awaiting override but a reasoned CONFIRM or DISAGREE on
whether the junior analyst missed something. That made it the one place worth testing a
stronger model, and we tested it: on 46 labelled disputed cases Ultra reached a verdict
less often than Super (37 of 46 against 40 of 46), cost 5.2x as much and was no better at
telling fraud from honest trade (DISAGREE on 95% of frauds and 94% of honest cases, against
Super's 73% and 72%). A CONFIRM changes nothing and a genuine DISAGREE at confidence 0.7 or
above can send the case to an investigation it would otherwise skip; nothing it says can
clear one. The debate runs on Super, and `DEBATE_MODEL` puts Ultra back without a deploy.
Document intake needs a vision model NVIDIA does not currently offer on
Token Factory, so it runs on **MiniCPM-V-4.5**, which the catalog specifically calls out
for OCR/PDF work.

All seven agents are reached through the same OpenAI-compatible client
(`nebius_client.py`), so the split adds no integration surface. Every agent
response envelope records the model that produced it, and the console's
per-case trace shows it on each hop.

### A real Tavily call -- 6 integration points

Tavily is woven throughout the pipeline, not just compliance:

1. **Compliance screening** -- `compliance_agent.py` searches shipper and receiver
   names for sanctions, fraud, and shell company indicators before every screening
   call, through `tavily_client.search_cached()`: a name searched in the last hour reuses
   that result (`TAVILY_ENTITY_CACHE_TTL_SECONDS`, default 3600), and the hop records
   live and cached searches separately. Results are injected as labelled, untrusted
   evidence into the LLM context.

2. **Investigation enrichment** -- `investigation_agent.py` searches for the
   specific fraud pattern identified (e.g. "under-invoicing Vietnam logistics") and
   cross-references shipper + receiver as a pair to find business relationship
   evidence before Nemotron Super generates its deep analysis.

3. **Route validation** -- `orchestrator.py` runs a parallel Tavily search for
   shipping route disruptions ("port congestion", "sanctions", "shipping lane
   disruption") alongside the fraud and compliance agents, cached per lane. The route
   intelligence is attached to the case and shown in its trace for the reviewer; no
   model reads it.

4. **Governance watchlist scanner and entity check** -- the *Scan for sanctions
   updates* button on the Governance page calls `/api/v1/governance/tavily-scan`, which
   searches for recent sanctions additions, OFAC SDN changes, and trade enforcement
   actions; its entity check calls `/api/v1/governance/verify-entity`, which searches
   for evidence that a named company or tax ID actually exists. The point is that the
   code-resident lists on that page can be reviewed against what actually changed,
   rather than only against what a model remembers.

5. **Multi-agent debate** -- `debate_agent.py` exposes `search_tavily` as a tool
   that the Senior Auditor (Nemotron 3 Super) can invoke during function-calling debate
   rounds. It decides at runtime whether to search -- a genuine tool call, not scripted.

6. **Zero-Day Radar** -- `zero_day_agent.py` gives Nemotron Nano a
   `tavily_zero_day_search` tool (news, last 45 days) for adverse media on parties no
   sanctions list has caught up with yet. Code decides whether it may search at all
   (a dual-use heading, a high-risk destination, two or more transhipment hubs, or a
   shipper with at most one prior shipment); the model chooses the queries. A failed
   search is reported as failed, never as "nothing found".

A missing API key or a Tavily outage never blocks the pipeline. Where a model reads the
results (compliance, investigation, the debate and the radar), the search status goes
with them, so a failed search is reported as failed rather than read as "nothing
found".

### Verify the defence layer yourself

The strongest guardrail in the system used to be invisible: 14 injection
patterns plus 15 classes of invisible Unicode, applied to every document before
a model sees it, with nothing in the UI to exercise them. The **Injection probe**
on the Agents page (`/agents`; operator role, which the guest-judge sign-in holds)
now calls `/api/v1/security/screen`, which runs the exact functions the document
pipeline runs -- `untrusted.screen_text()` and Model Armor -- over any text you
paste. Beneath it the page lists real attack samples to paste in: an override
attempt, role injection, score manipulation, a clearance assertion, output
hijacking, tag injection and hidden characters, plus a clean control sample to show
the screen does not simply block everything. Nothing is simulated or replayed.

Worth knowing what the two layers each do, because they are not
interchangeable. The deterministic pass is free and needs no network, and it closes a
real gap -- an injection in document prose that the vision extractor does not carry
into any structured field never reaches the field-level screen, so nothing catches it
when Model Armor is unreachable. But it only ever **flags for a human**; it cannot
block. Its patterns were written for extracted field values, and a legitimate bill of
lading containing `Booking System:` or `pre-approved` trips two of them. A committed
test asserts that exact document is not blocked. Refusing a real shipment on a form
label is a worse failure than screening it a moment later, so the block decision stays
with the purpose-built detector.

### Governance: the part most agent systems skip

Most agent demonstrations treat "the model decided" as the end of the story.
We treat it as the beginning of a different question: **who authorised that
decision?**

The answer in this system is a **Delegation Boundary**: a versioned,
machine-readable policy published by a named human. The boundary says exactly
which actions the agent may take (`release_shipment`, `hold_shipment`,
`assign_analyst`, `draft_sar`, `notify_webhook`, `publish_decision`) and within
what limits — maximum declared value, maximum effective risk, forbidden HS
prefixes, forbidden destinations. Every executed action records the boundary
version that permitted it.

The boundary enforces a simple rule: **an agent may raise risk, never lower it
below a deterministic floor.** `verifier.py` computes that floor from
arithmetic and code-resident reference lists. A successful prompt injection
can manipulate the model's score; it cannot manipulate the floor, because the
floor does not come from the model.

With no active boundary the entire system is **SUSPENDED**. Analysis keeps
running — the agents will still tell you what they found — but protected
actions are refused.

### Input security: documents are untrusted

A shipping document is attacker-controlled. It arrives as a PDF, goes into a
model, and the model's output moves physical cargo. **Google Cloud Model
Armor** screens the document before any model sees it, in overlapping windows
so an injection diluted by surrounding legitimate text is still caught. After
transcription, `untrusted.py` enforces a strict field whitelist —
`avg_route_cost`, `shipper_tx_count` and `created_at` are deliberately
excluded, so a document cannot assert its own clean history.

`shipper_registry.py` resolves the claimed shipper against our own
counterparty book on tax ID **and** company name together, so a forged
document carrying a real customer's tax number under a different name is
reported as `identity_mismatch` rather than inheriting that customer's clean
record.

Seven sample bills of lading are committed, each isolating one mechanism:

```bash
$env:VF_API_KEY = (gcloud secrets versions access latest --secret=VF_API_KEY)
$env:VF_TEST_BASE = "http://localhost:8080"
python scripts/test_documents.py 3
```

The suite clears the board before each pass, because re-uploading a document
returns the existing case rather than re-running it. Against a non-local base it
refuses unless you pass `--yes-wipe-board`, which is there because pointing it at
the live service once destroyed the seeded demo board.

### Features and functionality

- **Seven specialised agents** — Nemotron 3 Nano for fraud, compliance, HS
  classification and zero-day radar; Nemotron 3 Super for investigation and the Senior
  Auditor debate (Ultra measured against it, not adopted); MiniCPM-V-4.5 for document intake — all
  through one OpenAI-compatible client
- **Multi-Agent Debate** — Senior Auditor (Nemotron 3 Super) reviews Junior Analyst
  (Nemotron Nano) using function calling with three tools: request re-evaluation,
  Tavily search, and render verdict. Fires automatically when the floor exceeds the
  model's score by 15 or more; a reviewer can also run it by hand as "Deep Review"
- **A real, runtime Tavily search** in the compliance path (and in route validation,
  investigation, the zero-day radar, the debate and the Governance page's scan)
- **Autonomous multi-step workflow** — conditional routing with no human
  step-through
- **Delegation Boundary** — versioned policy published by a named human; the
  only source of authority for protected actions
- **Fail-closed execution gate** — no boundary, no execution. On the model path, no
  automated release unless compliance returned exactly `CLEARED` with a stated risk floor;
  and on every path, the deterministic pre-filter's included, the gate refuses a release
  without a deterministic finding count (under the default zero-findings rule), a usable
  declared value and, where destinations are excluded, a stated destination
- **Deterministic risk floor** — arithmetic and code-resident lists constrain
  what the agent can claim
- **Two-layer input screening** — Model Armor screens a text-layer PDF in
  overlapping windows before any model is invoked, and an independent deterministic
  pass over 14 injection patterns and 15 classes of invisible Unicode then runs on
  the transcription. Only Model Armor blocks; the deterministic layer flags for a
  human, because its patterns were written for extracted fields and fire on ordinary
  phrases like `pre-approved`
- **Every case gets reviewable paperwork** — an uploaded original is archived
  to Cloud Storage; a data-event case gets a bill of lading rendered from its
  record and labelled `SYSTEM-GENERATED`. Under `MULTI_TENANT` the shared bucket is
  scoped by tenant: each tenant's objects sit under `tenants/<id>/`, and an object
  whose tenant cannot be established is refused rather than ingested
- **Scale-to-zero** — `--min-instances=0`, `WORKER_MODE=ondemand`, lease-based
  claiming, exponential backoff
- **Human review queue**, coloured by state — red escalated, yellow held or
  pending
- **Live operations console** — pipeline board, event feed, action log, and
  a per-case trace showing every agent hop with its real latency and model
- Batch analysis, standalone entity screening, consolidated report generation
- **Per-tenant soft spend ceiling** — checked at the single chokepoint every model
  call passes through, so no agent can be added that bypasses it

### Technologies used

| Layer | Choice |
|---|---|
| Model | NVIDIA Nemotron 3 Nano (fraud, compliance, HS classification, zero-day radar), NVIDIA Nemotron 3 Super (investigation and the Senior Auditor debate), MiniCPM-V-4.5 (document intake, vision) — all via Nebius Token Factory |
| Agent framework | `openai.AsyncOpenAI` against Token Factory's OpenAI-compatible endpoint |
| External signal | Tavily Search API |
| Input security | Google Cloud Model Armor |
| Compute | Cloud Run (source deploy, `--min-instances=0`) |
| State | Firestore Native mode — `cases`, `events`, `audit_log`, `delegation_boundaries`, `boundary_counters`, `prefilter_rules`, `login_throttle`, `operator_activity` |
| Messaging | Pub/Sub — `shipment-events` inbound, `case-decisions` outbound |
| Documents | Cloud Storage |
| Web | Flask + gunicorn (1 worker, 8 threads) on `127.0.0.1:9090`, behind the console |
| Console | Next.js 16 (App Router, React 19, Tailwind, TanStack Query) on `$PORT`, in the same container |
| Container | One image, one Cloud Run service (`vf-app`): python:3.11-slim with Node copied from a digest-pinned image; `entrypoint.sh` starts Flask, waits for `/health`, then starts Next.js |

Hackathon requirements this satisfies, per the Official Rules: a functional runtime
call to Nebius Token Factory (all seven agents) · at least one NVIDIA open source
models (Nemotron 3 Nano and Super in the pipeline; Ultra measured against Super) · fits the **Best Apps and Agents** track · entered
in another hackathon first, on code begun after this Submission Period opened, and
significantly updated inside it, with the explanation given in the section below · (bonus) a
functional runtime Tavily call.

The four judging criteria are Technological Implementation, Design, Potential Impact and
Quality of the Idea.

### Other data sources used

- **Tavily** live web search, at runtime.
- **OFAC SDN and UN Security Council consolidated lists**: 18,525 records, rebuilt from
  the published files by `scripts/refresh_sanctions.py`.
- **19 public enforcement cases** (BIS, OFAC, US Commerce, EPPO, OLAF), rebuilt as
  shipments in `data/public_cases.json` by `scripts/build_public_cases.py`.
- **Published HS heading texts**, as the reference extract in `data/hs_reference.yaml`.
- **Vietnam Customs 2024 statistics** (customs.gov.vn) for the 0.2% base rate.

Shipment records — including the historical route-cost baseline and the shipper
transaction count the agents reason against — are synthetic or supplied per request; the
verifier falls back to code-resident lane tables when no `avg_route_cost` is provided.

### What was significantly updated during the Submission Period

This began as a Google Cloud entry to a different hackathon, All Things Agentic 2026,
on Vertex AI Gemini. That entry is public, in the same author's earlier repository
under a second GitHub account:
[CHAUPHUOCHOA/vf-logistics-hackathon-google](https://github.com/CHAUPHUOCHOA/vf-logistics-hackathon-google),
first commit 29 Aug 2026 (after this Submission Period opened on 26 Aug), last commit
`755d998` on 31 Aug (Vietnam time; 30 Aug 23:46 UTC on GitHub). The **Before** column describes `755d998` unless it names a commit
of this repository.

This repository's first commit, `6bb1e59` (17 Sep), already contains the first step of
the port, so the work is measured in two ranges. From a clone of this repository, after
`git fetch https://github.com/CHAUPHUOCHOA/vf-logistics-hackathon-google.git main`:
`git diff --shortstat 755d998 6bb1e59` reports **32 files, +1,833 / −1,380** for that
first step; `git diff --shortstat 6bb1e59 00d896a` reports **297 files changed, 171,921
insertions** across 49 commits for the first ten days, to 27 Sep; and
`git diff --shortstat 6bb1e59 d76e64e` reports **395 files changed, +199,584 / −6,681**
across 131 commits to 14 Oct. The ranges end at commits, not `HEAD`, so the figures stay
true. Fifty-three more commits after `d76e64e`, from `3e6b1dd` (16 Oct) to `febc252` (20 Oct), change
`src/`, `tests/` or `frontend/` again (167 files, +23,957 / −292, by `git diff --shortstat d76e64e febc252 -- src tests frontend`): they fix the verification loop (one publish per idempotency key, an
exception route for releases over unresolved evidence, an `unknown` send state), add the receiver, the sent ledger and the one-audit-row-per-send rule, raise the tests to 2,120 backend and 1,731 frontend (measured on `76d015e`), and add the
runner tooling that checked those fixes. They are outside the two ranges above.

One thing was deliberately carried over rather than rebuilt: the deterministic
governance layer — risk floor, untrusted-input boundary, delegation boundary, shipper
identity verification. None of it is model-specific, and it is what constrains the
system regardless of which model reasons. Carried over, not frozen: at `d76e64e`,
`untrusted.py` is byte-for-byte the `755d998` file, while `verifier.py` grew from 487
lines to 2,023 and `governance.py` from 492 to 894. The rows below were rebuilt.

| | Before | Now | Why |
|---|---|---|---|
| **Models** | 4 agents on Gemini 3.5 Flash + Flash-Lite, Vertex AI | 7 agents, 3 models in the pipeline on Token Factory: Nano ×4 hops, Super ×2 (investigation and the debate), MiniCPM-V; Ultra measured and not adopted | The floor overrides a score but not a verdict, so the debate was the one place a stronger model might pay. Measured on 46 labelled debates, Ultra did not (5.2x the cost, a verdict less often, no better at telling fraud from honest trade), so the debate runs on Super |
| **Agents** | intake, fraud, compliance, investigation | plus HS classifier, zero-day radar, auto-debate (1,751 lines) | Deterministic checks can match an HS code against a list but not against the cargo; sanctions lists lag the news; a 15-point floor/model gap needs arguing, not escalating |
| **Console** | one static HTML page served by Flask | Next.js 16 in the same container as the API — 107 files, 20,657 lines of TypeScript at `d76e64e` (tests excluded); it began as 7 screens, since joined by Access Control, Evaluation, three operator screens for the verification loop and a public upload page for shippers | The static page could not carry a session, and recording a decision had to be attributable |
| **Audit attribution** | `reviewer` read from the **request body as free text** | HMAC sessions, PBKDF2 operator records, audit names the authenticated account | Anyone could sign any name, which makes an audit trail decoration rather than evidence |
| **Anonymous authority** | no authentication at all in `755d998`; then, mid-port, any visitor held `GOVERNANCE_ADMIN` on the live console and an unauth `POST /orchestrator/reset` cleared 307 real cases | code default `ANONYMOUS_ROLE=viewer` (deployed: `none`), API key on writes, split on HTTP method; the console forwards its key only with a verified session, which **narrows** it to the person's role | Found by doing it. The first fix closed writes but left roles decorative — the console's key made every signed-in user an admin — so roles now come from the person, and `/api/v1/auth/policy` reports what every route enforces |
| **Cost control** | an estimate summed over the cases on screen; no ceiling, no output cap on any agent — after the port the provider default of 8,192 was hit twice by runaway calls that returned unparseable output | per-hop attribution, per-tenant soft ceiling at the one chokepoint, a switch ratchet, measured ceilings everywhere | A runaway costs money and produces nothing |
| **Tests** | **no** unit tests in `755d998` (one self-check script); this repository's first commit added one HTTP script | **2,120** backend tests passing at 96% coverage (CI runs the Firestore and Pub/Sub emulators), 1,731 frontend tests, and a mutation check that breaks 112 lines and sees every one caught | A test nobody has seen fail is not evidence |
| **CI** | no test or lint CI in `755d998` (only a Cloud Build deploy config); the workflow added on 19 Sep filtered on branch `main` while the repo uses `master`, so it **never ran** until 24 Sep | five jobs: ruff, mypy (zero errors, strict on clean modules), tests in the emulators with a 92% coverage floor, frontend (with a coverage floor), and a container smoke test | A documented pipeline that does not execute is the same defect as an undocumented one |
| **Structure** | flat root: `main.py` and 16 other modules at top level | `src/vf_logistics/` with 16 modules that did not exist: auth, b2b, budget, dossier, evaluation, evidence, hs_reference, lineage, membership, observability, openapi, sanctions, sanctions_sources, schemas, tenant, worklist | — |
| **Verification loop** | `Request info` wrote a note and moved on; no way to track what was asked, whether it arrived, or whether the answer changed anything | 6-step evidence loop: request with HMAC link, receive and extract per doc type, re-evaluate against extracted facts, approve bound to evidence version, execute with claim-before-send, one publish per idempotency key and publish-failure handling. 8 endpoints for the loop (the exception approval among them) plus the worklist and outcomes reads, 3 operator screens, 0 improper executions on the 7-case evaluation corpus (a mocked harness) | Four identical origin-fraud pairs proved booking data alone is not enough. The loop is how the system collects what is missing and reaches a defensible decision |

Three measured results worth stating plainly, because each corrected an assumption we
had published or would have:

- **HS classification recall went 40.0% → 26.7% → 92.9%.** Two worked examples
  made Nano *worse* (few-shot, 26.7%); what fixed it was reasoning against a reference
  extract of the published HS headings — 92.9% at 100% precision on the 30-case pairs
  set, and **11 of 12 caught with 0 of 12 honest cases flagged** on a 24-case holdout of
  substitutions the reference never mentions (rates over graded cases; with twelve of
  each, the 95% interval on recall is 64–99% and the upper bound on false alarms
  24.2%). A retrieval problem dressed as a reasoning problem does not respond to examples.
- **Tuning cut the clean shipments the rules hold from 76.7% to 38.4%.** Tuned on the dev
  half of a 1,000-case synthetic corpus and measured on the held-out half: **38.4%**
  [28–49], precision 84.1% → 91.2%, recall 95.1% → 93.5% [90–96], sanctions-alias
  and shell-company detection unchanged at 100%. Still high: at 0.2% fraud that is about
  206 shipments held per real case, against roughly 19 red-channel inspections per
  violation found by customs. We traced the remaining false positives to four findings on
  dev, measured five candidate fixes and rejected all five, so the verifier is unchanged.
  The largest lever left, counting a first-time shipper only as corroboration (dev
  false-positive rate 35.1% → 21.9%, no recall lost on the corpus), would release new
  counterparties' first shipments unreviewed, and is the operator's call. The
  operating curve for the review threshold is on the Evaluation page of a build that
  includes `896bbae`. It is a what-if over the rules' floors, not a setting: any non-zero
  floor already withholds auto-clear, so moving along it would mean changing `reconcile()`
  and the boundary. We left the threshold where it is.
- **Tavily, not the models, is the binding cost constraint.** A 20-case run spends
  `$0.068`–`$0.088` on inference and 90–106 Tavily searches, so the free search tier
  runs out around 200 cases while model spend is still negligible. Every cost figure
  we had published before measuring this was a model-cost figure.

The measurement work of 21 Oct 2026 (`05a8063` to `fb3a53f`, merged as `b6389c9`) is outside
the ranges above. It added `scripts/compare_fraud_models.py` and a `--corpus` mode for
`scripts/compare_debate_models.py`, the two reports in `data/model_comparison/`, the
model-comparison card on the Evaluation page and the `/api/v1/evaluation` field behind it,
and it moved the debate from Nemotron 3 Ultra to Super on the strength of those reports.
It also corrected two defects in the older board mode of the comparison script: it read the
whole verdict object as the verdict string and reported every debate as unresolved.

### Findings and learnings

**Vision models on OpenAI-compatible endpoints want images, not PDFs.**
Porting from Gemini's native PDF support meant adding a rasterisation step
(`pypdfium2`, first page to PNG) before the vision model call — a detail that
would have silently produced garbage transcriptions if missed.

**Structured output travels across providers.** Gemini's
`response_mime_type="application/json"` and the OpenAI-compatible
`response_format={"type":"json_object"}` do the same job, so none of the
agents' prompts or output schemas needed to change — only the transport
underneath them did. That is what made a same-week model-layer swap possible
without rewriting the governance, verifier, or orchestrator layers at all.

**A failed search must not read as a clean one.** Tavily is new to this system, and our
first integration returned an empty list on any failure, so the compliance model was
told "searched, found nothing" about a search that never ran. Every call site a model
reads from now carries the search status into the prompt (worst status wins across
shipper and receiver), so an outage still never blocks a case, but it is never mistaken
for a clean result.

**Model choice is a per-agent decision, not a project-wide one.** Fraud and
compliance are the highest-volume, primary-judgement calls, so they get the
cheapest model that is still reliable (Nano); investigation is low-volume and
benefits from more reasoning, so it gets a larger tier (Super); and the debate also runs
on Super, because Ultra, which it started on, was measured against Super there and did not
do better. This mirrors the hackathon's own guidance (Nano or Super for the everyday calls,
Ultra when serious reasoning is needed), and it adds the part the guidance cannot: on this
task serious reasoning did not show up as a better verdict. The original Gemini build
reached its model split from the other direction, so the two systems disagree on *which*
task deserves the stronger model, which is evidence that the choice is task-dependent.

**The execution gate is more important than the model.** A judge will
remember "you can publish a policy that constrains what the agent does"
longer than which model scored a shipment 78. Governance is the
differentiator; the model is the commodity -- true regardless of which
provider's models are underneath.

**Nemotron Nano vs Super: observable differences in structured output.**
Nano (`Nemotron-3-Nano-30B-A3B`) reliably produces clean JSON for fraud detection
and compliance screening -- the most frequent calls -- under
`response_format={"type":"json_object"}`. Super (`nemotron-3-super-120b-a12b`)
carries the multi-step reasoning in investigation and the debate, and
under that same mode with reasoning on it failed half the time: measured on two
stored board prompts, 6 of 12 replies were fragments such as `{": {}}": null}` or
planning text used as a JSON key. The same prompt without `response_format` was
12 of 12, so Super is asked for JSON by its prompt alone, checked against a schema,
and asked once more if a reply still fails it.

**Token Factory pricing is developer-friendly but hard to predict, and a rate
multiple is not a cost multiple.** The per-token pricing ($0.06 / $0.24 per million
for Nano input/output, $0.30 / $0.90 for Super, $1.00 / $3.00 for Ultra) is clear,
but predicting total cost for a pipeline of variable-length prompts is non-trivial —
and we got it wrong in the direction that matters. We documented the Ultra debate as
costing 3.3x Super, which is its rate. Measured, it cost **13x**: `$0.0198` per
debate against `$0.0015`, because Ultra emits more tool-call rounds and each round
resends the growing transcript, so it spent 13,151-16,502 input tokens where Super
spent 2,522-5,546. We had multiplied Super's token usage by Ultra's rate, assuming
equal token spend. (The later 46-debate comparison, with empty search results and other
cases, gave 5.2x; we have not isolated why the two differ.) A cost dashboard showing real-time spend per agent is essential
for any production deployment — we built one, and a per-tenant spend ceiling behind
it — but the dashboard is what caught this, not the arithmetic.

**What we'd build next with Nebius.** (1) Fine-tune Nano on our
fraud-detection domain to improve structured output quality. (2) Use Nebius
Serverless inference for auto-scaling during peak shipment volumes. (3) Explore
Nebius GPU clusters for batch processing historical fraud cases with the
investigation agent.

---

## Video script

The shooting script is [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md): eight scenes,
each with the click path and the narration line it is timed against. The narration
itself lives in [`scripts/build_narration.py`](scripts/build_narration.py), which
synthesises the audio track and matching SRT subtitles per sentence, so caption
timings are measured from real audio rather than interpolated from word counts. The
eight scene lengths sum to 165 seconds, under the three-minute limit. It follows two shipments, A and B, with the same booking data: the purchase invoice sends A to a human Release and B to a human Block.
The second scene names Floorline and who it is for, and NVIDIA Nemotron and
Nebius Token Factory are named aloud, not only on screen.

An earlier `docs/video-script.txt` described the retired vanilla-HTML dashboard, a
`DEMO_MODE` sidebar that no longer exists, and pointed at a service in a different
project. It was git-ignored, so it never formed part of this repository; it has now
been deleted outright rather than repaired, because two shooting documents that
disagree is worse than one.

Two things worth knowing before recording: the board has to be seeded first
(`python scripts/seed_full_board.py --yes`) or the cost meter and pipeline board are
both empty on camera, and a document upload takes roughly 30–60 seconds to reach a
terminal state, so narration over that scene needs to be long enough to cover
it.

The shot list is the one in `docs/DEMO_SCRIPT.md` (eight clips); this file does not
keep a second one. Tavily results are shown in the case trace (clips 2 and 6); a case cleared by the pre-filter ran no model and has no sources. Model Armor
is live and enforcing: `sample_docs/injected_bol.pdf` is blocked with `MATCH_FOUND`
before the vision model sees it, `case_id` prefixed `CASE-BLOCKED-` (shown on the Agents page; the video does not film it).

---

## Devpost form — field-by-field answers

### Project details

**Built with** (tags, the required tools first): nebius-token-factory, nebius,
nvidia-nemotron, nemotron-3-nano, nemotron-3-super, nvidia,
minicpm-v, tavily, google-cloud, cloud-run, firestore, pub-sub, cloud-storage,
model-armor, openai-sdk, python, flask, gunicorn, asyncio, nextjs, react, typescript,
tailwindcss, docker

**"Try it out" links:**
- https://vf-app-350828852747.asia-southeast1.run.app (console and API, one service)
- https://github.com/hoachauphuoc/vf-logistics-nebius-nvidia

**About the project** (the story field) → paste `docs/PROJECT_STORY.md`, which follows
Devpost's headings; the *Text description* above is the longer version. The story does
not name the predecessor repository or the commit ranges, so the next answer carries them.

**What was significantly updated** (wherever the form asks for it, or appended to the
story) → paste *What was significantly updated during the Submission Period* above, with
its table.

**Feedback on Nebius Token Factory, AI Cloud and NVIDIA tools** (wherever the form asks
for it) → paste the *Feedback* section below.

**Video demo link** → the public YouTube URL, once recorded (see *Submission checklist*).

**Thumbnail and image gallery** → Devpost takes 15 images, so upload the set in `docs/screenshots/devpost/` in the numbered order (`01` is the 3:2 thumbnail; the folder README has a caption for each). The full set of seventeen screenshots is in `docs/screenshots/`, with a
caption for each in its README, taken from revision `vf-app-00045-fxk` (the pipeline board, a
review case where the floor overrides the model, a case trace naming the model on each hop,
the dossier PDF, governance, the evaluation page with the model comparisons, the agent
console, the audit trail, a debate on Super and the architecture page). One planned image is
not there and the README says why: Model Armor blocking the injected sample.

### Additional info

<!--
The testing-instructions answer holds two <PASTE ...> placeholders for the real passwords
(Secret Manager: VF_JUDGE_PASSWORD, VF_REVIEWER_PASSWORD). They are pasted into Devpost
only and never committed. Its verification-loop step names VF-DEMO-PLY-A and
VF-DEMO-PLY-B, which scripts/seed_verification_demo.py creates; the live board did not
carry them on 15 Oct 2026.
-->

| Field | Answer |
|---|---|
| **Submitter Type** | Individual |
| **Country of residence** | Vietnam |
| **Category** | Best Apps and Agents |
| **Public code repo URL** | https://github.com/hoachauphuoc/vf-logistics-nebius-nvidia |
| **Reproducible Testing instructions in README?** | **Yes** — README → *Reproducible testing* |
| **Testing instructions (private)** | **Console:** https://vf-app-350828852747.asia-southeast1.run.app — the deployed console asks everyone to sign in, and an anonymous request to the API answers 401. **One click, no password:** the login page offers **Continue as guest judge**, which signs you in as `guest-judge@vf-logistics.demo`. That account holds the governance-admin role, but a one-click session is refused the hard-to-undo actions listed below; it can read the board, any case, the review queue, the audit trail and the evaluation figures, and it can inject shipments or upload a document (DevOps), block a case or ask for information, and simulate a policy against the board. Anything that releases cargo or would carry over to the next judge needs a password sign-in: **Release** (on Review, and the verification loop's approve and execute), **Clear board** (DevOps), **Publish** or **Revoke** the delegation boundary, and editing the **pre-AI screening rules** (Governance). **Password accounts:** `judge@vf-logistics.demo`, password `<PASTE VF_JUDGE_PASSWORD>`, is a **governance admin**: it can release a shipment, see the spend figures and publish policy. `reviewer@vf-logistics.demo`, password `<PASTE VF_REVIEWER_PASSWORD>`, holds only the **Reviewer** role: it can decide a case and open its document, and the spend figures, document upload and policy publishing stay locked for it, naming the role that would unlock each — sign in with it to watch the access control refuse something. The audit trail names whichever account acted. **Verification loop:** sign in with a password account, open `/today` and pick a case with the ORIGIN_INPUTS_UNVERIFIED finding — `scripts/seed_verification_demo.py` seeds two plywood shipments for this, `VF-DEMO-PLY-A` and `VF-DEMO-PLY-B`. A case released by an earlier judge cannot be released again, so if they are not on the board or are already decided, make a fresh one: DevOps → *Submit one by hand* (an operator action, so the guest judge or `judge@vf-logistics.demo`) with origin `Vietnam`, destination `United States` and HS code `4412.33` raises the same finding and lands on `/today`. Click "Request evidence", copy the link, and open `/respond/<token>` in a new tab to upload a PDF (any of the bills of lading in the repository's `sample_docs/` will do). Back on the case page, the timeline shows the document received. Click "Re-evaluate", then "Release" (routes through approve + execute). The `/outcomes` page shows the resolved count. **Evaluation script:** `python scripts/run_verification_eval.py` runs 7 cases and reports 0 improper executions. **API only:** `GET /health` is open and warms the service, and `GET /api/v1/openapi.json` is open; every other route answers 401 without a session, so the console is the way in (`GET /api/v1/auth/policy` and `GET /api/v1/orchestrator/state` need a session too). Full walkthrough in the README. |

**Which model provider(s) did you use?** → **Nebius Token Factory**, hosting
**NVIDIA Nemotron 3 Nano** (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`), **NVIDIA
Nemotron 3 Super** (`nvidia/nemotron-3-super-120b-a12b`, investigation and the debate),
and **MiniCPM-V-4.5** (`openbmb/MiniCPM-V-4_5`) for document vision, which NVIDIA does
not currently offer on Token Factory. **NVIDIA Nemotron 3 Ultra**
(`nvidia/Nemotron-3-Ultra-550b-a55b`) was measured against Super on the debate and is
not used by default.

**Which bonus integrations did you use?** -> **Tavily** -- 6 real, runtime
search integrations: compliance screening, investigation enrichment, route
validation, the zero-day radar's adverse-media search, the Governance page's
watchlist scanner and entity check, and multi-agent debate tool calling. Compliance
reuses a search for the same name made in the last hour. Verifiable in the per-case trace UI, in
`GET /api/v1/orchestrator/case/<case_id>` (the compliance hop's `external_search_used`
and `external_search_results`), or in `GET /api/v1/billing/usage` -> `tavily_searches`
(operator).

> Both the Nebius and Tavily calls can be checked on the live service:
> `GET /api/v1/agents` lists the four request/response agents and the model each runs
> on; the HS classifier, zero-day radar (Nano) and the Senior Auditor debate (Super) show
> their model on every hop of the case trace (`steps[].model`, e.g. the `auto_debate`
> step); and every compliance hop carries `external_search_used` and the search result
> titles/links, which the console renders directly in the case trace.

---

## Submission checklist

| Item | Status |
|---|---|
| Demo video, up to 3 minutes | **OUTSTANDING** — mandatory. Record, upload to YouTube as public, paste the URL here and on Devpost. The shooting script is `docs/DEMO_SCRIPT.md` (eight scenes, 165s) and the narration track is generated by `scripts/build_narration.py`; the narration text changed on 21 Oct (the eight-scene plan), so the audio has to be regenerated from its `SCENES` before recording. |
| Public code repository | done -- https://github.com/hoachauphuoc/vf-logistics-nebius-nvidia |
| Secrets out of the repository | done -- scanned the tracked files, the untracked working tree and the full git history for Tavily, Google, Nebius and private-key patterns: no hit. `.env` is git-ignored and was never committed; `.env.example` holds placeholders only; on Cloud Run the keys come from Secret Manager |
| Devpost text description | this file |
| README with spin-up instructions | `README.md` |
| Reproducible testing instructions | `README.md` -> *Reproducible testing* |
| Hosted project URL | done -- https://vf-app-350828852747.asia-southeast1.run.app, console and API in one service; revision `vf-app-00047-228`, deployed from `76d015e` |
| Runtime call to Nebius Token Factory | done -- all seven agents |
| NVIDIA open model used | done -- Nemotron 3 **Nano** (screening, every case that reaches the models), **Super** (investigation and the auto-debate) + **MiniCPM-V 4.5** for document vision. Ultra was measured against Super on 46 labelled debates and not adopted. Each model sits on the job its measured result justifies. |
| Functional Tavily runtime call | done -- 6 integration points |
| Automated test suite | 2,120 backend tests passing on `76d015e`, run in the Firestore and Pub/Sub emulators as CI does (pytest, 96% coverage), 1,731 frontend tests in 91 files (vitest, 62.0% line coverage), mypy 0 errors, mutation check 112/112 |
| CI pipeline | GitHub Actions, five jobs, every one able to fail the build: ruff; mypy with zero errors, strict on the clean modules; pytest in the Firestore and Pub/Sub emulators with `--cov-fail-under=92`; frontend typecheck, lint, tests with a coverage floor, and build; and a container job that builds both images and smoke-tests the deployed one |
| Auto-debate on score disputes | done -- fires without human intervention; a confident DISAGREE can send the case to an investigation it would otherwise skip, upward only; a person decides every disputed case |
| Human feedback learning loop | done -- derived from reviewed cases, read fresh on every scored case; two human blocks raise risk by name, five human releases lower it only for a registry-verified counterparty, never below the floor |
| Adversarial demo scenarios | seven sample bills of lading in `sample_docs/` (prompt injection, identity spoof, dual-use goods, over the value ceiling, unknown shipper, several fraud signals at once, and a clean one) to upload on DevOps; DevOps -> *Scripted batch* loads three fixed cases, one per outcome |
| Injection probe | Agents page (`/agents`): paste any attack, or one of the listed samples, and see the real screen verdict |
| Governance kill switch | `/api/v1/governance/revoke` -- agent goes SUSPENDED |
| Policy dry run | preview which cases a boundary would flip before publishing |
| Per-hop model I/O | exact prompt, raw response, tokens and cost per agent call |
| Cost dashboard | real-time token spend + rules savings (operator and above) |
| Per-tenant spend ceiling | soft ceiling at the single model-call chokepoint |
| Console login + audit attribution | a decision records the signed-in account, not a typed name |
| Role-based access | anonymous viewer, reviewer, operator, governance admin; a session narrows the key and never widens it |
| Measured evaluation | detection benchmark on a held-out split, HS classifier on a holdout set, public enforcement cases and a rules operating curve, all with 95% intervals, served at `/evaluation` by a build that includes `896bbae`; and two model comparisons (Nano against Super on fraud where the rules score 0, Ultra against Super on 46 labelled debates) with the same intervals, in `data/model_comparison/` |

The repository is public, so no judging-instruction share is required. Outstanding: the video; the test counts, which are to be refreshed from the next CI run on `master`; and the image gallery, which is to be captured from the live service.


---

## Feedback on Nebius Token Factory, AI Cloud, and NVIDIA tools

### What worked well

1. **OpenAI-compatible API** — Migrating from Gemini took hours, not days. The
   same `AsyncOpenAI` client talks to Token Factory; only the base URL, model
   IDs, and pricing table changed. Structured JSON output (`response_format`)
   works identically for Nano; Super with reasoning on is the exception, below.

2. **Model variety in one endpoint** — Having Nemotron Nano (fast/cheap),
   Nemotron Super (high-quality reasoning and the debate), Nemotron Ultra (measured, not adopted), and a
   vision model (MiniCPM-V) behind the same endpoint simplified architecture. Each agent picks the
   right model for its job without managing multiple SDKs or auth flows.

3. **Generous hackathon credits** — The Nebius Builder Program credits allowed
   extensive iteration on prompt engineering and multi-agent orchestration
   without worrying about cost during development.

4. **Latency for Nemotron Nano** — fraud and compliance take roughly seven seconds a
   call, and running the two in parallel puts a case through both in about one round
   trip, which is workable for a review queue.

### What could be improved

Each item below is something we measured while building, with the consequence it had
on this codebase. The first two are the ones we would fix first.

1. **There is no safety or guard model in the catalogue — 24 models, zero
   classifiers.** Queried against the live `/v1/models` endpoint at submission time:
   24 models served, of which four are NVIDIA and all four are chat models
   (`Nemotron-3_5-Lightning`, `NVIDIA-Nemotron-3-Nano-30B-A3B`,
   `nemotron-3-super-120b-a12b`, `Nemotron-3-Ultra-550b-a55b`). No `nemoguard`, no
   content-safety or moderation classifier, nothing purpose-built to screen input.

   The consequence is concrete and visible in this submission: a shipping document is
   attacker-controlled and its transcription moves physical cargo, so it has to be
   screened before a model reads it — and **the only component of this pipeline that
   is not on Token Factory is that screen**. It runs on Google Cloud Model Armor
   because Token Factory offers no first-party alternative. We evaluated NVIDIA NeMo
   Guardrails as a replacement and did not adopt it, for a reason that is itself
   feedback: Guardrails is a *toolkit*, and its `self check input` rail asks a chat
   model whether text is trying to manipulate a chat model. That is the vulnerable
   component policing itself, and it is injectable. A toolkit cannot substitute for a
   classifier that was trained for the job.

   What would close it: a hosted `nemoguard`-class content-safety or
   jailbreak-detection model behind the same OpenAI-compatible endpoint. A team
   building anything that ingests untrusted text — which is most agentic pipelines —
   currently has to leave the platform at exactly the security-critical step.

2. **A servable model is missing from the published pricing, and the failure is
   silent.** `nvidia/Nemotron-3_5-Lightning` is returned by `/v1/models` and can be
   called, but we could find no rate for it, and the models endpoint does not carry
   pricing. Because a client has to hard-code a rate table, an unpriced model does not
   fail — it falls through to whatever default the client picked. In our case that was
   the cheapest entry, which means spend is under-reported and the per-tenant spend
   ceiling that reads the same figure quietly stops holding. We now log it at ERROR at
   startup and per call rather than absorbing it, but the fix belongs upstream.

   What would close it: pricing on the model object in `/v1/models`, or a versioned
   rate-card endpoint. Either lets a client fail loudly on an unknown model instead of
   guessing, and makes a new model adoptable the day it appears.

3. **`usage` can be absent from a completion response, and there is no way to tell
   that apart from a free call.** Our `_usage()` helper returns `(0, 0)` when the block
   is missing, because there is nothing else it can return — so an unmetered call and
   a zero-token call are indistinguishable downstream, and billing silently
   under-counts. A guarantee that `usage` is always present on a 2xx, or an explicit
   error when it cannot be computed, closes a metering hole that a customer-facing
   invoice sits on top of.

4. **No NVIDIA vision model on Token Factory.** Document intake (transcribing bills
   of lading, invoices, packing lists) needs a vision-language model, and at
   submission time Token Factory's NVIDIA catalog has none, so this project runs that
   one agent on `MiniCPM-V-4.5`. Document understanding is one of the most common
   real-world entry points for an agentic pipeline — someone always starts from a PDF,
   a scan, or a photo of paperwork — so a team building a document-first workflow has
   no first-party NVIDIA option for the very first step. A Nemotron VL variant, or an
   `nvidia/nemotron-parse`-style OCR/layout model on the same endpoint, would close it.

   Separately, the vision endpoint requires images rather than raw PDF bytes (Gemini
   reads PDFs natively), which meant an extra `pypdfium2` rasterisation step in
   `document_agent.py` that cost real debugging time before we realised it was expected
   rather than a bug. A one-line note in the vision-model docs would have saved that.

5. **Prefix caching exists on the backend but cannot be billed for, and there is no way
   to tell whether it hit.** The inference docs list "KV Cache" and "Context Caching"
   under optimisations, and the observability page exposes a **KV-cache hit rate**
   distinguishing local from external hits — so prefix caching is real and running. But
   the `Pricing` schema returned by `GET /v1/models?verbose=true` has six dimensions
   (`prompt`, `completion`, `image`, `price_per_video_second`, `request`,
   `price_per_minute`) and **none for cached input**, so there is no field in which a
   discounted cached rate could be expressed. `prompt_tokens_details.cached_tokens` is in
   the OpenAPI schema but carries only a title, no description, and comes back `null` on
   every chat completion we observed.

   The consequence here is concrete and we chose to eat it. Our HS-classification agent
   sends a 1,376-token static nomenclature extract on every call — essentially 100% of
   that agent's input, since the variable part is one cargo description. We measured what
   the block buys before considering trimming it: base Nemotron Nano reaches **40.0%**
   recall on deliberately evasive misclassification cases, naive few-shot prompting makes
   it **worse at 26.7%**, and the same model reasoning against this reference block
   reaches **92.9%** on those cases and **91.7%** on a holdout of substitutions the block
   never mentions.
   The block is the capability, not overhead, so it stays — and we pay full `prompt` rate
   for an identical prefix 16 times per 20-case run. Either a documented cached-input rate
   or a populated `cached_tokens` would let a builder reason about this instead of
   guessing.

   Related and cheap to fix: **`max_tokens` defaults to 8192 when omitted**, which *is*
   documented but easy to miss, and it cost us real money before we found it. Two calls
   on a measured run returned exactly 8,192 output tokens and both failed to parse — one
   was a degenerate repetition loop emitting `{}, {}, {}, ...` for 38.6 seconds at
   `$0.007873`, which was 29% of that agent's entire spend for the run. `finish_reason:
   "length"` is documented and is what let us tell "the model emitted invalid JSON" from
   "the reply was cut off". Surfacing the 8192 default more prominently — or defaulting
   to the model's context limit rather than a fixed number — would have saved the hunt.

6. **Rate limit headers** — Token Factory returns 429 on rate limit but does not
   include `Retry-After` or `X-RateLimit-*`. For a production service that has to back
   off gracefully, knowing *when* to retry is the difference between a backoff and a
   guess.

7. **Streaming for long completions** — the investigation agent's detailed reports
   take 5–10 seconds. Streaming would improve perceived latency for a human reviewer
   waiting on a decision.

8. **Batch API** — for offline scoring of historical shipments (backtesting a new
   risk model against last year's freight), a batch endpoint at a lower per-token cost
   would make the difference between running it and not.
9. **Say whether a JSON-schema mode exists for the Nemotron models.** Even with
   `response_format={"type":"json_object"}`, Nemotron 3 Nano returned a reply that failed our
   schema in 5 of 179 fraud-screening calls (2.8%): `recommendations` came back as a string
   where the schema wants a list. Super, asked for JSON by its prompt alone because it does not
   survive `json_object`, returned invalid JSON in 1 of 179. We handle both (a failed reply
   refuses auto-clear and goes to a person), but we did not test a schema-constrained mode and
   could not tell from the documentation whether one is offered for these models. A stated
   answer, and an example, would remove this class of failure for anyone building on them.
   The counts are in `data/model_comparison/fraud_floor0.json`.

### Nebius AI Cloud

We did not use it. Every model call goes to Token Factory, and the service itself runs on
Cloud Run next to the Google Cloud services it depends on: Model Armor, Firestore,
Pub/Sub and Cloud Storage. Serverless Endpoints, Serverless Jobs and GPU capacity are not
used, so we have no first-hand feedback on them; the *What we'd build next with Nebius*
paragraph above says where we would try them.

### NVIDIA open models feedback

1. **Nemotron 3 Nano** (`Nemotron-3-Nano-30B-A3B`) — excellent for deterministic
   classification. Fraud scoring and compliance bucketing are consistent and fast, and
   it holds `response_format={"type":"json_object"}` reliably across thousands of calls.

2. **Nemotron 3 Super** (`nemotron-3-super-120b-a12b`) — handles the multi-hop work
   well: cross-referencing compliance findings and temporal anomaly detection. One
   measured rough edge: **`response_format={"type":"json_object"}` and reasoning do not
   mix.** With both on, Super's planning leaks into the constrained output: half the
   replies were a fragment such as `{": {}}": null}`, `{".json": null}`, or a JSON key
   holding text like "We must output JSON only...". Measured on two stored prompts, 12
   calls per mode: json_object with reasoning 6/12 usable, no response_format 12/12,
   json_object with `enable_thinking=False` 12/12. Every failed reply still parsed as
   valid JSON, so nothing flagged it until we added a schema. We dropped json_object for
   Super and kept the reasoning; on the live service the same ten cases went from 5/10
   to 10/10 usable reports.

3. **Nemotron 3 Ultra** (`Nemotron-3-Ultra-550b-a55b`) — we ran the Senior Auditor debate
   on it first, where native function calling lets it decide at runtime whether to search
   rather than following a script, then measured it against Super on 46 labelled disputed
   cases. Both models returned a parseable verdict in most debates (Ultra 37 of 46, Super
   40 of 46), and neither was better at telling fraud from honest trade. Ultra cost 5.2x as
   much per debate with empty search results and 13x on the live service with real ones,
   because it takes more tool-call rounds and each round resends the transcript (13,151–
   16,502 input tokens where Super spent 2,522–5,546). A per-round token accounting
   breakdown in the usage object, rather than a single total, would have made that visible
   without instrumenting it ourselves. We moved the debate to Super; Ultra stays one
   environment variable away. Fair to Ultra: our corpus is synthetic and small, and the
   auditor is shown the rules' findings, so the test cannot tell independent judgement from
   agreement with the rules.

4. **Wish list, in the order we would use them.** (a) A safety/guard model — see the
   first item above; it is the only reason this pipeline reaches outside Nebius.
   (b) A Nemotron VL variant, so document intake can be Nemotron too. (c) A Nemotron
   fine-tuned for entity extraction, to pull shipper and receiver names, addresses and
   tax IDs out of noisy transcriptions more accurately than a general chat model does.

---

## Cost note

The one service runs with `--min-instances=0`, so it scales to zero when idle. After
judging closes it can be deleted:

```bash
gcloud run services delete vf-app --region asia-southeast1
```
