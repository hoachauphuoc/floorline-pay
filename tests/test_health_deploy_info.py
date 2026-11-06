"""
/health says which revision serves and whether the sent ledger is configured, so a
person (or PDPOST) can prove what is live without credentials. It never exposes the
subscription's name.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
from test_routes import FlaskTestBase  # noqa: E402
from vf_logistics import tools  # noqa: E402


class TestHealthSaysWhatIsDeployed(FlaskTestBase):
    def _health(self) -> dict:
        return json.loads(self.client.get("/health").data)

    def test_the_revision_is_the_cloud_run_revision(self):
        with patch.dict(os.environ, {"K_REVISION": "vf-app-00042-abc"}):
            self.assertEqual(self._health()["revision"], "vf-app-00042-abc")

    def test_the_revision_is_the_render_commit_when_there_is_no_cloud_run_revision(self):
        with patch.dict(os.environ, {"RENDER_GIT_COMMIT": "645b921abc"}):
            os.environ.pop("K_REVISION", None)
            self.assertEqual(self._health()["revision"], "645b921abc")

    def test_the_cloud_run_revision_wins_over_the_render_commit(self):
        with patch.dict(os.environ, {"K_REVISION": "svc-00001", "RENDER_GIT_COMMIT": "645b921abc"}):
            self.assertEqual(self._health()["revision"], "svc-00001")

    def test_health_names_the_paypal_hackathon_not_the_nebius_one(self):
        body = self._health()
        self.assertEqual(body["hackathon"], "PayPal AI Hackathon")
        self.assertNotIn("Nebius", json.dumps(body))

    def test_the_revision_is_null_when_nothing_names_it(self):
        with patch.dict(os.environ):
            os.environ.pop("K_REVISION", None)
            os.environ.pop("RENDER_GIT_COMMIT", None)
            self.assertIsNone(self._health()["revision"])

    def test_the_sent_ledger_is_reported_as_configured(self):
        with patch.object(tools, "SENT_LEDGER_SUBSCRIPTION", "case-decisions-sent-ledger"):
            self.assertIs(self._health()["sent_ledger"], True)

    def test_the_sent_ledger_is_reported_as_not_configured(self):
        with patch.object(tools, "SENT_LEDGER_SUBSCRIPTION", ""):
            self.assertIs(self._health()["sent_ledger"], False)

    def test_the_subscription_name_is_not_exposed(self):
        with patch.object(tools, "SENT_LEDGER_SUBSCRIPTION", "very-private-ledger-name"):
            body = self.client.get("/health").get_data(as_text=True)
        self.assertNotIn("very-private-ledger-name", body)


if __name__ == "__main__":
    unittest.main()
