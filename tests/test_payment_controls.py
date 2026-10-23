"""
The payment controls (payment_controls.py): a pause switch and spending limits outside every
model.

Pinned here: the defaults are in force until a person publishes others; a published set must
state all four limits as sensible numbers that let one payout fit inside a day, with a
reason and a person behind it; a set that cannot be read back means nothing is paid; the
newest pause or resume decides whether payments run; only payments made today and only
those with an amount are counted; and a payout above the per-payout limit is held, while one
that would pass a daily limit is retried.

payment_controls is imported inside each test so that, before it exists, the tests fail
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402

ADMIN = "admin@forwarder.example"
OPERATOR = "operator@forwarder.example"
LIMITS = {
    "max_payout_usd": 2000, "carrier_daily_usd": 4000, "tenant_daily_usd": 8000,
    "carrier_daily_count": 3,
}


def run(coro):
    return asyncio.run(coro)


def controls():
    return importlib.import_module("vf_logistics.payment_controls")


def settled(store, carrier, amount, at=None):
    run(store.add_audit({
        "audit_id": f"audit-{uuid.uuid4().hex}", "case_id": f"CASE-{uuid.uuid4().hex[:8]}",
        "action": "settle_payment", "status": "settled",
        "detail": {"amount": amount, "carrier_key": carrier},
        "at": at or datetime.now(timezone.utc).isoformat(),
    }))


class TestThePublishedLimits(Base):
    def test_the_defaults_are_in_force_until_a_person_publishes_others(self):
        c = controls()
        assert run(c.limits()) == c.DEFAULT_LIMITS

    def test_a_published_set_replaces_the_defaults_and_the_newest_one_wins(self):
        c = controls()
        run(c.publish_limits(LIMITS, "quarter budget", ADMIN, "password"))
        assert run(c.limits()) == LIMITS
        run(c.publish_limits({**LIMITS, "carrier_daily_count": 4}, "raised", ADMIN, "password"))
        assert run(c.limits())["carrier_daily_count"] == 4

    def test_a_publication_needs_a_reason_and_a_person(self):
        c = controls()
        with pytest.raises(c.ControlsError) as no_reason:
            run(c.publish_limits(LIMITS, "  ", ADMIN, "password"))
        assert no_reason.value.status == 400
        assert "reason" in no_reason.value.message
        with pytest.raises(c.ControlsError) as no_person:
            run(c.publish_limits(LIMITS, "why", "", None))
        assert no_person.value.status == 403
        assert "by a person" in no_person.value.message
        assert run(c.limits()) == c.DEFAULT_LIMITS

    @pytest.mark.parametrize("bad,phrase", [
        (None, "exactly"),
        ("limits", "exactly"),
        ({"max_payout_usd": 1}, "exactly"),
        ({**LIMITS, "extra": 1}, "exactly"),
        ({**LIMITS, "max_payout_usd": 0}, "max_payout_usd must be a number above zero"),
        ({**LIMITS, "max_payout_usd": -5}, "max_payout_usd must be a number above zero"),
        ({**LIMITS, "carrier_daily_usd": "4000"}, "carrier_daily_usd must be a number above zero"),
        ({**LIMITS, "tenant_daily_usd": True}, "tenant_daily_usd must be a number above zero"),
        ({**LIMITS, "tenant_daily_usd": float("inf")}, "tenant_daily_usd must be a number above zero"),
        ({**LIMITS, "carrier_daily_count": 0}, "carrier_daily_count must be a whole number"),
        ({**LIMITS, "carrier_daily_count": 1001}, "carrier_daily_count must be a whole number"),
        ({**LIMITS, "carrier_daily_count": 2.5}, "carrier_daily_count must be a whole number"),
        ({**LIMITS, "carrier_daily_count": True}, "carrier_daily_count must be a whole number"),
        ({**LIMITS, "max_payout_usd": 5000}, "one payout can always fit"),
        ({**LIMITS, "carrier_daily_usd": 9000}, "one payout can always fit"),
    ])
    def test_a_set_of_limits_that_is_not_sensible_is_refused(self, bad, phrase):
        c = controls()
        with pytest.raises(c.ControlsError) as raised:
            run(c.publish_limits(bad, "why", ADMIN, "password"))
        assert raised.value.status == 400
        assert phrase in raised.value.message
        assert run(c.limits()) == c.DEFAULT_LIMITS

    def test_limits_that_cannot_be_read_back_are_not_limits_at_all(self):
        c = controls()
        run(self.store.add_audit({
            "audit_id": f"audit-{uuid.uuid4().hex}", "case_id": c.CASE, "action": c.PUBLISHED,
            "status": "done", "detail": {"limits": {"max_payout_usd": "lots"}},
            "at": datetime.now(timezone.utc).isoformat(),
        }))
        assert run(c.limits()) is None


class TestThePauseSwitch(Base):
    def test_payments_run_until_somebody_pauses_them(self):
        assert run(controls().pause_state())["paused"] is False

    def test_a_pause_names_who_and_why_and_a_resume_undoes_it(self):
        c = controls()
        paused = run(c.pause("a carrier was phished", OPERATOR, "password"))
        assert paused["paused"] is True
        assert paused["reason"] == "a carrier was phished"
        assert paused["by"] == OPERATOR
        assert run(c.pause_state())["paused"] is True
        resumed = run(c.resume("verified with the carrier", ADMIN, "password"))
        assert resumed["paused"] is False
        assert resumed["by"] == ADMIN
        assert run(c.pause_state())["paused"] is False

    def test_the_newest_of_several_switches_decides(self):
        c = controls()
        run(c.pause("first", OPERATOR, "password"))
        run(c.resume("second", ADMIN, "password"))
        run(c.pause("third", OPERATOR, "password"))
        state = run(c.pause_state())
        assert state["paused"] is True
        assert state["reason"] == "third"

    @pytest.mark.parametrize("reason", ["", "   ", None])
    def test_a_switch_needs_a_reason(self, reason):
        c = controls()
        with pytest.raises(c.ControlsError) as raised:
            run(c.pause(reason, OPERATOR, "password"))
        assert raised.value.status == 400
        assert "reason is required" in raised.value.message
        assert run(c.pause_state())["paused"] is False

    def test_a_switch_needs_a_person(self):
        c = controls()
        with pytest.raises(c.ControlsError) as raised:
            run(c.pause("why", "", None))
        assert raised.value.status == 403
        assert "by a person" in raised.value.message


class TestWhatHasBeenPaidToday(Base):
    def test_only_payments_made_today_with_an_amount_are_counted(self):
        c = controls()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        settled(self.store, "mekong", "100.10")
        settled(self.store, "mekong", "200.20")
        settled(self.store, "other", "50.00")
        settled(self.store, "mekong", "999.00", at=yesterday)
        settled(self.store, "mekong", "not a number")
        run(self.store.add_audit({
            "audit_id": f"audit-{uuid.uuid4().hex}", "case_id": "CASE-X", "action": "settle_payment",
            "status": "held", "detail": {"amount": "5000.00", "carrier_key": "mekong"},
            "at": datetime.now(timezone.utc).isoformat(),
        }))
        used = run(c.usage_today())
        assert used["tenant_usd"] == Decimal("350.30")
        assert used["carriers"]["mekong"] == {"usd": Decimal("300.30"), "count": 2}
        assert used["carriers"]["other"] == {"usd": Decimal("50.00"), "count": 1}

    def test_nothing_paid_is_nothing_counted(self):
        used = run(controls().usage_today())
        assert used == {"tenant_usd": Decimal(0), "carriers": {}}


class TestAPayoutAgainstTheLimits(Base):
    def _check(self, amount="1250", carrier="mekong", currency="USD"):
        return run(controls().check_payout(currency, Decimal(amount) if amount else None, carrier))

    def test_a_payout_inside_every_limit_goes_ahead(self):
        assert self._check() is None

    def test_a_payout_without_a_usable_amount_is_held(self):
        status, reason = self._check(amount=None)
        assert status == "held"
        assert "not a positive number" in reason

    def test_a_payout_in_another_currency_is_held_because_the_limits_are_in_dollars(self):
        status, reason = self._check(currency="EUR")
        assert status == "held"
        assert "limits are in USD and the payout is in EUR" in reason

    def test_a_payout_above_the_per_payout_limit_is_held_for_a_person(self):
        status, reason = self._check(amount="5000.01")
        assert status == "held"
        assert "per-payout limit of 5000.0 USD" in reason

    def test_a_payout_exactly_at_the_per_payout_limit_goes_ahead(self):
        assert self._check(amount="5000") is None

    def test_unreadable_published_limits_hold_everything(self):
        c = controls()
        run(self.store.add_audit({
            "audit_id": f"audit-{uuid.uuid4().hex}", "case_id": c.CASE, "action": c.PUBLISHED,
            "status": "done", "detail": {"limits": "garbage"},
            "at": datetime.now(timezone.utc).isoformat(),
        }))
        status, reason = self._check()
        assert status == "held"
        assert "cannot be read" in reason

    def test_a_carrier_that_has_been_paid_its_count_today_is_retried_tomorrow(self):
        for _ in range(5):
            settled(self.store, "mekong", "10.00")
        status, reason = self._check()
        assert status == "retry"
        assert "limit of 5 payouts today" in reason
        assert "when the day turns over" in reason

    def test_another_carriers_payments_do_not_count_against_this_one(self):
        for _ in range(5):
            settled(self.store, "someone-else", "10.00")
        assert self._check() is None

    def test_a_payout_that_would_pass_the_carriers_daily_amount_is_retried(self):
        settled(self.store, "mekong", "9000.00")
        status, reason = self._check(amount="1500")
        assert status == "retry"
        assert "past its daily limit of 10000.0 USD" in reason

    def test_a_payout_that_exactly_fills_the_carriers_day_goes_ahead(self):
        settled(self.store, "mekong", "9000.00")
        assert self._check(amount="1000") is None

    def test_a_payout_that_would_pass_the_days_total_is_retried(self):
        for i in range(10):
            settled(self.store, f"carrier-{i}", "4900.00")
        status, reason = self._check(amount="1500", carrier="fresh-carrier")
        assert status == "retry"
        assert "daily limit of 50000.0 USD" in reason

    def test_a_published_lower_limit_applies_at_once(self):
        c = controls()
        run(c.publish_limits(LIMITS, "tighter", ADMIN, "password"))
        status, reason = self._check(amount="2500")
        assert status == "held"
        assert "per-payout limit of 2000.0 USD" in reason
