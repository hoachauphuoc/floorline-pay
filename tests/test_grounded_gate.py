"""
The agent gate and the grounded risk (grounding plan layer 3, 7 Nov 2026).

With the switch off (the default) the gate reads the case risk as always. With it on, a payment case whose model score no flag supports is read at
the deterministic floor, the case risk and the model score stay as they were, and the reason on the decision says the score was not counted. A
case with one grounded flag, with no flags, or that moves no money is read as before.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from vf_logistics import orchestrator
from vf_logistics import store as store_mod

BOOKING = {
    "shipment_id": "TEST-GATE-1",
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
PAYMENT = {
    "carrier_name": "Mekong Haulage Co", "payout_amount": 400.0, "currency": "USD",
    "paypal_authorization_id": "0VF52814937998046", "carrier_payee_email": "payments@mekong-haulage.example",
    "carrier_registered_payee_email": "payments@mekong-haulage.example",
}
ABSENT_ONLY = ["Missing route information", "Missing creation and status data"]
COMPLIANT = {
    "result": {"compliance_status": "CLEARED", "compliance_score": 90},
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
FINE = {
    "analyst": {"verdict": "normal", "confidence": 0.9, "reasons": [], "suggested_tier": "A"},
    "identity": {"status": "found", "reason": "1 result(s) name the carrier", "urls": []},
}


HS_CONSISTENT = {
    "result": {"consistent": True, "confidence": 0.9, "reasoning": "T-shirts fall under heading 6109"},
    "model": "nano", "latency_ms": 500, "input_tokens": 300, "output_tokens": 60,
    "at": "2026-09-26T12:00:02+00:00", "parse_error": False, "prompt": "p", "raw": "{}",
}


def fraud(flags):
    return {
        "result": {"risk_score": 78, "risk_level": "HIGH", "flags": flags},
        "model": "nano", "latency_ms": 800, "input_tokens": 900, "output_tokens": 200,
        "at": "2026-09-26T12:00:00+00:00", "parse_error": False, "prompt": "p", "raw": "{}",
    }


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def drive(shipment, flags, switch):
    store_mod._store = None
    orchestrator._invalidate_shipper_feedback()
    decided = []

    async def fake_decide(case, outcome, reason, *args, **kwargs):
        decided.append((outcome, reason))
        case["state"] = outcome
        return case

    async def fake_send(case, limit):
        return case

    offline = RuntimeError("no model calls in this test")
    with patch.object(orchestrator, "AGENT_GROUNDED_GATE", switch), \
         patch.object(orchestrator, "analyze_shipment", AsyncMock(return_value=fraud(flags))), \
         patch.object(orchestrator, "screen_shipment", AsyncMock(return_value=COMPLIANT)), \
         patch.object(orchestrator, "conduct_debate", AsyncMock(return_value=DEBATE)), \
         patch.object(orchestrator, "investigate_case", AsyncMock(return_value=INVESTIGATION)), \
         patch.object(orchestrator, "classify_hs", AsyncMock(return_value=HS_CONSISTENT)), \
         patch.object(orchestrator.carrier_registry, "enrich", AsyncMock(return_value=None)), \
         patch.object(orchestrator, "screen_zero_day", AsyncMock(side_effect=offline)), \
         patch.object(orchestrator, "shipper_risk_adjustment", AsyncMock(return_value=0)), \
         patch.object(orchestrator, "_decide", fake_decide), \
         patch.object(orchestrator, "_send_agent_decision", fake_send), \
         patch.object(orchestrator.agent_payment, "review", AsyncMock(return_value=({"limit_usd": 500}, []))), \
         patch.object(orchestrator.payment_ai, "assess", AsyncMock(return_value=FINE)), \
         patch.object(orchestrator.tavily_client, "search", AsyncMock(return_value=[])):
        case = _run(orchestrator.ingest_shipment(dict(shipment)))
        for _ in range(6):
            if case["state"] not in ("INGESTED", "SPECIALISTS_DONE", "INVESTIGATED"):
                break
            case = _run(orchestrator.advance(case))
    return case, decided


class TestTheSwitch(unittest.TestCase):
    def test_it_is_off_by_default(self):
        self.assertIs(orchestrator.AGENT_GROUNDED_GATE, False)

    def test_with_it_off_a_high_model_score_is_not_cleared_even_when_no_flag_is_grounded(self):
        case, decided = drive({**BOOKING, **PAYMENT}, ABSENT_ONLY, False)
        self.assertNotIn("AUTO_CLEARED", [outcome for outcome, _ in decided])
        self.assertNotIn("gate_risk", case)


class TestWithTheSwitchOn(unittest.TestCase):
    def test_a_payment_case_whose_flags_are_all_absence_is_read_at_the_floor_and_cleared(self):
        case, decided = drive({**BOOKING, **PAYMENT}, ABSENT_ONLY, True)
        self.assertIn("AUTO_CLEARED", [outcome for outcome, _ in decided])
        reason = [r for outcome, r in decided if outcome == "AUTO_CLEARED"][0]
        self.assertIn("not counted", reason)
        self.assertEqual(case["gate_risk"], 0)

    def test_the_case_risk_and_the_model_score_are_not_lowered(self):
        case, _ = drive({**BOOKING, **PAYMENT}, ABSENT_ONLY, True)
        self.assertEqual(case["risk_score"], 78)
        self.assertEqual(case["model_risk_score"], 78)

    def test_one_grounded_flag_keeps_the_old_gate(self):
        flags = ABSENT_ONLY + ["Weight of 100 kg looks wrong for this cargo"]
        case, decided = drive({**BOOKING, **PAYMENT}, flags, True)
        self.assertNotIn("AUTO_CLEARED", [outcome for outcome, _ in decided])
        self.assertNotIn("gate_risk", case)

    def test_a_score_with_no_flags_keeps_the_old_gate(self):
        case, decided = drive({**BOOKING, **PAYMENT}, [], True)
        self.assertNotIn("AUTO_CLEARED", [outcome for outcome, _ in decided])
        self.assertNotIn("gate_risk", case)

    def test_a_shipment_that_moves_no_money_keeps_the_old_gate(self):
        case, decided = drive(BOOKING, ABSENT_ONLY, True)
        self.assertNotIn("AUTO_CLEARED", [outcome for outcome, _ in decided])
        self.assertNotIn("gate_risk", case)


if __name__ == "__main__":
    unittest.main()
