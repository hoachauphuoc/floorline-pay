/**
 * What Help mode says.
 *
 * Every entry is derived from the backend that actually implements the behaviour
 * -- the check functions and thresholds in `verifier.py`, the state tuples in
 * `orchestrator.py` -- rather than written from an idea of what the console
 * probably does. Help that describes behaviour the code does not have is worse
 * than no help: it is the one text on screen a reader has no way to verify, so it
 * gets believed.
 *
 * Numbers quoted here (500 USD/kg, 70% of the lane baseline, 100 USD) are the
 * live defaults. They are tenant-configurable through the Governance screen, so
 * the wording says "by default" wherever a rule can be re-tuned.
 *
 * Kept as plain data, separate from the components, so the copy can be reviewed
 * as prose without reading JSX.
 */

export type HelpEntry = {
  title: string;
  body: string;
  /** Optional second paragraph, for the "and why it is done that way" part. */
  note?: string;
};

// ---------------------------------------------------------------------------
// Screens, keyed by route. Extends the one-line `blurb` already on NAV_ITEMS.
// ---------------------------------------------------------------------------

export const NAV_HELP: Record<string, HelpEntry> = {
  "/pay": {
    title: "Floorline Pay",
    body:
      "How a shipper's money moves: held at booking, screened by deterministic checks, then paid to the carrier or returned on a published decision. The counts are read live from the payment ledger.",
    note:
      "Only the settler moves money, and it never calls a model.",
  },
  "/book": {
    title: "Book and pay",
    body:
      "Book a shipment and approve the freight on PayPal's own page. PayPal holds the money; nothing is captured until a decision is published.",
    note:
      "This runs on the PayPal sandbox, so no real money moves.",
  },
  "/payments": {
    title: "Payments",
    body:
      "The newest outcome of every payment: paid to the carrier and confirmed by PayPal, returned to the shipper after a block, or held back by the payment checks.",
  },
  "/settlement": {
    title: "Settlement",
    body:
      "Which parties are frozen, and what became of each payout sent to PayPal, so a payout that is not confirmed yet is shown as not confirmed.",
    note:
      "A freeze is lifted only by a person signed in with a password.",
  },
  "/carriers": {
    title: "Carriers",
    body:
      "Who each carrier may be paid to: the PayPal account registered for it, who verified it and when, and any change still waiting for a second person.",
    note:
      "A payee changed less than 14 days before a payout holds that payout, and a payout to any other account is held for a person.",
  },
  "/controls": {
    title: "Payment controls",
    body:
      "The pause switch and the spending limits. Any operator can stop payments; only a governance admin can start them again or change a limit.",
    note:
      "The agent limit is separate: 0 turns agent payments off, and every payment then waits for a person.",
  },
  "/defense": {
    title: "Defense",
    body:
      "Ten attacks replayed against the payment path, with the result of each. None of them moves money.",
  },
  "/today": {
    title: "Today",
    body:
      "Every case waiting on someone, grouped by what it is waiting for: documents from the shipper, a reviewer's decision, or a decision above the reviewer. Each card names who holds it and the next thing to do.",
    note:
      "The grouping comes from the API (worklist.py), not from this screen, so the count in each group is the whole queue rather than the page that happened to load.",
  },
  "/outcomes": {
    title: "Outcomes",
    body:
      "Whether the work got done: cases resolved and how, time from arrival to resolution, rounds of requests for information, model cost per resolved case, and the cases still held.",
    note:
      "Human handling time is the time a reviewer actually had a case page open and in front of them, recorded as it happens. A figure nobody measured is shown as a dash, never as zero.",
  },
  "/": {
    title: "Pipeline",
    body:
      "The live board. Each card is one shipment moving through the workflow, and each column is the stage it has reached. Cards advance on their own; the ones that stop in an awaiting column are the ones needing a person.",
    note:
      "Columns fold several internal states together. Cleared holds shipments released automatically and shipments a reviewer released, because both mean cleared to ship.",
  },
  "/radar": {
    title: "Risk Radar",
    body:
      "Completed audits, newest first, with the risk verdict and what drove it. Use this to look back at decisions; use Pipeline to watch work in progress.",
  },
  "/review": {
    title: "Review queue",
    body:
      "Only the cases waiting on a human decision. Each one shows the findings, the paperwork the decision was made from, and what the agent did or proposed.",
    note:
      "A decision here is recorded against your name and writes two audit entries. It cannot be undone from this console.",
  },
  "/audit": {
    title: "Audit trail",
    body:
      "Every action the system took or was refused, in order, with its outcome. Records here are immutable once written -- they can be read and filtered but never edited or deleted.",
  },
  "/governance": {
    title: "Governance",
    body:
      "The rules the agent operates under: what it may execute on its own, and the pre-filter lists that decide what gets fast-tracked or stopped before any model runs.",
    note:
      "Changes can be simulated against recent cases before publishing, so you can see what a new boundary would have changed without writing anything.",
  },
  "/devops": {
    title: "DevOps",
    body:
      "Put work into the real pipeline: the scripted batch, a bulk load, a hand-written shipment or an uploaded document. Every control here is an operator action, because each one creates cases and spends model tokens.",
  },
  "/agents": {
    title: "Agent Console",
    body:
      "Which model answered which question, how many tokens it used, and what it cost. Also the prompt-injection screen that every uploaded document passes through.",
  },
  "/evaluation": {
    title: "Evaluation",
    body:
      "Detection, false alarms and cost, measured by the benchmark harness on a fixed synthetic corpus and served from the reports committed with the code. Nothing on the page is typed in.",
    note:
      "Thresholds were chosen on the dev half of the corpus; the holdout half is what is reported. The caveats at the top of the page travel with every number.",
  },
  "/admin": {
    title: "Access Control",
    body:
      "Who you are to the API, which role that gives you and why, and the role every API route actually enforces -- read from the running service rather than written down.",
    note:
      "Roles are assigned by email on the API. Signing in proves who you are; it does not make you anything.",
  },
};

