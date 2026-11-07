"""
The boundary check and the grounded gate risk (grounding plan layer 3, 7 Nov 2026).

The ceiling a person published (max_effective_risk) is compared with case["gate_risk"] when the orchestrator set one for a payment case whose model
score no flag supports, and with the risk score otherwise. A bad gate_risk falls back to the risk score. Findings, the value ceiling and the human
triggers are checked exactly as before.
"""

from __future__ import annotations

from typing import Any

from vf_logistics.governance import check


def boundary() -> dict[str, Any]:
    return {
        "boundary_id": "BOUNDARY-v3",
        "version": 3,
        "status": "ACTIVE",
        "permissions": {
            "allowed_actions": ["hold_shipment", "assign_analyst", "publish_decision"],
            "auto_release": {
                "permitted": True,
                "max_declared_value_usd": 5000,
                "max_effective_risk": 39,
                "require_zero_deterministic_findings": True,
                "forbidden_hs_prefixes": [],
                "forbidden_destinations": [],
            },
            "require_human_when": ["score_disputed"],
        },
    }


def case(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "case_id": "CASE-GATE-1",
        "state": "SPECIALISTS_DONE",
        "risk_score": 78,
        "gate_risk": 0,
        "compliance_status": "CLEARED",
        "shipment": {"declared_value": 5000, "hs_code": "6109.10", "destination": "hanoi"},
        "validation": {"findings": [], "finding_count": 0},
        "reconciliation": {"auto_clear_permitted": True, "risk_floor": 0, "score_disputed": False},
        "input_security": {"injection_screening": {"blocked": False}, "forbidden_fields_attempted": []},
    }
    base.update(changes)
    return base


def release(c: dict[str, Any]) -> dict[str, Any]:
    return check("release_shipment", c, boundary())


class TestTheGateRiskIsRead:
    def test_a_valid_gate_risk_under_the_ceiling_allows_the_release(self):
        assert release(case())["allowed"] is True

    def test_a_gate_risk_at_the_ceiling_allows_and_one_above_it_is_denied(self):
        assert release(case(gate_risk=39))["allowed"] is True
        denied = release(case(gate_risk=40))
        assert denied["allowed"] is False
        assert "exceeds delegated ceiling" in denied["reason"]

    def test_without_a_gate_risk_the_risk_score_is_read_as_before(self):
        c = case()
        del c["gate_risk"]
        denied = release(c)
        assert denied["allowed"] is False
        assert "effective risk 78 exceeds delegated ceiling 39" in denied["reason"]


class TestABadGateRiskIsIgnored:
    def test_a_gate_risk_below_the_deterministic_floor_is_ignored(self):
        c = case(gate_risk=10, reconciliation={"auto_clear_permitted": True, "risk_floor": 30, "score_disputed": False})
        assert release(c)["allowed"] is False

    def test_a_gate_risk_above_the_risk_score_is_ignored(self):
        denied = release(case(gate_risk=90))
        assert denied["allowed"] is False
        assert "effective risk 78 exceeds delegated ceiling 39" in denied["reason"]

    def test_a_gate_risk_that_is_not_a_number_is_ignored(self):
        for value in ("0", True, None, [0], float("nan")):
            assert release(case(gate_risk=value))["allowed"] is False, value

    def test_a_missing_floor_makes_the_gate_risk_unreadable(self):
        c = case(reconciliation={"auto_clear_permitted": True, "score_disputed": False})
        assert release(c)["allowed"] is False


class TestOtherConditionsAreUnchanged:
    def test_a_deterministic_finding_still_denies(self):
        denied = release(case(validation={"findings": [{"code": "X"}], "finding_count": 1}))
        assert denied["allowed"] is False
        assert "deterministic finding" in denied["reason"]

    def test_a_value_over_the_ceiling_still_denies(self):
        denied = release(case(shipment={"declared_value": 9000, "hs_code": "6109.10", "destination": "hanoi"}))
        assert denied["allowed"] is False
        assert "exceeds delegated" in denied["reason"]

    def test_a_disputed_score_still_needs_a_person(self):
        c = case(reconciliation={"auto_clear_permitted": True, "risk_floor": 0, "score_disputed": True})
        denied = release(c)
        assert denied["allowed"] is False
        assert "requires human review" in denied["reason"]

    def test_a_deterministic_veto_of_auto_clear_still_denies(self):
        c = case(reconciliation={"auto_clear_permitted": False, "risk_floor": 0, "score_disputed": False})
        assert release(c)["allowed"] is False
