"""
The evaluation table of the attack theatre (payment_eval.offline_summary, GET /api/v1/defense/evaluation).

Pinned here: the offline summary is the summary of the corpus run without a model (24 of 36 caught by the
deterministic checks, 32 of 36 with the carrier look-up, the analyst arm not run); it is computed once per
process; and the route answers anyone signed in, says the corpus is synthetic, and only reads.

The new function is looked up inside each test so that, before it exists, every test fails instead of the file
failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_console_session import TEST_KEY  # noqa: E402
from test_evidence_eval_approve import Base  # noqa: E402


def ev():
    return importlib.import_module("vf_logistics.payment_eval")


def test_the_offline_summary_is_the_corpus_run_without_a_model():
    module = ev()
    module._CACHE.clear()
    summary = asyncio.run(module.offline_summary())
    arms = summary["arms"]
    assert (arms["floor"]["recall"]["count"], arms["floor"]["recall"]["of"]) == (24, 36)
    assert arms["floor"]["false_positive_rate"]["count"] == 0
    assert (arms["identity"]["recall"]["count"], arms["identity"]["false_positive_rate"]["count"]) == (32, 6)
    assert arms["analyst"] is None


def test_it_is_computed_once_per_process(monkeypatch):
    module = ev()
    module._CACHE.clear()
    first = asyncio.run(module.offline_summary())

    async def boom(*args, **kwargs):
        raise AssertionError("the corpus must not be run twice")

    monkeypatch.setattr(module, "run", boom)
    assert asyncio.run(module.offline_summary()) is first


class TestTheRoute(Base):
    def test_anyone_signed_in_reads_the_table_and_it_says_synthetic(self):
        from vf_logistics.app import app

        r = app.test_client().get("/api/v1/defense/evaluation", headers={"X-VF-API-Key": TEST_KEY})
        assert r.status_code == 200
        body = r.get_json()
        assert body["synthetic"] is True
        assert body["flag_floor"] == 40
        assert body["summary"]["arms"]["floor"]["recall"]["count"] == 24
        assert body["summary"]["arms"]["analyst"] is None
        assert "invented" in body["note"].lower()

    def test_it_only_reads(self):
        from vf_logistics.app import app

        r = app.test_client().post("/api/v1/defense/evaluation", headers={"X-VF-API-Key": TEST_KEY}, json={})
        assert r.status_code == 405
