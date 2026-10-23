"""
The payment AI layers in the analysis pipeline (orchestrator.advance with payment_ai.assess).

Pinned here: a payment case is assessed once, the answer is stored on the case for the settler
and reaches the findings and the floor; a shipment that moves no money is not assessed and has
no payment_ai on its case; and an answer that says "normal" and "found" changes neither the
findings nor the floor.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from vf_logistics import orchestrator
from vf_logistics import store as store_mod

SHIPMENT = {
    "shipment_id": "TEST-PAYAI-1",
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
PAYMENT = {
    "carrier_name": "Mekong Haulage Co", "payout_amount": 2000.0, "currency": "USD",
    "paypal_authorization_id": "0VF52814937998046", "carrier_payee_email": "payments@mekong-haulage.example",
    "carrier_registered_payee_email": "payments@mekong-haulage.example",
}
FRAUD_LOW = {
    "result": {"risk_score": 20, "risk_level": "LOW"},
    "model": "nano", "latency_ms": 800, "input_tokens": 900, "output_tokens": 200,
    "at": "2026-09-26T12:00:00+00:00", "parse_error": False, "prompt": "p", "raw": "{}",
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
ELEVATED = {
    "analyst": {"verdict": "elevated", "confidence": 0.9, "reasons": ["payee changed days before the payout"],
                "suggested_tier": "C"},
    "identity": {"status": "not_found", "reason": "no search result names the carrier", "urls": []},
}
FINE = {
    "analyst": {"verdict": "normal", "confidence": 0.9, "reasons": [], "suggested_tier": "A"},
    "identity": {"status": "found", "reason": "1 result(s) name the carrier", "urls": []},
}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def drive(shipment, answer):
    store_mod._store = None
    orchestrator._invalidate_shipper_feedback()
    assess = AsyncMock(return_value=answer)
    offline = RuntimeError("no model calls in this test")
    with patch.object(orchestrator, "analyze_shipment", AsyncMock(return_value=FRAUD_LOW)), \
         patch.object(orchestrator, "screen_shipment", AsyncMock(return_value=COMPLIANT)), \
         patch.object(orchestrator, "conduct_debate", AsyncMock(return_value=DEBATE)),          patch.object(orchestrator, "investigate_case", AsyncMock(return_value=INVESTIGATION)),          patch.object(orchestrator, "classify_hs", AsyncMock(side_effect=offline)), \
         patch.object(orchestrator, "screen_zero_day", AsyncMock(side_effect=offline)), \
         patch.object(orchestrator, "shipper_risk_adjustment", AsyncMock(return_value=0)), \
         patch.object(orchestrator.payment_ai, "assess", assess), \
         patch.object(orchestrator.tavily_client, "search", AsyncMock(return_value=[])):
        case = _run(orchestrator.ingest_shipment(dict(shipment)))
        for _ in range(6):
            if case["state"] not in ("INGESTED", "SPECIALISTS_DONE", "INVESTIGATED"):
                break
            case = _run(orchestrator.advance(case))
    return case, assess


def codes(case):
    return [f["code"] for f in case["validation"]["findings"]]


class TestThePaymentLayersInThePipeline(unittest.TestCase):
    def test_a_payment_case_is_assessed_once_and_the_answer_is_kept_on_the_case(self):
        case, assess = drive({**SHIPMENT, **PAYMENT}, ELEVATED)
        assess.assert_awaited_once()
        self.assertEqual(assess.await_args.args[0]["carrier_name"], "Mekong Haulage Co")
        self.assertEqual(case["payment_ai"], ELEVATED)

    def test_the_answer_reaches_the_findings_and_the_floor(self):
        case, _ = drive({**SHIPMENT, **PAYMENT}, ELEVATED)
        self.assertIn("PAYMENT_AI_ELEVATED", codes(case))
        self.assertIn("CARRIER_NOT_FOUND_ONLINE", codes(case))
        self.assertGreaterEqual(case["validation"]["risk_floor"], 65)
        self.assertTrue(case["validation"]["payment_ai_checked"])

    def test_a_fine_answer_adds_no_finding_and_leaves_the_floor_alone(self):
        without, _ = drive({**SHIPMENT, **PAYMENT}, {"analyst": {"verdict": "unknown"}, "identity": {"status": "found"}})
        case, _ = drive({**SHIPMENT, **PAYMENT}, FINE)
        self.assertEqual(codes(case), codes(without))
        self.assertEqual(case["validation"]["risk_floor"], without["validation"]["risk_floor"])

    def test_a_shipment_that_moves_no_money_is_not_assessed(self):
        case, assess = drive(SHIPMENT, ELEVATED)
        assess.assert_not_called()
        self.assertNotIn("payment_ai", case)
        self.assertFalse(case["validation"]["payment_ai_checked"])
        self.assertNotIn("PAYMENT_AI_ELEVATED", codes(case))


if __name__ == "__main__":
    unittest.main()