// ---------------------------------------------------------------------------
// Case states
// ---------------------------------------------------------------------------

export const STATE_HELP: Record<string, HelpEntry> = {
  INGESTED: {
    title: "Queued",
    body:
      "Accepted and waiting for the first pass. The deterministic checks -- freight, value density, HS code, counterparty lists -- run here, before any model is called.",
  },
  SPECIALISTS_DONE: {
    title: "Fraud and compliance done",
    body:
      "The fraud and compliance agents have both answered. If either raised something serious the case goes on to a full investigation rather than clearing.",
  },
  INVESTIGATED: {
    title: "Investigated",
    body:
      "The investigation agent has written its assessment and the case is about to be routed to an outcome.",
  },
  AUTO_CLEARED: {
    title: "Cleared automatically",
    body:
      "Low risk, compliance clear, and inside the authority the agent has been delegated. No person was required.",
    note:
      "Cases cleared by the pre-filter alone never reach a model, which is why some cleared cases show no token cost at all.",
  },
  HELD_FOR_REVIEW: {
    title: "Held for review",
    body:
      "Below the investigation bar but not cleared: risk 40 to 69, a deterministic finding that blocks auto-clear, a compliance status that is missing or unreadable, or an agent whose output failed validation (the last two at HIGH priority). An analyst is assigned, but this is a queue rather than an alarm.",
  },
  ESCALATED: {
    title: "Escalated",
    body:
      "Investigated and held. Compliance returned REVIEW_REQUIRED or the effective risk reached 70, the investigation agent wrote its report, and the delegation boundary let the agent act: the shipment is held, a SAR drafted for signature and an analyst assigned. A person must decide.",
    note:
      "Compliance BLOCKED, a pre-filter blacklist match and every case the auto-debate ran on end in Awaiting a person instead: the default boundary does not let the agent act on them at all.",
  },
  PENDING_HUMAN: {
    title: "Awaiting a person",
    body:
      "Nothing leaves this state without a named reviewer making a decision. The agent has finished its work and stopped.",
    note:
      "A case lands here when the delegation boundary refused the outcome the agent proposed: compliance BLOCKED (which every pre-filter blacklist match is), a disputed score (every case the auto-debate ran on), an investigation exposure figure the shipment does not support, or a document that tried to inject instructions or set a decision field. A document blocked at intake, and a reviewer's request for more information, land here too.",
  },
  RELEASED_BY_HUMAN: {
    title: "Released by a reviewer",
    body:
      "A named reviewer cleared this shipment to ship, and their name and reason are on the audit trail.",
  },
  BLOCKED_BY_HUMAN: {
    title: "Blocked by a reviewer",
    body:
      "A named reviewer refused the shipment. There is no automatic block: this outcome only ever comes from a person.",
  },
  DEAD_LETTER: {
    title: "Abandoned after retries",
    body:
      "The workflow failed repeatedly and gave up rather than looping. This is work for a person, and the last error is recorded on the case.",
  },
};

// ---------------------------------------------------------------------------
// Severities
// ---------------------------------------------------------------------------

export const SEVERITY_HELP: Record<string, HelpEntry> = {
  CRITICAL: {
    title: "Critical",
    body:
      "On its own, enough to stop the shipment. A single critical finding sets the risk score at or near the top of the scale regardless of what else is clean.",
  },
  HIGH: {
    title: "High",
    body:
      "A serious finding. It blocks auto-clear, most HIGH findings reach the investigation bar on their own, and two of them raise the floor to 80.",
  },
  MEDIUM: {
    title: "Medium",
    body:
      "Raises the floor into the review band and blocks auto-clear, so one of these queues the case for a person. It does not count toward corroboration, which only HIGH and critical findings do.",
  },
  LOW: {
    title: "Low",
    body: "Recorded and shown, but does not raise the risk floor by itself.",
  },
  INFO: {
    title: "Informational",
    body:
      "Context rather than a concern -- most often a note that a check was skipped or could not run.",
  },
  CLEAR: {
    title: "Clear",
    body:
      "A positive signal, such as a match against the trusted-counterparty list. These can allow a case to skip the models entirely.",
  },
};

// ---------------------------------------------------------------------------
// Findings. Wording and thresholds taken from the matching check in verifier.py.
// ---------------------------------------------------------------------------

