"""
The settler under the payment controls (settler.py with payment_controls.py).

Pinned here, with the real controls, registry and bookings and no stand-in for them: paused
payments are retried and nothing moves, while a block still voids the authorization; a
payout above the per-payout limit is held; one that would pass a daily limit is retried;
the limits count what was paid and a payment records its amount and carrier so that it
counts; and publishing different limits changes what goes through.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from vf_logistics import store as store_mod

ADMIN = "admin@forwarder.example"
OPERATOR = "operator@forwarder.example"
REVIEWER = "reviewer@forwarder.example"
PAYEE = "payments@mekong-haulage.example"
NOTE = "called the carrier on the number in the contract; they confirmed the account"
CAPTURED = {"status": "ok", "http_status": 201, "body": {"id": "CAP-1", "status": "COMPLETED"}, "error": None}
PAID_OUT = {"status": "ok", "http_status": 201,
            "body": {"batch_header": {"payout_batch_id": "PAYOUT-1"}}, "error": None}
VOIDED = {"status": "ok", "http_status": 204, "body": {}, "error": None}
LIVE = {"status": "ok", "http_status": 200, "error": None,
        "body": {"id": "A", "status": "CREATED", "amount": {"currency_code": "USD", "value": "1300.00"}}}
LIMITS = {
    "max_payout_usd": 1250, "carrier_daily_usd": 2000, "tenant_daily_usd": 2000,
    "carrier_daily_count": 1,
}


def run(coro):
    return asyncio.run(coro)


def mod(name):
    return importlib.import_module(f"vf_logistics.{name}")


def carrier():
    return f"Mekong Haulage {uuid.uuid4().hex[:6]} Co"


def decision(name, outcome="RELEASED_BY_HUMAN", payout=1250.0):
    registry = mod("carrier_registry")
    if run(registry.current(name)) is None:
        made = run(registry.register_payee(name, PAYEE, OPERATOR, "password"))
        run(registry.verify_payee(made["carrier_key"], made["version"], REVIEWER, "password", NOTE))
    authorization = f"AUTH{uuid.uuid4().hex[:12].upper()}"
    run(mod("payment_binding").record_authorization(authorization, "SHP-CTL-1", "1300.00", "USD", "ORDER-1"))
    case_id = f"CASE-{uuid.uuid4().hex[:10]}"
    run(store_mod.get_store().put_case({"case_id": case_id, "state": outcome, "shipment": {
        "shipment_id": "SHP-CTL-1", "currency": "USD", "shipping_cost": 1300.0,
        "payout_amount": payout, "carrier_name": name, "paypal_authorization_id": authorization,
    }}))
    return {"case_id": case_id, "outcome": outcome, "approval_id": "apr-1",
            "idempotency_key": f"exec-{uuid.uuid4().hex[:8]}"}


@pytest.fixture
def pp(monkeypatch):
    monkeypatch.setattr(store_mod, "_store", None)
    settler = mod("settler")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
    client = settler.paypal_client
    with patch.object(client, "get_authorization", AsyncMock(return_value=LIVE)), \
            patch.object(client, "capture_authorization", AsyncMock(return_value=CAPTURED)) as capture, \
            patch.object(client, "create_payout", AsyncMock(return_value=PAID_OUT)) as payout, \
            patch.object(client, "void_authorization", AsyncMock(return_value=VOIDED)) as void:
        yield settler, capture, payout, void


def test_paused_payments_are_retried_and_nothing_moves(pp):
    settler, capture, payout, _ = pp
    run(mod("payment_controls").pause("a carrier was phished", OPERATOR, "password"))
    result = run(settler.settle(decision(carrier())))
    assert result["status"] == "retry"
    assert "payments are paused (a carrier was phished)" in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_the_same_decision_is_paid_once_payments_resume(pp):
    settler, capture, payout, _ = pp
    controls = mod("payment_controls")
    run(controls.pause("stop", OPERATOR, "password"))
    d = decision(carrier())
    assert run(settler.settle(d))["status"] == "retry"
    run(controls.resume("checked", ADMIN, "password"))
    assert run(settler.settle(d))["status"] == "settled"
    payout.assert_awaited_once()


def test_a_block_still_voids_the_authorization_while_payments_are_paused(pp):
    settler, capture, payout, void = pp
    run(mod("payment_controls").pause("stop", OPERATOR, "password"))
    result = run(settler.settle(decision(carrier(), outcome="BLOCKED_BY_HUMAN")))
    assert result["status"] == "voided"
    void.assert_awaited_once()
    capture.assert_not_called()
    payout.assert_not_called()


def test_a_payout_above_the_per_payout_limit_is_held(pp):
    settler, capture, payout, _ = pp
    run(mod("payment_controls").publish_limits(
        {**LIMITS, "max_payout_usd": 1000, "carrier_daily_usd": 2000}, "tighter", ADMIN, "password"))
    result = run(settler.settle(decision(carrier())))
    assert result["status"] == "held"
    assert "per-payout limit of 1000.0 USD" in result["reason"]
    capture.assert_not_called()
    payout.assert_not_called()


def test_a_second_payout_to_a_carrier_past_its_daily_count_is_retried(pp):
    settler, capture, payout, _ = pp
    run(mod("payment_controls").publish_limits(LIMITS, "one a day", ADMIN, "password"))
    name = carrier()
    assert run(settler.settle(decision(name)))["status"] == "settled"
    second = run(settler.settle(decision(name)))
    assert second["status"] == "retry"
    assert "limit of 1 payouts today" in second["reason"]
    assert payout.await_count == 1


def test_a_payout_that_would_pass_the_days_total_is_retried(pp):
    settler, capture, payout, _ = pp
    run(mod("payment_controls").publish_limits(LIMITS, "small day", ADMIN, "password"))
    assert run(settler.settle(decision(carrier())))["status"] == "settled"
    second = run(settler.settle(decision(carrier())))
    assert second["status"] == "retry"
    assert "daily limit of 2000.0 USD" in second["reason"]
    assert payout.await_count == 1


def test_a_payment_records_its_amount_and_carrier_so_that_it_counts(pp):
    settler, capture, payout, _ = pp
    name = carrier()
    d = decision(name)
    assert run(settler.settle(d))["status"] == "settled"
    rows, _ = run(store_mod.get_store().query_audit(
        case_id=d["case_id"], action="settle_payment", status="settled"))
    detail = rows[0]["detail"]
    assert Decimal(detail["amount"]) == Decimal("1250")
    assert detail["carrier_key"] == mod("carrier_registry").carrier_key(name)
    used = run(mod("payment_controls").usage_today())
    assert used["tenant_usd"] == Decimal("1250")
    assert used["carriers"][detail["carrier_key"]]["count"] == 1


def test_the_audit_row_of_a_payment_carries_no_payee_address(pp):
    settler, capture, payout, _ = pp
    d = decision(carrier())
    run(settler.settle(d))
    rows, _ = run(store_mod.get_store().query_audit(case_id=d["case_id"], action="settle_payment"))
    assert PAYEE not in str(rows)
