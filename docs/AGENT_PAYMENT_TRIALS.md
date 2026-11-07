# Agent payment trials on the Render deployment (6 Nov 2026)

Evidence of what happened when a booking made through Book and pay was offered to the agent payment path (`agent_payment.py`).
Setup: agent limit 500 USD published and boundary BOUNDARY-v2 active, both by a person (reviewer-a). PayPal Sandbox only.
Read from the case pages and from `/api/proxy/orchestrator/case/<id>`; nothing below is quoted from memory.

Common input: shipper Saigon Textile Export JSC (tax ID 0301234567), ho chi minh city to hanoi, declared 95 USD, freight 20 USD,
carrier Mekong Haulage Co (standing A, 3 confirmed payouts). The payment analyst said `normal` (confidence 0.92) and the web check found the carrier in every trial.

| Case | Deployed build | What differed | Outcome | Why the agent did not pay |
|---|---|---|---|---|
| CASE-SHP-PAY-DEMO-1 | fa179c2 | plywood, HS 4412.33, no countries | Awaiting a person, risk 78 (floor 0) | Shipper and receiver country missing (floor 45 each); HS classifier said plywood is 4418 (floor 40); investigation cited 39,000 USD against 95 USD with no basis, and the delegation boundary refused the hold: "requires human review (exposure_claim_unsupported)" |
| CASE-SHP-PAY-DEMO-2 | 03d8393 (both countries) | cotton t-shirts, HS 6109.10 | Escalated by the agent, risk 68 (floor 0) | HS classifier said 6108, not 6109 (floor 40) |
| CASE-SHP-PAY-DEMO-3 | 03d8393 | description "T-shirts, knitted, cotton" | Escalated by the agent, risk 72 (floor 0) | No deterministic finding left (only LOW_VALUE_DOMESTIC, floor 0). Compliance model: REVIEW_REQUIRED, score 78. Investigation: escalation required (confidence 0.68), citing shipper and receiver fields as N/A and missing identification data |

| CASE-SHP-PAY-DEMO-4 | a2d40be (plus receiver company) | same goods, receiver company Hanoi Garment Retail Co | Held for review, risk 68 (floor 0) | Compliance CLEARED (95), no escalation, analyst normal (0.96), carrier found, standing A, `auto_clear_permitted: true`. Two gates stay shut: the fraud model scored the case 68 and `orchestrator.py` asks for a score below `FRAUD_CLEAR_BELOW` (40) before it asks `agent_payment` at all; and `agent_payment.py:60` would refuse because the checks left a finding, LOW_VALUE_DOMESTIC (floor 0, informational) |

Probe VPROBE2 (deterministic verifier only, 72 value/freight/weight pairs on the same lane): below 100 USD every pair keeps LOW_VALUE_DOMESTIC; from 100 to 500 USD every pair (freight 20 to 450) gives FREIGHT_ANOMALY (floor 75-90). No pair gave an empty findings list, so under the current rules and limits the form cannot produce a case the agent may pay on this lane.

What this shows:
- The form gap was real and is fixed: shipper and receiver country (03d8393) and receiver company (a2d40be, not yet deployed when these trials ran).
- The remaining blockers are model verdicts. The rules floor stayed 0 in all three, and the models raised risk to 68-78. The agent path stayed closed, as designed.
- Seeded cases R1105A-E, 28,000 USD, were also marked "review required" by compliance and released by a person.
- No money moved in these three trials: the authorizations are still held, with no decision taken. A person releases or blocks them from each case page.
- Not tried: a trial after a2d40be is deployed.