export const FINDING_HELP: Record<string, HelpEntry> = {
  SANCTIONS_MATCH: {
    title: "Sanctions match",
    body:
      "A counterparty on this shipment matches an entry in the sanctions index. Treated as disqualifying on its own.",
  },
  SANCTIONS_SCREENING_UNAVAILABLE: {
    title: "Sanctions screening unavailable",
    body:
      "The screening step could not complete, so nothing was checked against the index.",
    note:
      "Recorded as a concern rather than ignored. The absence of a finding from a check that did not run is not a clearance.",
  },
  BLACKLIST_MATCH: {
    title: "Blacklist match",
    body:
      "The company name appears on this tenant's internal blacklist, maintained on the Governance screen. Stops the case before any model runs.",
  },
  BLACKLIST_TAX_ID: {
    title: "Blacklisted tax ID",
    body:
      "The tax ID appears on the internal blacklist even though the company name does not. Catches a blocked counterparty trading under a new name.",
  },
  WHITELIST_MATCH: {
    title: "Trusted counterparty",
    body:
      "The shipper matches the trusted-counterparty register, so the case can be cleared without calling a model.",
    note:
      "The fast path is withdrawn if anything else on the record is seriously adverse, so naming a trusted company does not buy a free pass.",
  },
  WHITELIST_IDENTITY_MISMATCH: {
    title: "Trusted identity mismatch",
    body:
      "The company and the tax ID each match a trusted entry, but not the same one. A half-correct identity claim is treated as worse than an unknown counterparty, not better.",
  },
  SHIPPER_IDENTITY_MISMATCH: {
    title: "Shipper identity mismatch",
    body:
      "The tax ID on the paperwork is one we hold, but under a different company name -- the cheapest way to try to inherit another counterparty's good history.",
  },
  SHIPPER_NO_HISTORY: {
    title: "No shipping history",
    body: "This counterparty has no prior shipments on file.",
  },
  SHIPPER_THIN_HISTORY: {
    title: "Short trading record",
    body:
      "Between two and nine prior shipments on file. Shown as context, not scored: it raises no floor and holds no shipment on its own.",
    note:
      "Measured on the benchmark corpus, a record this short was as common among honest shippers as among the attacks it was meant to catch. No record at all is still a HIGH finding.",
  },
  SHIPPER_HISTORY_UNVERIFIED: {
    title: "History unverified",
    body:
      "No transaction history was available from internal records, and it cannot be taken from the document.",
    note:
      "A document is not allowed to assert its own shipper's history, because a forged one would simply claim a long record.",
  },
  RECENTLY_REGISTERED_SHIPPER: {
    title: "Recently registered shipper",
    body:
      "The counterparty was registered very recently. Common in shell-company patterns, where a new entity is created for a single consignment.",
  },
  HIGH_RISK_DESTINATION: {
    title: "High-risk destination",
    body:
      "The destination country is on the restricted list: Afghanistan, Belarus, Cuba, Iran, Myanmar, North Korea, Pakistan, Russia, Sudan, Syria or Venezuela.",
  },
  HIGH_RISK_ORIGIN: {
    title: "High-risk origin",
    body:
      "The shipment leaves from a country on the same restricted list. Sanctions apply to goods coming from a country, not only going to it -- and a false origin there is a known way to dodge anti-dumping duty.",
  },
  HIGH_RISK_TRANSIT: {
    title: "High-risk transit",
    body:
      "The route passes through a country on the restricted list. Sanctions are written 'to, from, or through': a freight forwarder was fined by OFAC for shipments that only passed through.",
  },
  MULTIPLE_DIVERSION_HUBS: {
    title: "Multiple transhipment hubs",
    body:
      "The routing passes through more than one known diversion hub -- Busan, Dubai, Hong Kong, Jebel Ali, Kaohsiung, Port Klang, Singapore or the UAE. Each is a legitimate port; stacking several is how a shipment's real destination gets obscured.",
  },
  ROUTE_CHANGED_AFTER_BOOKING: {
    title: "Route changed after booking",
    body:
      "The routing was amended after the booking was made, which is how a consignment reaches a destination it was not approved for.",
  },
  DUAL_USE_HS_CODE: {
    title: "Dual-use HS code",
    body:
      "The declared heading falls in a category with both civilian and military application, so it may need an export licence regardless of the stated use.",
  },
  HS_CODE_MALFORMED: {
    title: "Malformed HS code",
    body:
      "The declared code is not a valid Harmonized System heading, so nothing downstream can classify the goods from it.",
  },
  HS_DESCRIPTION_MISMATCH_DUAL_USE: {
    title: "Description does not match a dual-use heading",
    body:
      "The goods described and the heading declared disagree, and the correct heading appears to be a controlled one. The most serious form of misdeclaration.",
  },
  HS_DESCRIPTION_MISMATCH_LOW_CONFIDENCE: {
    title: "Possible description mismatch",
    body:
      "The classifier thinks the description and the declared heading disagree, but is not confident. Recorded without raising the risk floor on its own.",
  },
  HS_DESCRIPTION_CHECK_UNAVAILABLE: {
    title: "Classification check unavailable",
    body:
      "The description-to-heading check could not run, so the declared code was accepted as given.",
  },
  HS_DESCRIPTION_CHECK_CONTRADICTORY: {
    title: "Classification reply contradicted itself",
    body:
      "The classifier called the description inconsistent with the declared heading, then named that same heading as the right one. Shown as context and not acted on: a reply that disagrees with itself is not a finding.",
  },
  FREIGHT_MISSING: {
    title: "No freight charge",
    body:
      "No shipping cost was stated, so the pricing cannot be validated at all. Treated as a data-quality problem rather than a pricing one, and it applies at any shipment size.",
  },
  FREIGHT_ANOMALY: {
    title: "Freight out of line",
    body:
      "The freight charge is far below the baseline for this lane: below 70% is a concern, below 50% serious, below 25% critical. Far above the record's own route average is also flagged, as over-invoicing is its own way of moving value.",
    note:
      "Skipped entirely below the low-value threshold. A parcel is not an underpriced container, and applying the lane baseline to one made the low-value fast path unreachable.",
  },
  FREIGHT_ABOVE_LANE_TYPICAL: {
    title: "Freight above the lane figure",
    body:
      "More than three times the typical charge for one consignment on this lane. Shown as context, not scored: that figure does not scale with the size or value of the shipment, so a large consignment exceeds it in the ordinary course.",
    note:
      "Scored as a finding when the record carries its own route average, which describes comparable consignments.",
  },
  VALUE_DENSITY_HIGH: {
    title: "Value per kilogram too high",
    body:
      "Above 500 USD/kg by default, which is consistent with high-value or controlled goods rather than the general cargo declared.",
  },
  VALUE_DENSITY_LOW: {
    title: "Value per kilogram too low",
    body:
      "Below 1 USD/kg by default -- a classic under-invoicing pattern, where duty is paid on a fraction of the real value.",
  },
  LOW_VALUE_DOMESTIC: {
    title: "Low-value domestic shipment",
    body:
      "Under 100 USD by default, on a known domestic route, with no dual-use heading. Cleared by arithmetic without calling a model.",
  },
  ZERO_DAY_ADVERSE_MEDIA: {
    title: "Adverse media found",
    body:
      "A live search found recent reporting against this counterparty that no static sanctions list would yet carry.",
  },
  ZERO_DAY_ADVERSE_MEDIA_LOW_CONFIDENCE: {
    title: "Possible adverse media",
    body:
      "Something was found but the match to this counterparty is weak. Shown for context without raising the risk floor.",
  },
  ZERO_DAY_CHECK_UNAVAILABLE: {
    title: "Adverse-media check unavailable",
    body: "The search provider could not be reached, so no live check was made.",
  },
  ZERO_DAY_SEARCH_DID_NOT_RUN: {
    title: "Adverse-media search did not run",
    body:
      "The case qualified for a live search but none was performed, so this counterparty has not been checked against current reporting.",
  },
  EXPOSURE_CLAIM_UNSUPPORTED: {
    title: "Exposure figure unsupported",
    body:
      "The investigation quoted a financial exposure that the record does not support. Recorded so a number nobody can source does not end up in a filing.",
  },
};

