"""
When the agent may pay a clean case by itself (agent_payment.py).

Pinned here: with every condition met there is no refusal and the limit comes back; each condition missing, unreadable or wrong gives a
refusal that names it; a model's answer can only refuse, because the standing it suggests is applied through credit_profile and a
"normal" allows nothing without the person's limit; a record that cannot be read is a refusal, never a pass; and neither this module
nor the settler imports a model client.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import pathlib
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

ADMIN = "admin@forwarder.example"
PAYEE = "payments@mekong-haulage.example"
LIMIT = {"limit_usd": Decimal("1000"), "published_by": ADMIN, "reason": "first month", "at": "2026-11-06T00:00:00+00:00"}
STANDING = {"tier": "A", "baseline": "A", "suggested": "A", "cap_usd": Decimal("5000"), "paid_count": 3, "frozen": False}
SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "vf_logistics"


@pytest.fixture(autouse=True)
def the_default_threshold(monkeypatch):
    monkeypatch.delenv("PAYMENT_DUAL_APPROVAL_USD", raising=False)


def agent():
    return importlib.import_module("vf_logistics.agent_payment")


def clean_case():
    return {
        "case_id": "CASE-AGENT-1",
        "state": "AUTO_CLEARED",
        "validation": {"findings": [], "finding_count": 0},
        "reconciliation": {"auto_clear_permitted": True},
        "payment_ai": {
            "analyst": {"verdict": "normal", "confidence": 0.9, "reasons": [], "suggested_tier": "A"},
            "identity": {"status": "found", "reason": "1 result(s) name the carrier", "urls": [], "payee_site": "on_site"},
        },
    }


def shipment(**changes):
    return {
        "carrier_name": "Mekong Haulage Co", "currency": "USD", "payout_amount": 900.0,
        "paypal_authorization_id": "AUTH-1", "carrier_payee_email": PAYEE,
        "carrier_registered_payee_email": PAYEE, **changes,
    }


def review(case=None, ship=None, limit=LIMIT, standing=STANDING):
    module = agent()
    with patch.object(module.payment_controls, "agent_limit", AsyncMock(return_value=limit)), \
            patch.object(module.credit_profile, "profile", AsyncMock(return_value=standing)):
        return asyncio.run(module.review(
            clean_case() if case is None else case, shipment() if ship is None else ship, "tenant-a",
        ))


def refused(phrase, **kwargs):
    found = review(**kwargs)[1]
    assert any(phrase in reason for reason in found), found


def test_a_clean_case_below_the_limit_may_be_paid():
    assert review() == (LIMIT, [])


def test_with_no_limit_published_the_agent_pays_nothing():
    assert review(limit=None) == (None, ["no person has published an agent limit"])


def test_the_key_of_the_agents_decision_is_fixed_per_case():
    assert agent().key_for("CASE-1") == "agent-CASE-1"


CASE_CHANGES = [
    (lambda c: c.pop("payment_ai"), "no payment analysis"),
    (lambda c: c.update(payment_ai="not a reading"), "no payment analysis"),
    (lambda c: c["payment_ai"].pop("analyst"), "the analyst did not read"),
    (lambda c: c["payment_ai"]["analyst"].update(verdict="unknown"), "the analyst did not read"),
    (lambda c: c["payment_ai"]["analyst"].update(verdict="elevated"), "the analyst did not read"),
    (lambda c: c["payment_ai"]["analyst"].update(confidence=0.3), "the analyst did not read"),
    (lambda c: c["payment_ai"]["analyst"].update(confidence="high"), "the analyst did not read"),
    (lambda c: c["payment_ai"]["analyst"].update(confidence=True), "the analyst did not read"),
    (lambda c: c["payment_ai"]["analyst"].update(confidence=float("nan")), "the analyst did not read"),
    (lambda c: c["payment_ai"].pop("identity"), "the web check did not find"),
    (lambda c: c["payment_ai"]["identity"].update(status="unverified"), "the web check did not find"),
    (lambda c: c["payment_ai"]["identity"].update(status="not_found"), "the web check did not find"),
    (lambda c: c.pop("validation"), "findings"),
    (lambda c: c["validation"].update(findings=None), "findings"),
    (lambda c: c["validation"].update(findings=[{"code": "PAYEE_UNVERIFIED"}]), "findings"),
    (lambda c: c.pop("reconciliation"), "deterministic floor"),
    (lambda c: c["reconciliation"].update(auto_clear_permitted="true"), "deterministic floor"),
    (lambda c: c["reconciliation"].update(auto_clear_permitted=False), "deterministic floor"),
]


@pytest.mark.parametrize("change,phrase", CASE_CHANGES)
def test_a_missing_or_wrong_record_on_the_case_is_a_refusal(change, phrase):
    case = clean_case()
    change(case)
    refused(phrase, case=case)


@pytest.mark.parametrize("tier", ["B", "C", "D", None])
def test_a_carrier_below_standing_a_is_refused(tier):
    refused(f"standing is {tier}", standing={**STANDING, "tier": tier})


def test_the_standing_is_read_with_what_the_analyst_suggested():
    module = agent()
    profile = AsyncMock(return_value=STANDING)
    case = clean_case()
    with patch.object(module.payment_controls, "agent_limit", AsyncMock(return_value=LIMIT)), \
            patch.object(module.credit_profile, "profile", profile):
        asyncio.run(module.review(case, shipment(), "tenant-a"))
    profile.assert_awaited_once_with("Mekong Haulage Co", case["payment_ai"], "tenant-a")


@pytest.mark.parametrize("amount", [1000.0, 1000.01, "1500", 5000])
def test_a_payout_not_below_the_limit_is_refused(amount):
    refused("not below the agent limit", ship=shipment(payout_amount=amount))


@pytest.mark.parametrize("amount", [None, "", "abc", -5, 0, float("nan"), float("inf")])
def test_a_payout_that_cannot_be_read_is_refused(amount):
    refused("payout amount cannot be read", ship=shipment(payout_amount=amount))


def test_a_payout_at_the_second_approval_threshold_is_refused_whatever_the_limit():
    refused("second approver", ship=shipment(payout_amount=2500.0), limit={**LIMIT, "limit_usd": Decimal("9000")})


def test_a_payout_in_another_currency_is_refused():
    refused("in USD", ship=shipment(currency="EUR"))


def test_a_payee_on_the_carriers_website_passes_even_after_a_change():
    ship = shipment(carrier_payee_changed_at="2026-01-01T00:00:00+00:00")
    assert review(ship=ship)[1] == []


def test_a_proven_payee_passes_without_the_website():
    case = clean_case()
    case["payment_ai"]["identity"]["payee_site"] = "unknown"
    assert review(case=case)[1] == []


@pytest.mark.parametrize("site", ["unknown", "off_site", None])
def test_a_changed_payee_off_the_website_is_refused(site):
    case = clean_case()
    case["payment_ai"]["identity"]["payee_site"] = site
    ship = shipment(carrier_payee_changed_at="2026-01-01T00:00:00+00:00")
    refused("neither on the carrier's website nor proven", case=case, ship=ship)


def test_a_limit_that_cannot_be_read_is_a_refusal_and_nothing_raises():
    module = agent()
    with patch.object(module.payment_controls, "agent_limit", AsyncMock(side_effect=RuntimeError("store down"))):
        limit, found = asyncio.run(module.review(clean_case(), shipment(), None))
    assert limit is None
    assert len(found) == 1 and "could not be read (RuntimeError)" in found[0]


def test_a_standing_that_cannot_be_read_is_a_refusal_and_nothing_raises():
    module = agent()
    with patch.object(module.payment_controls, "agent_limit", AsyncMock(return_value=LIMIT)), \
            patch.object(module.credit_profile, "profile", AsyncMock(side_effect=RuntimeError("store down"))):
        limit, found = asyncio.run(module.review(clean_case(), shipment(), None))
    assert limit is None
    assert len(found) == 1 and "could not be read (RuntimeError)" in found[0]


def imports(name):
    tree = ast.parse((SRC / f"{name}.py").read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found.add(str(node.module))
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


@pytest.mark.parametrize("name", ["agent_payment", "settler"])
def test_the_code_that_decides_a_payment_imports_no_model_client(name):
    assert not imports(name) & {"nebius_client", "payment_ai", "change_screen", "vf_logistics.nebius_client"}
