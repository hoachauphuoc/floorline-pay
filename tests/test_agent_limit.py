"""
The limit a person publishes for the agent (payment_controls.py, controls_routes.py).

Pinned here: with no limit published the agent may pay nothing; a governance admin signed in with a password publishes a limit below
the second-approval threshold, with a reason, and 0 turns it off; a stored limit that cannot be read, or that no password sign-in
stands behind, counts as none; an operator, a reviewer and the one-click guest cannot publish one; and the person recorded is the
signed-in one, never a name in the request.
"""

from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY, mint  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402
from vf_logistics import payment_controls  # noqa: E402
from vf_logistics.app import app  # noqa: E402
from vf_logistics.store import get_store, new_id, utcnow  # noqa: E402

ADMIN = "admin@forwarder.example"
OPERATOR = "operator@forwarder.example"
REVIEWER = "reviewer@forwarder.example"


def run(coro):
    return asyncio.run(coro)


def as_person(email, amr="password"):
    return {"X-VF-API-Key": TEST_KEY, "X-VF-Session": mint(email, amr=amr)}


def refusal(raw=1000, reason="first month of agent payouts", actor=ADMIN, auth="password"):
    with pytest.raises(payment_controls.ControlsError) as raised:
        run(payment_controls.publish_agent_limit(raw, reason, actor, auth))
    return raised.value


class TestTheAgentLimit(Base):
    def rows(self):
        found, _ = run(get_store().query_audit(case_id=payment_controls.CASE, limit=50))
        return [r for r in found if r.get("action") == payment_controls.AGENT_LIMIT]

    def test_with_no_limit_published_the_agent_has_none(self):
        assert run(payment_controls.agent_limit()) is None

    def test_a_published_limit_is_read_back_with_the_person_and_the_reason(self):
        published = run(payment_controls.publish_agent_limit(1000, " first month ", ADMIN, "password"))
        assert published["limit_usd"] == Decimal("1000")
        live = run(payment_controls.agent_limit())
        assert live["limit_usd"] == Decimal("1000")
        assert live["published_by"] == ADMIN
        assert live["reason"] == "first month"
        assert live["at"]
        assert self.rows()[0]["detail"]["auth"] == "password"

    def test_zero_turns_the_agent_off(self):
        run(payment_controls.publish_agent_limit(1000, "on", ADMIN, "password"))
        assert run(payment_controls.publish_agent_limit(0, "a carrier was phished", ADMIN, "password")) is None
        assert run(payment_controls.agent_limit()) is None
        assert len(self.rows()) == 2

    @pytest.mark.parametrize("raw", [None, "1000", True, -1, float("nan"), float("inf"), [1000]])
    def test_a_limit_that_is_not_an_amount_is_refused(self, raw):
        exc = refusal(raw=raw)
        assert exc.status == 400
        assert "limit_usd must be a number" in exc.message
        assert self.rows() == []

    def test_a_limit_at_the_second_approval_threshold_is_refused(self, monkeypatch):
        monkeypatch.delenv("PAYMENT_DUAL_APPROVAL_USD", raising=False)
        exc = refusal(raw=2500)
        assert exc.status == 400
        assert "below the second-approval threshold of 2500 USD" in exc.message
        assert self.rows() == []

    def test_the_threshold_in_force_is_the_one_that_counts(self):
        with patch.dict(os.environ, {"PAYMENT_DUAL_APPROVAL_USD": "500"}):
            exc = refusal(raw=500)
        assert exc.status == 400
        assert "below the second-approval threshold of 500 USD" in exc.message

    def test_a_limit_needs_a_reason(self):
        exc = refusal(reason="   ")
        assert exc.status == 400
        assert "reason is required" in exc.message
        assert self.rows() == []

    def test_a_limit_needs_a_person(self):
        exc = refusal(actor="")
        assert exc.status == 403
        assert "published by a person" in exc.message
        assert self.rows() == []

    @pytest.mark.parametrize("auth", ["one_click", "api_key", None, ""])
    def test_a_limit_needs_a_password_sign_in(self, auth):
        exc = refusal(auth=auth)
        assert exc.status == 403
        assert "password sign-in" in exc.message
        assert self.rows() == []

    @pytest.mark.parametrize("detail", [
        {"limit_usd": "not a number", "published_by": ADMIN, "auth": "password"},
        {"limit_usd": "NaN", "published_by": ADMIN, "auth": "password"},
        {"limit_usd": "-5", "published_by": ADMIN, "auth": "password"},
        {"limit_usd": "1000", "published_by": "", "auth": "password"},
        {"limit_usd": "1000", "published_by": ADMIN, "auth": "one_click"},
        {"limit_usd": "1000", "published_by": ADMIN},
        {},
    ])
    def test_a_stored_limit_that_cannot_be_trusted_counts_as_none(self, detail):
        run(get_store().add_audit({
            "audit_id": new_id("audit"), "case_id": payment_controls.CASE, "action": payment_controls.AGENT_LIMIT,
            "status": "done", "detail": detail, "at": utcnow(),
        }))
        assert run(payment_controls.agent_limit()) is None


class TestTheAgentLimitRoute(Base):
    def setup_method(self):
        super().setup_method()
        self.people = patch.dict(os.environ, {
            "ADMIN_EMAILS": ADMIN, "OPERATOR_EMAILS": OPERATOR, "REVIEWER_EMAILS": REVIEWER,
            "VF_ONE_CLICK_JUDGE": "true", "VF_ONE_CLICK_EMAIL": ADMIN, "PAYMENT_DUAL_APPROVAL_USD": "2500",
        })
        self.people.start()
        self.client = app.test_client()

    def teardown_method(self):
        self.people.stop()
        super().teardown_method()

    def _post(self, body, who=ADMIN, amr="password"):
        return self.client.post("/api/v1/payments/agent/limit", json=body, headers=as_person(who, amr))

    def _shown(self):
        return self.client.get("/api/v1/payments/controls", headers=as_person(REVIEWER)).get_json()["agent_limit"]

    def test_the_controls_show_the_agent_off_until_a_limit_is_published(self):
        assert self._shown() is None

    def test_an_admin_publishes_a_limit_and_the_controls_show_who_and_why(self):
        r = self._post({"limit_usd": 1000, "reason": "first month", "published_by": "someone-else@x.example"})
        assert r.status_code == 200
        assert r.get_json()["agent_limit"]["limit_usd"] == "1000"
        shown = self._shown()
        assert shown["limit_usd"] == "1000"
        assert shown["published_by"] == ADMIN
        assert shown["reason"] == "first month"

    def test_an_admin_turns_the_agent_off_with_zero(self):
        self._post({"limit_usd": 1000, "reason": "on"})
        r = self._post({"limit_usd": 0, "reason": "a carrier was phished"})
        assert r.status_code == 200
        assert r.get_json()["agent_limit"] is None
        assert self._shown() is None

    def test_a_refused_limit_comes_back_with_its_status_and_message(self):
        r = self._post({"limit_usd": 2500, "reason": "too high"})
        assert r.status_code == 400
        assert "below the second-approval threshold" in r.get_json()["error"]
        assert self._shown() is None

    @pytest.mark.parametrize("who,amr", [(OPERATOR, "password"), (REVIEWER, "password"), (ADMIN, "one_click")])
    def test_only_an_admin_signed_in_with_a_password_can_publish_one(self, who, amr):
        r = self._post({"limit_usd": 1000, "reason": "try"}, who=who, amr=amr)
        assert r.status_code == 403
        assert self._shown() is None
