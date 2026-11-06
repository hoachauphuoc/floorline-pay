"""
The payment controls routes (controls_routes.py).

Pinned here: anyone signed in can read the controls; an operator signed in with a password
can pause payments and cannot resume them; a governance admin signed in with a password
resumes them and publishes limits; the one-click guest session and a reviewer can do none
of that; the person recorded is the signed-in one, never a name in the request; the
controls' refusals come back with their status and message; and the usage is given as
exact decimal strings.

controls_routes is checked through the real app, so that, before the routes exist, the
tests fail instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY, mint  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics.app import app  # noqa: E402
from vf_logistics.store import get_store  # noqa: E402

ADMIN = "admin@forwarder.example"
OPERATOR = "operator@forwarder.example"
REVIEWER = "reviewer@forwarder.example"
LIMITS = {
    "max_payout_usd": 2000, "carrier_daily_usd": 4000, "tenant_daily_usd": 8000,
    "carrier_daily_count": 3,
}


def as_person(email, amr="password"):
    return {"X-VF-API-Key": TEST_KEY, "X-VF-Session": mint(email, amr=amr)}


class TestTheControlsRoutes(Base):
    def setup_method(self):
        super().setup_method()
        self.people = patch.dict(os.environ, {
            "ADMIN_EMAILS": ADMIN, "OPERATOR_EMAILS": OPERATOR, "REVIEWER_EMAILS": REVIEWER,
            "VF_ONE_CLICK_JUDGE": "true", "VF_ONE_CLICK_EMAIL": ADMIN,
        })
        self.people.start()
        self.client = app.test_client()

    def teardown_method(self):
        self.people.stop()
        super().teardown_method()

    def _read(self, headers=None):
        return self.client.get("/api/v1/payments/controls", headers=headers or as_person(REVIEWER))

    def _post(self, path, body, who=OPERATOR, amr="password"):
        return self.client.post(f"/api/v1/payments/{path}", json=body, headers=as_person(who, amr))

    def test_the_controls_start_running_with_the_defaults_and_nothing_paid(self):
        r = self._read()
        assert r.status_code == 200
        body = r.get_json()
        assert body["pause"]["paused"] is False
        assert body["limits_are_defaults"] is True
        assert body["limits"]["max_payout_usd"] == 5000.0
        assert body["usage_today"] == {"tenant_usd": "0", "carriers": {}}

    def test_an_operator_pauses_payments_and_it_shows(self):
        r = self._post("pause", {"reason": "a carrier was phished", "paused_by": "someone-else@x.example"})
        assert r.status_code == 200
        assert r.get_json()["paused"] is True
        assert r.get_json()["by"] == OPERATOR
        again = self._read().get_json()["pause"]
        assert (again["paused"], again["reason"], again["by"]) == (True, "a carrier was phished", OPERATOR)

    def test_a_pause_needs_a_reason(self):
        r = self._post("pause", {"reason": "  "})
        assert r.status_code == 400
        assert "reason is required" in r.get_json()["error"]

    def test_an_operator_cannot_resume_payments_but_an_admin_can(self):
        self._post("pause", {"reason": "stop"})
        assert self._post("resume", {"reason": "go"}, who=OPERATOR).status_code == 403
        assert self._read().get_json()["pause"]["paused"] is True
        r = self._post("resume", {"reason": "checked with the carrier"}, who=ADMIN)
        assert r.status_code == 200
        assert (r.get_json()["paused"], r.get_json()["by"]) == (False, ADMIN)

    def test_a_reviewer_cannot_pause_payments(self):
        assert self._post("pause", {"reason": "stop"}, who=REVIEWER).status_code == 403
        assert self._read().get_json()["pause"]["paused"] is False

    @pytest.mark.parametrize("path,body", [
        ("pause", {"reason": "stop"}),
        ("resume", {"reason": "go"}),
        ("limits", {"limits": LIMITS, "reason": "tighter"}),
    ])
    def test_the_one_click_guest_session_can_change_none_of_them(self, path, body):
        r = self._post(path, body, who=ADMIN, amr="one_click")
        assert r.status_code == 403
        assert self._read().get_json()["pause"]["paused"] is False
        assert self._read().get_json()["limits_are_defaults"] is True

    def test_an_admin_publishes_limits_and_they_replace_the_defaults(self):
        r = self._post("limits", {"limits": LIMITS, "reason": "quarter budget"}, who=ADMIN)
        assert r.status_code == 200
        assert r.get_json() == {"limits": {**LIMITS, "max_payout_usd": 2000.0,
                                           "carrier_daily_usd": 4000.0, "tenant_daily_usd": 8000.0}}
        body = self._read().get_json()
        assert body["limits_are_defaults"] is False
        assert body["limits"]["max_payout_usd"] == 2000.0

    def test_an_operator_cannot_publish_limits(self):
        r = self._post("limits", {"limits": LIMITS, "reason": "tighter"}, who=OPERATOR)
        assert r.status_code == 403
        assert self._read().get_json()["limits_are_defaults"] is True

    def test_limits_that_are_not_sensible_are_refused_with_the_reason(self):
        r = self._post("limits", {"limits": {**LIMITS, "max_payout_usd": 9000}, "reason": "x"}, who=ADMIN)
        assert r.status_code == 400
        assert "one payout can always fit" in r.get_json()["error"]
        assert self._post("limits", {"limits": LIMITS, "reason": ""}, who=ADMIN).status_code == 400

    def test_what_has_been_paid_today_is_given_as_exact_decimal_strings(self):
        asyncio.run(get_store().add_audit({
            "audit_id": f"audit-{uuid.uuid4().hex}", "case_id": "CASE-1", "action": "settle_payment",
            "status": "settled", "detail": {"amount": "1250.10", "carrier_key": "mekong"},
            "at": datetime.now(timezone.utc).isoformat(),
        }))
        usage = self._read().get_json()["usage_today"]
        assert usage["tenant_usd"] == "1250.10"
        assert usage["carriers"]["mekong"] == {"usd": "1250.10", "count": 1}
