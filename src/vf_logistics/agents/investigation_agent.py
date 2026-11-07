"""
VF Logistics AI Investigation Agent - NVIDIA Nemotron 3 Super, via Nebius Token Factory
Conducts deep-dive investigations into flagged cases using multi-step reasoning.

This agent uses Nemotron 3 Super for cost-efficient synthesis of findings the
fraud and compliance agents already produced, while those two frequent, primary-
judgment agents run on the smaller Nemotron 3 Nano. Set INVESTIGATION_MODEL to
`nvidia/Nemotron-3-Ultra-550b-a55b` for stronger reasoning if credit allows.

Track: Best Apps and Agents
Hackathon: Nebius x NVIDIA Global AI Hackathon
"""

import json
import logging
import os
from typing import Any

from vf_logistics import nebius_client, tavily_client
from ._common import Timer, envelope, parse_model_json, validate_result

log = logging.getLogger(__name__)

# Output ceiling. 4,500 against a measured legitimate maximum of 1,387 output
# tokens, median 900.
#
# This agent produced the clearest runaway on the 20-case run: 8,192 output tokens in
# a degenerate loop emitting empty objects, 38.6 seconds, $0.007873 for a call that
# returned nothing usable -- 29% of all investigation spend on that run.
MAX_OUTPUT_TOKENS = 4500

MODEL_ID = os.getenv("INVESTIGATION_MODEL", "nvidia/nemotron-3-super-120b-a12b")

# Off: Super is asked for JSON by the prompt and parsed by parse_model_json, not
# constrained by response_format. With json_object on, its reasoning leaks into the
# constrained output -- the reply opens a key and the planning lands inside it.
# Measured 2026-10-10 on two stored board prompts, 12 calls per mode: json_object
# with reasoning 6/12 usable reports (`{": {}}": null}`, `{".json": null}`, "We
# must output JSON only..." as a key); no response_format 12/12; json_object with
# reasoning disabled 12/12. Reasoning is why this agent runs on Super, so the
# constraint goes and the reasoning stays.
JSON_MODE = False

# One more call when a reply still fails InvestigationReport. Bounded at two: the
# case is already going to a human, so a second failure is recorded and counted
# rather than chased. Tokens of every attempt are billed to the step.
MAX_ATTEMPTS = 2

def get_model_id() -> str:
    """Investigation agent is pinned via INVESTIGATION_MODEL, not the shared registry."""
    return MODEL_ID

INVESTIGATION_PROMPT = """
You are a senior fraud investigator AI for VF Logistics. Your role is to conduct 
thorough investigations of flagged shipments and entities.

INVESTIGATION METHODOLOGY:
1. EVIDENCE GATHERING
   - Collect all related shipments and transactions
   - Identify connected parties and relationships
   - Map timeline of suspicious activities

2. PATTERN ANALYSIS
   - Look for recurring fraud patterns
   - Identify network of potentially colluding parties
   - Compare against known fraud typologies

3. RISK ASSESSMENT
   - Calculate total exposure and potential losses
   - Assess likelihood of organized fraud vs isolated incident
   - Evaluate systemic vulnerabilities exploited

4. RECOMMENDATION ENGINE
   - Prioritize investigation actions
   - Suggest preventive controls
   - Recommend escalation if warranted

OUTPUT FORMAT (JSON):
- investigation_id: unique ID for this investigation
- summary: executive summary of findings
- evidence: key evidence items discovered
- connections: related entities and shipments
- fraud_pattern: identified fraud type if any
- exposure_estimate: potential financial impact
- confidence_level: investigation confidence (0-1)
- recommended_actions: prioritized next steps
- escalation_required: boolean with reason if true

All monetary amounts in the input are USD. Express exposure_estimate in USD,
leading with a figure (for example "USD 164,000 cargo value plus penalties").
"""


