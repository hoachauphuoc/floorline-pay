"""
The pipeline gives the grounding check the case's validation (grounding plan GR4b, 7 Nov 2026).

A cost flag is ungrounded when the freight check ran clean on the booking (domestic lane, freight below the declared value) and stays grounded when
the charge is above the declared value or far above the lane figure. Nothing reads the labels to decide: shadow mode.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from vf_logistics import orchestrator
from vf_logistics import store as store_mod

BOOKING = {
    "shipment_id": "TEST-GROUNDING-COST-1",
    "origin": "ho chi minh city",
    "destination": "hanoi",
    "weight_kg": 100,
    "declared_value": 5000,
    "shipping_cost": 1500,
    "shipper_company": "Viet Tien Garment JSC",
    "shipper_tax_id": "0302887411",
    "shipper_country": "Vietnam",
    "receiver_company": "Hanoi Garment Retail Co",
    "receiver_country": "Vietnam",
    "cargo_description": "T-shirts, knitted, cotton",
    "hs_code": "6109.10",
}
FLAG = "Price manipulation: shipping cost of 1500 USD for 100kg from Ho Chi Minh City to Hanoi is unusually high"
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
FRAUD = {
    "result": {"risk_score": 78, "risk_level": "HIGH", "flags": [FLAG]},
    "model": "nano", "latency_ms": 800, "input_tokens": 900, "output_tokens": 200,
    "at": "2026-09-26T12:00:00+00:00", "parse_error": False, "prompt": "p", "raw": "{}",
}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def drive(shipment):
    store_mod._store = None
    orchestrator._invalidate_shipper_feedback()
    offline = RuntimeError("no model calls in this test")
    with patch.object(orchestrator, "analyze_shipment", AsyncMock(return_value=FRAUD)), \
         patch.object(orchestrator, "screen_shipment", AsyncMock(return_value=COMPLIANT)), \
         patch.object(orchestrator, "conduct_debate", AsyncMock(return_value=DEBATE)), \
         patch.object(orchestrator, "investigate_case", AsyncMock(return_value=INVESTIGATION)), \
         patch.object(orchestrator, "classify_hs", AsyncMock(side_effect=offline)), \
         patch.object(orchestrator, "screen_zero_day", AsyncMock(side_effect=offline)), \
         patch.object(orchestrator, "shipper_risk_adjustment", AsyncMock(return_value=0)), \
         patch.object(orchestrator.tavily_client, "search", AsyncMock(return_value=[])):
        case = _run(orchestrator.ingest_shipment(dict(shipment)))
        for _ in range(6):
            if case["state"] not in ("INGESTED", "SPECIALISTS_DONE", "INVESTIGATED"):
                break
            case = _run(orchestrator.advance(case))
    return case


class TestCostFlagInThePipeline(unittest.TestCase):
    def test_a_clean_domestic_booking_drops_the_cost_flag_to_ungrounded(self):
        case = drive(BOOKING)
        self.assertEqual([f["label"] for f in case["grounding"]["flags"]], ["ungrounded"])
        self.assertEqual(case["grounding"]["ungrounded"], 1)

    def test_a_charge_above_the_declared_value_keeps_the_flag_grounded(self):
        case = drive(dict(BOOKING, declared_value=500))
        self.assertEqual([f["label"] for f in case["grounding"]["flags"]], ["grounded"])

    def test_a_charge_far_above_the_lane_figure_keeps_the_flag_grounded(self):
        case = drive(dict(BOOKING, declared_value=100000, shipping_cost=30000))
        self.assertEqual([f["label"] for f in case["grounding"]["flags"]], ["grounded"])

    def test_the_labels_decide_nothing(self):
        case = drive(BOOKING)
        self.assertEqual(case["model_risk_score"], 78)
        self.assertGreaterEqual(case["risk_score"], 78)


if __name__ == "__main__":
    unittest.main()
