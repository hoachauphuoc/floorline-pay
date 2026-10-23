"""
The second-approval route (POST /api/v1/review/<case_id>/second-approve).

Pinned here: a reviewer signed in with a password, who is not the first approver, seconds a
large release and the person recorded is the signed-in one, never a name in the request; the
first approver, a one-click session and a person who is not a reviewer are refused with their
status and message; and the note is required.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY, mint  # noqa: E402
from test_dual_approval import payment  # noqa: E402
from test_evidence_eval_approve import REVIEWER, Base, approve_open, run  # noqa: E402
from vf_logistics import evidence  # noqa: E402
from vf_logistics.app import app  # noqa: E402

SECOND = "second.reviewer@forwarder.example"
OUTSIDER = "outsider@elsewhere.example"
NOTE = "checked the invoice and the booking against the carrier contract"
URL = "/api/v1/review/CASE-1/second-approve"


def as_person(email, amr="password"):
    return {"X-VF-API-Key": TEST_KEY, "X-VF-Session": mint(email, amr=amr)}


class TestTheSecondApprovalRoute(Base):
    def setup_method(self):
        super().setup_method()
        self.people = patch.dict(os.environ, {
            "REVIEWER_EMAILS": f"{REVIEWER},{SECOND}", "VF_ONE_CLICK_JUDGE": "true", "VF_ONE_CLICK_EMAIL": SECOND,
        })
        self.people.start()
        self.client = app.test_client()
        self.put(shipment=payment(3000.0))
        self.request_and_submit()
        run(evidence.reevaluate("CASE-1"))
        result = run(approve_open("CASE-1", "release", REVIEWER, note="Reviewed", actor_auth="password"))
        self.approval_id = result["approval"]["approval_id"]

    def teardown_method(self):
        self.people.stop()
        super().teardown_method()

    def _post(self, who=SECOND, amr="password", **body):
        body.setdefault("approval_id", self.approval_id)
        body.setdefault("note", NOTE)
        return self.client.post(URL, json=body, headers=as_person(who, amr))

    def test_a_second_reviewer_seconds_the_release_and_is_recorded_as_themself(self):
        r = self._post(reviewer="someone-else@x.example")
        assert r.status_code == 200
        assert r.get_json()["approval"]["second_approval"]["approver"] == SECOND
        assert self.stored()["verification"]["approvals"][0]["second_approval"]["note"] == NOTE

    def test_the_first_approver_is_refused(self):
        r = self._post(who=REVIEWER)
        assert r.status_code == 403
        assert "different person" in r.get_json()["error"]

    def test_a_one_click_session_is_refused(self):
        r = self._post(amr="one_click")
        assert r.status_code == 403
        assert "second_approval" not in self.stored()["verification"]["approvals"][0]

    def test_a_person_who_is_not_a_reviewer_is_refused(self):
        r = self._post(who=OUTSIDER)
        assert r.status_code == 403
        assert "second_approval" not in self.stored()["verification"]["approvals"][0]

    def test_a_missing_note_is_refused(self):
        r = self._post(note="  ")
        assert r.status_code == 400
        assert "needs a note" in r.get_json()["error"]