async def investigate_case(case_data: dict[str, Any]) -> dict[str, Any]:
    """
    Conduct a deep investigation of a flagged case.
    """
    case_text = f"""
    CASE ID: {case_data.get('case_id', 'N/A')}
    TRIGGER: {case_data.get('trigger_reason', 'N/A')}
    INITIAL RISK SCORE: {case_data.get('risk_score', 'N/A')}
    
    PRIMARY SHIPMENT:
    {_format_shipment(case_data.get('primary_shipment', {}))}
    
    RELATED SHIPMENTS:
    {_format_related_shipments(case_data.get('related_shipments', []))}
    
    ENTITY PROFILE (Primary):
    {_format_entity(case_data.get('primary_entity', {}))}
    
    HISTORICAL ALERTS:
    {_format_alerts(case_data.get('historical_alerts', []))}
    
    FINANCIAL SUMMARY:
    - Total Transaction Volume: {case_data.get('total_volume', 'N/A')} USD
    - Average Transaction: {case_data.get('avg_transaction', 'N/A')} USD
    - Anomaly Count (30d): {case_data.get('anomaly_count_30d', 'N/A')}
    """
    
    # Tavily enrichment: search for the specific fraud pattern and counterparties
    tavily_context = ""
    shipment = case_data.get("primary_shipment", {})
    shipper = shipment.get("shipper_name") or shipment.get("shipper_company") or ""
    receiver = shipment.get("receiver_name") or shipment.get("receiver_company") or shipment.get("consignee", "")
    trigger = case_data.get("trigger_reason", "")

    tavily_queries = []
    if trigger:
        tavily_queries.append(f'"{trigger}" logistics fraud pattern')
    if shipper and receiver:
        tavily_queries.append(f'"{shipper}" "{receiver}" business relationship OR sanctions')
    elif shipper:
        tavily_queries.append(f'"{shipper}" fraud OR sanctions OR shell company')

    all_results = []
    statuses: list[str] = []
    for q in tavily_queries:
        results, status = await tavily_client.search_with_status(q, max_results=3)
        all_results.extend(results)
        statuses.append(status)
    failed = next((s for s in statuses if s in tavily_client.FAILED_STATUSES), None)
    search_status = failed or (statuses[0] if statuses else tavily_client.OK)
    if all_results:
        tavily_context = (
            "\n\n<<<BEGIN EXTERNAL WEB SEARCH FINDINGS (Tavily)>>>\n"
            # Status carried through for the same reason it now is in
            # compliance_agent: format_findings() defaults to OK, so a timeout or a
            # 429 rendered as "search ran and returned nothing" -- an outage read as
            # a clean result.
            + tavily_client.format_findings(all_results, search_status)
            + "\n<<<END EXTERNAL WEB SEARCH FINDINGS>>>\n"
        )

    user_text = f"Conduct a thorough investigation:\n{case_text}{tavily_context}"
    input_tokens = output_tokens = attempts = 0
    with Timer() as timer:
        while True:
            attempts += 1
            text, call_in, call_out = await nebius_client.complete_json(
                model=get_model_id(),
                system_prompt=INVESTIGATION_PROMPT,
                user_text=user_text,
                temperature=0.2,
                max_tokens=MAX_OUTPUT_TOKENS,
                json_mode=JSON_MODE,
            )
            input_tokens += call_in
            output_tokens += call_out
            parsed, error = parse_model_json(text)
            usable = error is None and validate_result("investigation", parsed)[1] is None
            if usable or attempts >= MAX_ATTEMPTS:
                break
            log.warning(
                "investigation reply for %s unusable on attempt %d, asking again: %r",
                case_data.get("case_id"), attempts, (text or "")[:120],
            )

    out = envelope(
        agent="investigation",
        model=get_model_id(),
        result=parsed,
        error=error,
        raw=text,
        latency_ms=timer.ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        legacy_key="investigation_result",
        prompt=user_text,
        case_id=case_data.get("case_id"),
    )
    out["thinking_enabled"] = True
    out["attempts"] = attempts
    out["external_search_used"] = len(all_results) > 0
    # A LIST of {title, url}, matching compliance_agent.
    #
    # This was `len(all_results)`, an int, under the same field name the compliance
    # step fills with a list -- and the case trace renders that field by mapping over
    # it, so `(3).length` was undefined, the branch was skipped, and an investigation's
    # search evidence silently never appeared. Two agents disagreeing about the type of
    # one field is the kind of defect that shows up as an empty panel rather than an
    # error.
    out["external_search_results"] = [
        {"title": r["title"], "url": r["url"]} for r in all_results[:5]
    ]
    out["external_search_status"] = search_status
    out["external_search_count"] = len(tavily_queries)
    # Not cached: this agent runs only on escalated cases, where the evidence behind a
    # decision that is already going to a human should be fetched live.
    out["external_search_cache_hits"] = 0
    out["external_search_live"] = len(tavily_queries)
    return out