// ---------------------------------------------------------------------------
// Reviewer actions
// ---------------------------------------------------------------------------

export const REVIEWER_ACTION_HELP: Record<string, HelpEntry> = {
  release: {
    title: "Release",
    body:
      "Clears the shipment to ship. Your name and note go on the case, and any drafted SAR on it is marked as signed off.",
    note: "Recorded immediately and cannot be undone from this console.",
  },
  block: {
    title: "Block",
    body:
      "Refuses the shipment. A note is required: a refusal nobody has to justify is not a control.",
    note: "Recorded immediately and cannot be undone from this console.",
  },
  request_info: {
    title: "Request information",
    body:
      "Leaves the case open and records what is missing. Use this rather than blocking when the paperwork is incomplete rather than wrong. A note is required.",
  },
};

// ---------------------------------------------------------------------------
// KPI tiles and panels, keyed by a stable id passed at the call site.
// ---------------------------------------------------------------------------

export const PANEL_HELP: Record<string, HelpEntry> = {
  // Pipeline KPIs
  "kpi.in-flight": {
    title: "In flight",
    body:
      "Cases the worker will pick up on its next pass: queued, fraud and compliance done, and investigated. A number that stays still while work is arriving means the worker is not running.",
  },
  "kpi.awaiting-human": {
    title: "Awaiting human",
    body:
      "Cases stopped on a person: awaiting a person, held for review, and escalated. Nothing leaves these states without a named reviewer.",
  },
  "kpi.agent-calls": {
    title: "Agent calls",
    body:
      "Model invocations across every case in the collection, counted exactly rather than over the fetched window.",
  },
  "kpi.spend": {
    title: "Estimated spend",
    body:
      "Estimated from token counts and per-model pricing. Cases the pre-filter cleared contribute nothing, which is the point of having a pre-filter.",
  },
  "kpi.delegated-authority": {
    title: "Delegated authority",
    body:
      "Whether the agent may execute protected actions on its own. With no published boundary it is suspended, every outcome stays a proposal, and each case lands on a human.",
    note:
      "That is correct fail-closed behaviour, not a fault -- but it explains a board where nothing ever clears.",
  },

  // Floorline Pay overview and Payments
  "pay.paid-confirmed": {
    title: "Paid to carriers, confirmed by PayPal",
    body:
      "Cases the settler paid whose payout PayPal has since confirmed. Only this count means the carrier has the money.",
  },
  "pay.paid-accepted": {
    title: "Payouts accepted, not confirmed yet",
    body:
      "The settler sent the payout and PayPal accepted it, but PayPal has not confirmed it yet. A payout state the console does not recognise is counted here too, as a warning.",
  },
  "pay.paid-waiting": {
    title: "Payouts sent, not received yet",
    body:
      "PayPal reports the payout as still pending, so the carrier does not have it yet.",
  },
  "pay.paid-failed": {
    title: "Payouts failed at PayPal",
    body:
      "PayPal reports the payout failed. The carrier is frozen until a governance admin, signed in with a password, lifts the freeze.",
  },
  "pay.paid-returned": {
    title: "Payouts returned by PayPal",
    body:
      "PayPal returned, refunded or reversed the payout, and the carrier is frozen. Check the payee before the freeze is lifted.",
  },
  "pay.voided": {
    title: "Returned to shippers",
    body:
      "Cases where the settler voided the PayPal hold after a published block. The shipper's money was never captured.",
  },
  "pay.held": {
    title: "Held by the checks",
    body:
      "Cases where the settler refused to move money: a payment check raised a finding, a party is frozen, a limit or the carrier's credit cap was passed, a second approval is missing, or the PayPal hold did not match. Nothing was captured; the reason is in the Why column on Payments.",
  },
  "pay.decision-not-sent": {
    title: "Decisions not sent",
    body:
      "A person's decision was saved on the case but never reached the settler, so no money moved. Send it again from the case.",
  },
  "pay.sent-today": {
    title: "Sent for payout today in USD",
    body:
      "The total of payouts the settler sent since midnight UTC, for all carriers. It counts what was sent, not what PayPal confirmed, and the daily limits on Payment controls are checked against it.",
  },
  "payments.state": {
    title: "State",
    body:
      "What the settler last did with this case's money. A paid case reads as paid to the carrier only once PayPal confirms the payout; until then it says accepted or sent.",
  },
  "payments.why": {
    title: "Why",
    body:
      "The reason the settler recorded with that outcome, for example which check held the payout or which limit it would pass.",
  },
  "payments.captured": {
    title: "Captured",
    body:
      "PayPal's id for the capture, the moment the shipper's held money was taken. Empty means nothing was captured.",
  },
  "payments.paid-out": {
    title: "Paid out",
    body:
      "PayPal's id for the payout sent to the carrier. It shows the payout was sent, not that it arrived; the State column says whether PayPal confirmed it.",
  },
  "payments.voided": {
    title: "Voided",
    body:
      "PayPal's id for the void that released the shipper's hold after a published block. A voided case was never captured.",
  },

  // Board
  "board.columns": {
    title: "The board",
    body:
      "One card per shipment, in the column for the stage it has reached. Click a card for the full trace: every agent hop, every action, and every finding.",
    note:
      "Columns group several internal states. Awaiting a person also holds held-for-review, escalated and abandoned cases; Cleared holds both automatic and reviewer releases.",
  },

  // Payment controls
  "controls.pause": {
    title: "Pause switch",
    body:
      "Any operator signed in with a password can pause payments, with a reason; only a governance admin can resume them. While paused, releases wait and are retried after the resume, and a block still returns the shipper's money.",
  },
  "controls.limits": {
    title: "Limits in force",
    body:
      "The limits the settler checks before every payout, the agent's included; a governance admin signed in with a password publishes them, with a reason. If they cannot be read, nothing is paid.",
    note:
      "A payout must also fit the carrier's credit cap, so the smaller of the two applies.",
  },
  "controls.max_payout_usd": {
    title: "Largest single payout (USD)",
    body:
      "A payout above this is held by the settler and nothing is paid; waiting does not change that.",
  },
  "controls.carrier_daily_usd": {
    title: "Per carrier per day (USD)",
    body:
      "A payout that would take one carrier past this total since midnight UTC waits and is retried when the day turns over.",
  },
  "controls.tenant_daily_usd": {
    title: "All carriers per day (USD)",
    body:
      "A payout that would take today's total for all carriers past this waits and is retried when the day turns over.",
  },
  "controls.carrier_daily_count": {
    title: "Payouts per carrier per day",
    body:
      "Once a carrier has had this many payouts today, the next one waits until the day turns over.",
  },
  "controls.agent-limit": {
    title: "Agent payments",
    body:
      "The agent may pay a case without a person only when the case is clean and the payout is below this limit; anything else waits for a person, and the settler runs every check again before money moves. 0 turns agent payments off, and with no limit published every payment waits for a person.",
    note:
      "Clean means no findings, a floor that permits an automated decision, the analyst reading it as normal, the carrier found online, credit standing A and no second approval needed. Only a governance admin signed in with a password publishes the limit.",
  },

  // Agents screen
  "agents.cost-by-agent": {
    title: "Cost by agent",
    body:
      "One row per agent, ordered by spend. The dollar figure is summed from each call's own rate rather than from these token totals, because the rate depends on which model ran the step: Nano on fraud, compliance, HS and zero-day, Super on investigation and the debate. Calls is how many times that agent was invoked; the two token figures are input then output. Windowed over the cases currently on the board, not the whole collection.",
    note:
      "Ordered by money rather than by tokens on purpose — Super's per-token rate is 4-5x Nano's, and a debate makes several tool-call rounds, so the most expensive agent is usually not the one that produced the most tokens. The headline totals on the Pipeline board are exact. A per-agent breakdown across every case would need a stored field per agent per case.",
  },
  "agents.injection-probe": {
    title: "Prompt-injection screen",
    body:
      "Runs text through the same two-stage defence every uploaded document passes: a pattern screen in code, then Model Armor. These are the exact functions the document pipeline calls, not a demonstration of them.",
  },
  "agents.worker": {
    title: "Worker",
    body:
      "The process that advances cases. On-demand mode runs the whole workflow inside the request that submitted it, which is what makes scale-to-zero viable; a stopped worker in polling mode means cases will sit in flight.",
  },
  "agents.models": {
    title: "Models",
    body:
      "Which model answers which question, and what each costs per million tokens. Document reading needs vision, so it uses a different model from the reasoning steps.",
  },

  // Settlement and Carriers
  "settlement.frozen": {
    title: "Frozen parties",
    body:
      "A carrier or shipper is frozen when PayPal reports a dispute, a denied, refunded or reversed capture, or a payout that failed or came back. The settler pays nothing to a frozen party until a governance admin signed in with a password lifts the freeze.",
    note:
      "No webhook, model or release decision can lift a freeze.",
  },
  "settlement.lift": {
    title: "Lift a freeze",
    body:
      "Choose Select on a frozen party, write why it is safe to pay it again, and lift. Your name and the reason are kept on the audit trail; every other check still runs on the next payout.",
  },
  "settlement.unbound": {
    title: "Holds with no case",
    body:
      "PayPal holds the shipper's money for these orders, but the booking never became a case, so no decision can be made on them. Finish the booking from the link.",
  },
  "settlement.events": {
    title: "PayPal events waiting",
    body:
      "Messages from PayPal that arrived before Floorline could match them to a settlement. Until they are applied, a refund or dispute among them has frozen nobody.",
  },
  "settlement.payouts": {
    title: "Payouts",
    body:
      "Each payout as PayPal last reported it. Only Confirmed by PayPal means the carrier has the money; a payout that failed or came back freezes the carrier.",
  },
  "carriers.register": {
    title: "Register a payee",
    body:
      "Names the PayPal account a carrier may be paid at. It stays pending until a different person verifies it after a callback; until then the old payee stays in force, and a payout to any other account is held for a person.",
  },
  "carriers.request": {
    title: "Message that asked for the change",
    body:
      "Paste the carrier's request. It is screened for wording typical of payment-diversion fraud; a flagged request holds payouts to that payee until it is registered and verified again, and only the screen's reasons are kept.",
  },
  "carriers.callback": {
    title: "Callback note",
    body:
      "Who you called, on which number, and what they confirmed. The person who registered the payee cannot verify it, and a payee changed less than 14 days before a payout holds that payout.",
  },

  // Review screen
  "review.queue": {
    title: "Queue",
    body:
      "Cases waiting on a decision, oldest first. The count here should match the Awaiting human tile on the Pipeline board.",
  },
  "review.findings": {
    title: "Findings",
    body:
      "What the checks found, each with the minimum risk score that finding justifies on its own -- its floor. The case's score is at least the highest floor present.",
  },
  "review.paperwork": {
    title: "Paperwork",
    body:
      "The bill of lading the decision is being made from. For a submitted shipment this is rendered from the record; for an upload it is the file as received.",
  },
  "review.decision": {
    title: "Decision",
    body:
      "Release, block, or ask for more information. The decision is recorded under your signed-in account -- never a typed name -- and a note is required for anything other than a plain release.",
  },

  // Book and Defense
  "book.shipping_cost": {
    title: "Freight to pay (USD)",
    body:
      "The amount PayPal holds on the shipper's account at checkout. It is held, not taken: a published decision to pay captures it and a block voids it.",
  },
  "book.carrier_payee_email": {
    title: "Carrier PayPal account for this load",
    body:
      "The account this load would pay. It is checked against the payee verified for this carrier on Carriers; a different account, or no verified payee, holds the payout for a person.",
  },
  "book.payout_amount": {
    title: "Payout to the carrier (USD)",
    body:
      "What the carrier is paid from the hold; blank means the freight. A payout above the booked freight is a finding that holds it, and the settler never pays more than PayPal holds.",
  },
  "book.after-paypal": {
    title: "After PayPal",
    body:
      "PayPal sends you back to Floorline, which asks PayPal to authorize the hold and submits the shipment as a case. If PayPal no longer holds the order nothing is submitted; if a step fails the booking is kept and a reload retries the same order without a second hold.",
  },
  "defense.replay": {
    title: "The attack replay",
    body:
      "Each row is one way to be paid wrongly, replayed against the same checks the pipeline and the settler run. The replay reads and writes nothing and calls neither PayPal nor a model, so none of them can move money. A row marked Proved by a test cannot be replayed here because it needs the store or PayPal.",
  },
  "defense.catches": {
    title: "What the checks catch",
    body:
      "How often each layer flags a fraudulent payout, and how often it flags a legitimate one, measured on a synthetic corpus and shown with a 95% confidence interval. Each row adds one layer to the row above it.",
  },

  // Today screen
  "today.groups": {
    title: "Waiting on someone",
    body:
      "Every case that a person still has to act on, counted across the whole organisation rather than the page that loaded. A case is in exactly one group.",
  },
  "today.awaiting_documents": {
    title: "Waiting for documents",
    body:
      "A reviewer pressed Request info, so the case waits on the shipper. Nothing a colleague does will move it until the documents arrive; the card says what was asked for.",
  },
  "today.awaiting_review": {
    title: "Waiting for a reviewer",
    body:
      "Held by the rules, parked by the delegation boundary, or stopped by the injection screen. The card's next step says which, from the case's own record.",
  },
  "today.awaiting_sign_off": {
    title: "Waiting for sign-off",
    body:
      "The agent escalated these. An escalation is not final until a person signs it off or overturns it, and a SAR draft needs a signature before it can be filed.",
  },
  "today.awaiting_execution": {
    title: "Approved, not yet carried out",
    body:
      "A decision that has been approved but not executed. Shown only when there is one: today a reviewer's decision is carried out in the same step it is recorded.",
  },

  // Case page
  "case.timeline": {
    title: "What happened",
    body:
      "The case in order: when it arrived, what the rules found and the floor each finding set, which agents ran, what the delegation boundary refused and why, what was asked for, and who decided what.",
    note:
      "Drawn from the case record and its audit trail. A refusal recorded only on the trail still appears here.",
  },

  // Outcomes screen
  "outcomes.resolved": {
    title: "Resolved",
    body:
      "Cases that arrived in the window and are finished: cleared by the rules alone, cleared after the agents ran, or released or blocked by a named reviewer.",
  },
  "outcomes.time_to_resolution": {
    title: "Arrival to resolution",
    body:
      "From the case arriving to it being finished, for a reviewer's case the moment their decision was recorded. This includes time the case sat waiting, so it is elapsed time, not effort.",
  },
  "outcomes.handling": {
    title: "Human handling time",
    body:
      "Time a reviewer had the case's page open, in view, and was using it -- idle tabs stop counting after a minute. This is effort, as opposed to the elapsed time beside it.",
    note:
      "Recording began with this screen, so older cases have none. The coverage line says how many of the reviewer-decided cases it was measured on.",
  },
  "outcomes.rounds": {
    title: "Requests for information",
    body:
      "How many times a reviewer had to ask the shipper for more before a case could be decided. Fewer rounds for the same cases means the right documents were asked for first time.",
  },
  "outcomes.cost": {
    title: "Model cost per resolved case",
    body:
      "Nebius Token Factory spend recorded on each resolved case, from the tokens its model calls used at the configured rates.",
    note:
      "Web searches are counted per case but not priced into this figure.",
  },
  "outcomes.latency": {
    title: "Model latency per case",
    body:
      "The sum of model call times on each resolved case. Cases with no recorded model time are left out rather than counted as instant.",
  },
  "outcomes.held": {
    title: "Held now",
    body:
      "Cases waiting on a person at this moment, whatever window is selected. The Today screen lists them.",
  },
  "outcomes.refused": {
    title: "Actions refused",
    body:
      "Agent actions the delegation boundary refused in the window, read from the audit trail. A refusal is the control working, not a failure.",
  },

  // Audit screen
  "audit.trail": {
    title: "Audit trail",
    body:
      "Every action attempted, with its outcome and the reason it was refused where it was. Filters are exact-match on one indexed field at a time.",
    note:
      "Rows are immutable once written. The store refuses an update or a delete on an audit record, so this trail cannot be tidied after the fact.",
  },

  // Governance screen
  "governance.boundary": {
    title: "Delegation boundary",
    body:
      "The list of actions the agent may execute without a person. Anything outside it stays a proposal, and the refusal is recorded against the case.",
  },
  "governance.simulate": {
    title: "Simulate",
    body:
      "Replays a candidate boundary against recent cases and reports what would change. Nothing is written and no action runs.",
  },
  "governance.history": {
    title: "History",
    body:
      "Every boundary ever published, including superseded and revoked versions, because \u201cwhat was the agent allowed to do in March\u201d is a question an auditor asks.",
  },
  "governance.prefilter": {
    title: "Pre-filter rules",
    body:
      "The trusted-counterparty register, the blacklists, the safe domestic routes and the low-value threshold. These decide what is cleared or stopped by arithmetic, before any model is called.",
    note:
      "Held per tenant. They used to be module globals, which meant one tenant's blacklist edit changed what every tenant was screened against.",
  },

  // Operations screen
  "devops.scripted-batch": {
    title: "Scripted batch",
    // Three cases, not four branches. This said "a sanctions hit, an export-control name,
    // an HS mismatch and a clean low-value parcel" -- `simulator.scripted_shipments`
    // returns CLEAN, MID and DIRTY, there is no HS-mismatch case, and the clean one sails
    // internationally for USD 9,600. Kept in step with the panel note in app/devops/page.tsx.
    body:
      "Three shipments, one per outcome, so a single click shows all three ways the pipeline can end: cleared by the agents, held for a human, escalated. Cotton garments to Singapore from a shipper with 412 prior shipments and a tax ID on file. Furniture to Busan whose paperwork is clean but whose freight sits under the route average, from a shipper with nine shipments behind it. And frequency converters to Karachi declared as agricultural end use, from a company registered eleven days ago with no tax ID, booked at 14% of the route's normal freight with two transhipments added after booking.",
    note:
      "Fixed rather than random on purpose, so two runs are comparable. Re-running it does not create duplicates -- a case id derives from the shipment, so the same shipment reuses its case.",
  },
  "devops.bulk-load": {
    title: "Bulk load",
    body:
      "Randomised shipments, for watching the board under load and seeing the real cost per case at volume.",
    note:
      "Every shipment that reaches an agent spends tokens. This is the one control on this screen that can run up a bill.",
  },
  "devops.submit": {
    title: "Submit one by hand",
    body:
      "Posts a single shipment and runs it end to end, exactly as an integration would. Useful for reproducing one specific finding.",
    note:
      "Blank fields are omitted rather than sent empty, because an absent field and an empty one produce different findings.",
  },
  "devops.upload": {
    title: "Upload a document",
    body:
      "Accepts a PDF or an image (.png, .jpg, .jpeg, .webp). The file passes the injection screen and Model Armor before any model reads it, then goes through extraction and the full workflow.",
    note:
      "Only the first page of a PDF is read today. If a document has more, the case records that the remaining pages were not examined rather than implying they were clean.",
  },
  "devops.health": {
    title: "Health",
    body:
      "Whether the state store and the worker are reachable, and which models are configured. If the store fell back to memory, this says so -- cases will not survive a restart.",
  },

  // Radar screen
  "radar.audits": {
    title: "Completed audits",
    body:
      "Finished verdicts, newest first. The badge shows the risk state; hover or open a row for what drove it.",
  },

  // Access Control screen
  "access.you": {
    title: "You, as the API sees you",
    body:
      "The identity and role the API resolved for your requests, and which setting gave it. Every locked control in the console is locked for the reason shown here.",
  },
  "access.posture": {
    title: "Sign-in posture",
    body:
      "What the console requires before it forwards a request. Reads are public on this deployment for the judging window; writes always need a signed-in session.",
  },
  "access.roles": {
    title: "Roles",
    body:
      "Each role includes the ones below it. Who holds which is configuration on the API -- ADMIN_EMAILS, OPERATOR_EMAILS, REVIEWER_EMAILS -- shown here as counts, never as names.",
  },
  "access.matrix": {
    title: "Effective access",
    body:
      "Every API route with the role its code enforces, read from the running service. A route marked Console is reachable from these screens; the rest need an API key.",
  },

  // Evaluation screen
  "evaluation.tuning": {
    title: "Before and after tuning",
    body:
      "The deterministic checks alone, on holdout cases that played no part in choosing the change. The false-alarm rate is the number the tuning was for; the recall it cost is shown beside it.",
  },
  "evaluation.full": {
    title: "Full pipeline (rules + HS + zero-day)",
    body:
      "Rules plus the HS classifier and the zero-day radar, behind the radar's real search gate. The fraud and compliance agents, investigation and the debate are not in this arm, and most zero-day searches in it were stubbed empty. The difference from the rules-alone figures is what those two checks contribute -- in both directions.",
  },
  "evaluation.hs": {
    title: "HS classifier",
    body:
      "Controlled goods declared under a benign heading, which rules cannot catch because the declared code is the evader's choice. The holdout substitutions appear nowhere in the material the method was developed on.",
  },
  "evaluation.workload": {
    title: "Workload at real fraud rates",
    body:
      "The test corpora are half (the public pairs) to over four-fifths (the synthetic holdouts) fraud by construction, so their precision does not describe a real queue. This reapplies each report's recall and false-alarm rate at 0.2%, 1% and 2% fraud. At 0.2%, close to the 0.18% Vietnam customs reported for 2024, the tuned rules hold about two hundred honest shipments for each real one, which is why the product's job is to make each hold quick to decide.",
  },
  "evaluation.curve": {
    title: "Rules at every threshold",
    body:
      "The same holdout cases re-scored at each review threshold that changes what is held. Each row down the table raises the threshold: fewer honest shipments held, and as many frauds missed or more. The held-per-real-case column is that trade in reviewer time at the measured fraud rate. Bracketed rates are 95% intervals; the held-per-real-case range puts both rates at their interval ends at once, which is not itself a 95% interval.",
    note:
      "A what-if over the rules' floors, not a setting. In the running system any non-zero floor already withholds automatic release, so moving along the curve would mean changing the code and the published boundary.",
  },
  "evaluation.models": {
    title: "Model choices, tested",
    body:
      "Two choices that were assumptions until they were measured. Fraud screening: on the corpus cases the rules score 0, where the model is the only check, did a larger model flag more of the frauds? Debate: on disputed cases, did the dearer model give a verdict more often, or tell fraud from honest trade better? Each rate shows its count and 95% interval; the samples are small, so read the intervals.",
    note:
      "A DISAGREE from the debate largely tracks the rules: the auditor is shown their findings and every honest case in the sample was one the rules had held. It routes a held case to an investigation and never clears one.",
  },
  "evaluation.public": {
    title: "Public enforcement cases",
    body:
      "Each case is a shipment as declared in a case a regulator made public, beside the honest trade it imitated. Separated means only the fraud was held. Both held usually means controlled goods, which want a licence check either way. Missed is mostly origin fraud, which is decided by where the inputs came from and is not on a booking.",
  },
};

// ---------------------------------------------------------------------------

/**
 * Every topic in one lookup, so a call site passes a single id.
 *
 * Prefixed namespaces rather than one flat object: a finding code and a panel id
 * could otherwise collide, and the collision would silently show the wrong
 * explanation, which is the failure mode this feature can least afford.
 */
export function helpFor(id: string): HelpEntry | undefined {
  if (id.startsWith("state:")) return STATE_HELP[id.slice(6)];
  if (id.startsWith("finding:")) return FINDING_HELP[id.slice(8)];
  if (id.startsWith("severity:")) return SEVERITY_HELP[id.slice(9)];
  if (id.startsWith("action:")) return REVIEWER_ACTION_HELP[id.slice(7)];
  if (id.startsWith("nav:")) return NAV_HELP[id.slice(4)];
  return PANEL_HELP[id];
}
