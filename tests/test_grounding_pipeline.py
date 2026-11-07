"""
The analysis pipeline keeps the grounding labels of the fraud flags on the case (grounding plan layer 2, GR4, 7 Nov 2026).

Shadow mode: the labels are stored and shown, nothing reads them to decide. The risk, the reconciliation and the state of a case must be the
same whether or not the model raised flags, and a flag list of the wrong type must not stop the analysis.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from vf_logistics import orchestrator
from vf_logistics import store as store_mod

SHIPMENT = {
    "shipment_id": "TEST-GROUNDING-1",
    "origin": "Ho Chi Minh City, Vietnam",
    "destination": "Rotterdam, Netherlands",
    "weight_kg": 1200,
    "declared_value": 40000,
    "shipping_cost": 2600,
    "avg_route_cost": 2500,
    "shipper_name": "Lotus Garment Works",
    "shipper_company": "Lotus Garment Works JSC",
    "shipper_country": "Vietnam",
    "shipper_tax_id": "0312345678",
    "receiver_name": "Procurement",
    "receiver_company": "Noordzee Textiel BV",
    "receiver_country": "Netherlands",
    "shipper_tx_count": 1,
    "cargo_description": "Cotton T-shirts, 4000 units",
    "hs_code": "6109.10",
    "transit_points": "none",
    "route_details": "direct sailing",
}
COMPLIANT = {
    "result": {"compliance_status": "COMPLIANT", "compliance_score": 90},
    "model": "nano", "latency_ms": 700, "input_tokens": 800, "output_tokens": 150,
    "at": "2026-09-26T12:00:01+00:00", "parse_error": False, "prompt": "p", "raw": "{}",
}
DEBATE = {
    "agent": "debate", "model": "super", "latency_ms": 1000, "input_tokens": 500, "output_tokens": 100,
    "at": "2026-09-26T12:00:05+00:00", "parse_error": False, "prompt": "p", "raw": "{}",
    "result": {"verdict": {"verdict": "CONFIRM", "confidence": 0.9, "forced": False}, "debate_trace": [],
               "rounds_used": 1},
}
INVESTIGATION = {
    "result": {"summary": "Nothing further.", "fraud_pattern": "none", "exposure_estimate": "0"},
    "model": "super", "latency_ms": 3000, "input_tokens": 2000, "output_tokens": 400,
    "at": "2026-09-26T12:00:09+00:00", "parse_error": False, "prompt": "p", "raw": "{}",
}


def fraud(**extra):
    result = {"risk_score": 20, "risk_level": "LOW", **extra}
    return {
        "result": result, "model": "nano", "latency_ms": 800, "input_tokens": 900, "output_tokens": 200,
        "at": "2026-09-26T12:00:00+00:00", "parse_error": False, "prompt": "p", "raw": "{}",
    }


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def drive(fraud_response):
    store_mod._store = None
    orchestrator._invalidate_shipper_feedback()
    offline = RuntimeError("no model calls in this test")
    with patch.object(orchestrator, "analyze_shipment", AsyncMock(return_value=fraud_response)), \
         patch.object(orchestrator, "screen_shipment", AsyncMock(return_value=COMPLIANT)), \
         patch.object(orchestrator, "conduct_debate", AsyncMock(return_value=DEBATE)), \
         patch.object(orchestrator, "investigate_case", AsyncMock(return_value=INVESTIGATION)), \
         patch.object(orchestrator, "classify_hs", AsyncMock(side_effect=offline)), \
         patch.object(orchestrator, "screen_zero_day", AsyncMock(side_effect=offline)), \
         patch.object(orchestrator, "shipper_risk_adjustment", AsyncMock(return_value=0)), \
         patch.object(orchestrator.tavily_client, "search", AsyncMock(return_value=[])):
        case = _run(orchestrator.ingest_shipment(dict(SHIPMENT)))
        for _ in range(6):
            if case["state"] not in ("INGESTED", "SPECIALISTS_DONE", "INVESTIGATED"):
                break
            case = _run(orchestrator.advance(case))
    return case


class TestGroundingOnTheCase(unittest.TestCase):
    def test_each_fraud_flag_is_labelled_against_the_record_and_kept_on_the_case(self):
        flags = ["Route information is missing", "Price manipulation: cost unusually high"]
        case = drive(fraud(flags=flags))
        grounding = case["grounding"]
        self.assertEqual([item["label"] for item in grounding["flags"]], ["ungrounded", "grounded"])
        self.assertEqual((grounding["grounded"], grounding["ungrounded"], grounding["absence"]), (1, 1, 0))

    def test_a_result_without_flags_keeps_an_empty_grounding(self):
        case = drive(fraud())
        self.assertEqual(case["grounding"]["flags"], [])
        self.assertEqual(
            (case["grounding"]["grounded"], case["grounding"]["ungrounded"], case["grounding"]["absence"]), (0, 0, 0)
        )

    def test_a_flag_list_of_the_wrong_type_does_not_stop_the_analysis(self):
        case = drive(fraud(flags="Route information is missing"))
        self.assertEqual(case["grounding"]["flags"], [])
        self.assertNotEqual(case["state"], "DEAD_LETTER")


class TestGroundingDecidesNothing(unittest.TestCase):
    def test_the_risk_the_reconciliation_and_the_state_are_the_same_with_or_without_flags(self):
        plain = drive(fraud())
        flagged = drive(fraud(flags=["Route information is missing", "Price manipulation: cost unusually high"]))
        self.assertEqual(flagged["risk_score"], plain["risk_score"])
        self.assertEqual(flagged["reconciliation"], plain["reconciliation"])
        self.assertEqual(flagged["state"], plain["state"])


if __name__ == "__main__":
    unittest.main()