def _format_shipment(shipment: dict) -> str:
    if not isinstance(shipment, dict) or not shipment:
        return "No data"
    return f"""
    - ID: {shipment.get('shipment_id', 'N/A')}
    - Route: {shipment.get('origin', 'N/A')} ? {shipment.get('destination', 'N/A')}
    - Value: {shipment.get('declared_value', 'N/A')} USD
    - Cost: {shipment.get('shipping_cost', 'N/A')} USD
    - Shipper: {shipment.get('shipper_name') or shipment.get('shipper_company') or 'N/A'}
    - Status: {shipment.get('status', 'N/A')}
    """


def _format_related_shipments(shipments: list) -> str:
    if not shipments:
        return "None found"
    lines = []
    for s in shipments[:10]:  # Limit to 10
        if isinstance(s, dict):
            lines.append(
                f"  - {s.get('shipment_id')}: {s.get('origin')} ? {s.get('destination')} "
                f"({s.get('declared_value')} USD)"
            )
        else:
            lines.append(f"  - {s}")
    return "\n".join(lines)


def _format_entity(entity: dict) -> str:
    if not isinstance(entity, dict) or not entity:
        return "No data"
    return f"""
    - Name: {entity.get('name') or entity.get('company') or 'N/A'}
    - Company: {entity.get('company', 'N/A')}
    - Tax ID: {entity.get('tax_id', 'N/A')}
    - Country: {entity.get('country', 'N/A')}
    - Registration Date: {entity.get('registration_date', 'N/A')}
    - Total Transactions: {entity.get('transaction_count', 'N/A')}
    - Risk Rating: {entity.get('risk_rating', 'N/A')}
    - Previous Flags: {entity.get('previous_flags', 'N/A')}
    """


def _format_alerts(alerts: list) -> str:
    """
    Render prior alerts.

    Upstream agents return these as either structured objects or plain strings
    depending on the finding, so both shapes are accepted rather than assumed.
    """
    if not alerts:
        return "No previous alerts"
    lines = []
    for a in alerts[:5]:
        if isinstance(a, dict):
            lines.append(
                f"  - [{a.get('date', 'undated')}] {a.get('type', 'alert')}: "
                f"{a.get('description', '')}"
            )
        else:
            lines.append(f"  - {a}")
    return "\n".join(lines)


async def generate_report(investigation_results: list[dict]) -> dict[str, Any]:
    """
    Generate a consolidated investigation report from multiple case results.
    """
    report_prompt = """
    Based on the investigation results provided, generate a consolidated report with:
    1. Executive Summary
    2. Key Findings
    3. Common Patterns
    4. Total Risk Exposure
    5. Priority Recommendations
    6. Systemic Issues Identified
    
    Output as structured JSON.
    """
    
    results_text = "\n\n".join([
        f"Case {r.get('case_id')}: "
        f"{json.dumps(r.get('result') or r.get('investigation_result') or {}, ensure_ascii=False)}"
        for r in investigation_results
    ])

    with Timer() as timer:
        text, input_tokens, output_tokens = await nebius_client.complete_json(
            model=get_model_id(),
            system_prompt=report_prompt,
            user_text=f"Generate report from these investigations:\n{results_text}",
            temperature=0.2,
            max_tokens=MAX_OUTPUT_TOKENS,
            json_mode=JSON_MODE,
        )

    parsed, error = parse_model_json(text)
    out = envelope(
        agent="investigation_report",
        model=get_model_id(),
        result=parsed,
        error=error,
        raw=text,
        latency_ms=timer.ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        legacy_key="report",
    )
    out["cases_analyzed"] = len(investigation_results)
    return out


def get_agent_info() -> dict[str, Any]:
    """Return agent metadata."""
    return {
        "name": "VF Logistics AI Investigation Agent",
        "version": "1.0.0",
        "model": get_model_id(),
        "capabilities": [
            "deep_case_investigation",
            "pattern_analysis",
            "network_mapping",
            "risk_assessment",
            "report_generation",
            "extended_thinking"
        ]
    }
