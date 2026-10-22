# Floorline Pay — the agent pays the carrier the moment delivery is proven, and can never pay a shipment the rules held

> **Work in progress for the PayPal AI Hackathon (submissions close 12 Nov 2026).** This repository starts from Floorline as submitted to the Nebius x NVIDIA Global AI Hackathon (tag `nebius-baseline`, commit `5690ea3`). Everything after that tag is the PayPal work; `git diff nebius-baseline` shows it. Plan: [docs/PAYPAL_PLAN.md](docs/PAYPAL_PLAN.md). The sections below still describe the Nebius baseline until they are rewritten.

Freight payments run on trust: a shipper's money is held at booking with PayPal (`intent: AUTHORIZE`), and the carrier is paid through PayPal Payouts only after Floorline has released the shipment. Deterministic checks set a risk **floor** for every shipment, including new payment checks (a payee changed just before payment, a payee that is not the registered carrier, an amount above the booked freight). AI agents may argue a case above that line; nothing they say can take it below, a held shipment keeps its money held, and a person decides every disputed case.

- **Hackathon:** PayPal AI Hackathon · **Targets:** Best Use of Agentic Commerce, Best Use of PayPal + AI, Best Use of AG Studio
- **Live demo:** pending (a separate Cloud Run service; the Nebius service `vf-app` is not changed)
- **Demo video (under 3 minutes):** YOUTUBE_URL_PENDING
- **Licence:** MIT · **Full reference:** [REFERENCE.md](REFERENCE.md) · **Architecture:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## What it does

Floorline screens shipments for a freight forwarder's compliance desk. Fraud in a forwarder's book is rare, so almost every hold is honest; the job is holding the right shipments and making each decision fast and defensible.

1. **Reads the documents as untrusted input.** A bill of lading is screened by Model Armor before any model reads it, then read by a vision model.
2. **Computes a deterministic floor** (`verifier.py`: arithmetic and list lookups, no model, no network).
3. **Scores above the floor.** Fraud and compliance agents screen the case with live Tavily web search; an HS classifier checks the declared tariff code against the goods; a zero-day radar searches adverse media for parties no sanctions list has caught up with.
4. **Argues disputes.** When the floor exceeds the model's score by 15 points or more, a Senior Auditor debate runs. A confident DISAGREE can send the case to a deep investigation it would otherwise skip, upward only. It can never clear a case.
5. **Releases inside limits a named person published.** Clear cases are released under a delegation boundary; the rest go to a reviewer with the findings, the sources and a due-diligence dossier (PDF). The audit trail names the signed-in account.
6. **Closes missing evidence.** The reviewer requests documents through per-document links, the shipper uploads without an account, Floorline extracts and re-evaluates, a person approves, and the decision is published once per idempotency key.

### The design rule

`reconcile()` takes the higher of the model's score and the floor, and `validate()` raises if the HS or zero-day findings ever lowered it. A weaker model can miss a hold; it cannot release what the rules stopped. The debate is the one hop whose output can change routing instead of a score the floor overrides, so it was the one place a stronger model might matter. It runs on Super: Nemotron 3 Ultra was measured against it there and did not do better (below).

### Measured (synthetic holdout; 95% Wilson intervals in brackets)

- Rules alone, 453 holdout cases: recall 93.5% [90–96], false-positive rate 38.4% [28–49].
- Adding the HS classifier and the zero-day radar, on 200 holdout cases: recall 95.8% [91–98] → 100% [97–100], because the classifier caught all 7 disguised tariff codes the rules missed. It held 2 more honest shipments and, by design, released none the rules held.
- 19 public enforcement cases, each paired with the honest trade it imitated: 14 frauds held (73.7% [51–89]; rules alone 11), and 9 of the 19 honest look-alikes also held.
- Model choice, measured on 179 corpus cases the rules scored 0, where the model is the only check: Nemotron 3 Nano flagged 46 of 52 frauds (88% [77–95]) and Super 47 of 52 (90% [79–96]). The sample cannot show a difference, and Super costs 4.3x as much per case, so fraud screening stays on Nano. The cost of that layer: on the holdout half Nano flagged 21 of the 24 frauds the rules missed, and also held 30 of the 53 honest shipments the rules had passed. With the rules' own 33 honest holds that is 63 of 86 honest holdout shipments (73%), against 38.4% for the rules alone.
- The debate, measured on 46 labelled disputed cases (`data/model_comparison/debate_corpus.json`): Ultra reached a verdict less often than Super (37/46 against 40/46), cost 5.2x as much per debate and took twice as long. Neither told fraud from honest trade: Super said DISAGREE on 16 of 22 frauds and 13 of 18 honest cases, Ultra on 18 of 19 and 17 of 18. The auditor is shown the rules' findings and every honest case in the sample was one the rules held, so DISAGREE largely tracks the rules; it can only send a held case to investigation, never clear one. Read the debate as a routing step, not as evidence of independent judgement.
- The weakness, in reviewer time: at the 2024 Vietnam customs fraud rate of 0.2%, the rules would hold about 206 shipments per real fraud case. The screen is not precise, and no real customer traffic has been measured. Cost is under half a cent of model spend per case.

