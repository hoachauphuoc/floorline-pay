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

## Trials 5 to 16 (7 Nov 2026)

Same path (`agent_payment.py`, Book and pay, PayPal Sandbox), read from `/api/proxy/orchestrator/case/<id>`. From trial 13 the grounded gate switch (`AGENT_GROUNDED_GATE`) was on. "Model" is the fraud model score; "gate" is `gate_risk` when the switch lowered it. The agent needs a score below 40, compliance CLEARED and no findings. No case reached AUTO_CLEARED: the agent paid in none of the sixteen trials, and a person decided trials 1 to 4 only.

| Case | Input | Outcome | Model / gate | Compliance | Findings (floor) | Why the agent did not pay |
|---|---|---|---|---|---|---|
| CASE-SHP-PAY-DEMO-5 | Saigon Textile, 6109.10, declared 400, freight 1500 | Escalated, case risk 93 | 78 / - | CLEARED 92 | none | Model score 78; investigation said identity fraud or double-brokering |
| CASE-SHP-PAY-DEMO-6 | same | Escalated, case risk 93 | 78 / - | REVIEW_REQUIRED 78 | none | Model score 78; compliance asked for review; investigation said undervaluation |
| CASE-SHP-PAY-DEMO-7 | same | Held for review, case risk 60 | 45 / - | CLEARED 95 | HS_DESCRIPTION_MISMATCH (40) | HS classifier disagreed with 6109; the finding floor is 40 |
| CASE-SHP-PAY-DEMO-8 | Saigon Textile, plywood 4412.33, 400 / 1500 | Escalated, case risk 70 | 55 / - | CLEARED 92 | HS_DESCRIPTION_MISMATCH (40) | HS classifier disagreed with 4412; model score 55 |
| CASE-SHP-PAY-DEMO-9 | Saigon Textile, 6109.10, 400 / 1500 | Pending a person, case risk 88 | 73 / - | REVIEW_REQUIRED 75 | EXPOSURE_CLAIM_UNSUPPORTED (0) | Model score 73 and compliance asked for review |
| CASE-SHP-PAY-DEMO-10 | Viet Tien Garment JSC, 6109.10, declared 20000, freight 1500 | Pending a person, case risk 12 | 12 / - | CLEARED 95 | none | Case risk and compliance were clean; the agent did not pay (the refusal reason was not read for this write-up) |
| CASE-SHP-PAY-DEMO-11 | Viet Tien, declared 500, freight 1300 | Held for review, case risk 40 | 40 / - | CLEARED 95 | none | Score 40 is not below the clear threshold of 40 |
| CASE-SHP-PAY-DEMO-12 | Viet Tien, declared 5000, freight 1500 | Escalated, case risk 78 | 78 / - | CLEARED 95 | none | Model score 78; investigation said over-invoiced freight |
| CASE-SHP-PAY-DEMO-13 | same, switch on | Escalated, case risk 35 | 35 / 0 | REVIEW_REQUIRED 80 | none | Compliance asked for the legal names as "N/A": it read only the personal-name fields (fixed in d64d530) |
| CASE-SHP-PAY-DEMO-14 | same, after d64d530 | Escalated, case risk 35 | 35 / - | REVIEW_REQUIRED 70 | none | Compliance cited speculative forced-labour and sanctions links from general web results about the garment sector |
| CASE-SHP-PAY-DEMO-15 | Minh Phuong Ceramics, ceramic tableware 6912.00, 5000 / 1500 | Held for review, case risk 45 | 45 / 40 | CLEARED 95 | HS_DESCRIPTION_MISMATCH (40) | Heading 6912 was missing from the HS reference and the classifier invented 6905 (fixed in 9ccefab) |
| CASE-SHP-PAY-DEMO-16 | same, after 9ccefab | Escalated, case risk 65 | 65 / 0 | REVIEW_REQUIRED 78 | none | Compliance compared the booking with a web import record of the real company, which names another consignee |

What this shows:
- Each refusal came from a deterministic finding or from a model verdict; the risk floor was never lowered and no rule was loosened to change an outcome.
- Three input bugs found by these trials were fixed (company names for compliance, headings 6109 and 4412, headings 6911 and 6912). After each fix the case stopped at a different place.
- Compliance on a real company name depends on what web search returns. The same company was CLEARED in trial 15 and REVIEW_REQUIRED in trial 16.
- Not shown: an agent payment to confirmed receipt. No claim of one is made.