The details, the operating curve and the reproduction commands are in [REFERENCE.md](REFERENCE.md#measured-results) and on the console's Evaluation page (`/evaluation`).

## Models, and where Token Factory helped

Every model call goes through **Nebius Token Factory**, one OpenAI-compatible endpoint (`https://api.tokenfactory.nebius.com/v1/`) used through `openai.AsyncOpenAI`.

| Model | Where it runs | Why this size |
|---|---|---|
| `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | fraud, compliance, HS classification, zero-day radar | The floor makes a smaller model safe here, and Super flagged no more frauds where the rules saw none (46/52 against 47/52, at 4.3x the cost) |
| `nvidia/nemotron-3-super-120b-a12b` | investigation, the auto-debate | The investigation writes the narrative a human reads; the debate routes a held case to it. Super matched Ultra on the debate at a fifth of the cost |
| `nvidia/Nemotron-3-Ultra-550b-a55b` | not used by default; `DEBATE_MODEL` selects it | Measured against Super on 46 debates: a verdict less often (80% against 87%), no better at telling fraud from honest trade, 5.2x the cost |
| `openbmb/MiniCPM-V-4_5` | document intake (vision) | Not an NVIDIA model: Token Factory offers no NVIDIA vision model yet |

- **Model choice is a config value.** Moving a hop from Nano to Super to Ultra is a string in `config.py`, so the HS experiment (Nano from 40.0% to 92.9% recall on the pairs set, 50.0% to 91.7% on the holdout) and the two model comparisons above cost nothing in plumbing.
- **No GPU provisioning.** Per-token access made a 550B model practical to test on 46 debates for about a dollar.
- **The port was a client change.** The JSON contract of each agent was kept, so moving from Vertex AI Gemini changed the base URL, the model ids and the sampling parameters.
- **Tavily** is a live runtime call in six places: compliance screening, route validation, investigation, the zero-day radar, the debate's search tool, and the Governance page's sanctions scan. In the radar and the debate, Nemotron calls the search as a tool and chooses the query.
- **Other Nebius services used: none.** Nebius AI Cloud (Serverless Endpoints and Jobs) is not used; the service runs on Cloud Run.

## What was significantly updated during the Submission Period

This began as an entry to a different hackathon (Google Cloud, Vertex AI Gemini): [CHAUPHUOCHOA/vf-logistics-hackathon-google](https://github.com/CHAUPHUOCHOA/vf-logistics-hackathon-google), first commit 29 Aug 2026 (after the Submission Period opened on 26 Aug), last commit `755d998` on 31 Aug (Vietnam time; 30 Aug 23:46 UTC on GitHub). This repository's first commit is `6bb1e59` (17 Sep).

| | Before (`755d998`) | Now |
|---|---|---|
| Models | 4 agents on Gemini via Vertex AI | 7 agents on Token Factory: Nemotron 3 Nano and Super (Ultra measured, not adopted), plus MiniCPM-V |
| Console | one static HTML page | Next.js 16 in the same container as the API |
| Evidence loop | a note | request, receive, re-evaluate, approve, execute, with an audit trail |
| Tests | none | 2,120 backend (96% coverage, with the Firestore and Pub/Sub emulators) and 1,731 frontend (62.0%); mypy 0 errors |
| CI | no test or lint CI, only a Cloud Build deploy config | five GitHub Actions jobs, each able to fail the build |

```bash
git fetch https://github.com/CHAUPHUOCHOA/vf-logistics-hackathon-google.git main
git diff --shortstat 755d998 6bb1e59   # the first port step: 32 files, +1,833 / -1,380
git diff --shortstat 6bb1e59 d76e64e   # to 14 Oct: 395 files, +199,584 / -6,681, in 131 commits
git diff --shortstat d76e64e febc252 -- src tests frontend   # 14 to 20 Oct, code and console only: 167 files, +23,957 / -292, in 53 commits
```

These commands need the full history (a plain `git clone`, not `--depth`). More detail: [REFERENCE.md](REFERENCE.md).

## Run it locally

Prerequisites: Python 3.11+, Node.js 20+ with npm, a Nebius Token Factory key (https://tokenfactory.nebius.com/), and optionally a Tavily key (https://tavily.com/).

```bash
git clone https://github.com/hoachauphuoc/vf-logistics-nebius-nvidia.git
cd vf-logistics-nebius-nvidia
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # Windows: copy .env.example .env
# edit .env: set NEBIUS_API_KEY, optionally TAVILY_API_KEY, and STORE_BACKEND=memory for a local run
PYTHONPATH=src python -m vf_logistics.app     # API on http://localhost:8080
```

On PowerShell: `$env:PYTHONPATH = "src"; python -m vf_logistics.app`. The console is a second process:

```bash
cd frontend && npm ci
NEXT_PUBLIC_DEMO_MODE=false FLASK_API_BASE=http://127.0.0.1:8080 npm run dev   # http://localhost:3000
```

Deployment to Cloud Run, the environment variables and the Firestore indexes are in [REFERENCE.md](REFERENCE.md).

## Signing in

The deployed console asks everyone to sign in. An anonymous visitor is sent to the sign-in screen and the API answers 401. **Continue as guest judge** on the login page signs in with one click and no password: the guest can look around, inject shipments or upload a document, block a case and simulate a policy, but cannot release or approve. Releasing cargo, clearing the board and publishing policy need a password account; two are listed in the Devpost testing instructions, one of them Reviewer-only so you can watch the access control refuse something. `GET /api/v1/auth/policy` (after sign-in) returns what every route enforces.

Try it in a few minutes: as guest judge, upload `sample_docs/injected_bol.pdf` on the DevOps page and watch Model Armor block it before any model reads it; open a held case for its findings, sources and dossier; simulate a policy on Governance.

## Reproducible testing

```bash
# Backend: 2,120 tests; CI runs them inside the Firestore and Pub/Sub emulators (firebase emulators:exec)
pip install "pytest>=8.0.0" "pytest-asyncio>=0.23.0" "pytest-cov>=4.1.0" "pytest-mock>=3.12.0"
python -m pytest tests/ -v --cov=vf_logistics --cov-fail-under=92

# Types and lint, as CI runs them
python -m mypy src/vf_logistics/ --ignore-missing-imports
python -m ruff check src/ tests/ scripts/ --select E,F,W --ignore E501,E402,W291,W293

# Frontend: 1,731 tests
cd frontend && npm ci && npm run lint && npm run test:coverage && npm run build

# The rules-only benchmark, in seconds and at no cost
python scripts/run_massive_benchmark.py --arm rules
```

Without the emulators the emulator tests skip. [REFERENCE.md](REFERENCE.md#reproducible-testing) has the mutation check, the real-model run and the receiver conformance script.

## Requirements of the hackathon rules, and where each is met

| Rule | Where it is met |
|---|---|
| Runtime call to Nebius Token Factory | every one of the seven agents |
| At least one NVIDIA open source model | Nemotron 3 Nano and Super in the pipeline; Ultra measured against Super and not adopted |
| Track | **Best Apps and Agents** |
| Significantly updated after 26 Aug 2026 | the section above; first commit here `6bb1e59`, 17 Sep |
| Working demo URL | the `vf-app` URL at the top |
| Public repository, detectable open source licence | MIT |
| README with setup and run instructions | this file |
| NVIDIA models, Token Factory and other Nebius services highlighted | "Models, and where Token Factory helped" |
| Feedback on Token Factory, AI Cloud and NVIDIA tools | [SUBMISSION.md](SUBMISSION.md), section *Feedback on Nebius Token Factory, AI Cloud, and NVIDIA tools* |
| Free access for judging, with credentials | "Signing in" |
| Demo video under three minutes, public on YouTube | link at the top |
| Bonus: functional runtime Tavily call | six call sites, `tavily_client.search_cached()` on the live compliance path |

## More

[REFERENCE.md](REFERENCE.md) (API, environment variables, measured results, findings, roadmap, repository layout) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/RECEIVER_CONTRACT.md](docs/RECEIVER_CONTRACT.md) · [docs/PROJECT_STORY.md](docs/PROJECT_STORY.md) · [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) · [SUBMISSION.md](SUBMISSION.md)

Released under the MIT licence; see [LICENSE](LICENSE).
