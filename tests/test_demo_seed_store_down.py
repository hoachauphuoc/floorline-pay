"""
A store that cannot be reached when the app starts does not stop the app from starting (7 Nov 2026).

The failed Render deploy of f4ebe2c died in the demo seed with "429 Quota exceeded" and "Worker failed to boot". A worker that cannot boot takes the live
service down with it. Skipping the seed is safe: nothing is seeded, so no payment is made on a payee that was never verified, and the next start seeds again.
A missing setting and any other failure still stop the start, so they are seen in the logs.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable

from vf_logistics import demo_seed


@pytest.fixture(autouse=True)
def seed_on(monkeypatch):
    monkeypatch.setenv("VF_SEED_PAYMENT_DEMO", "true")
    monkeypatch.setenv("VF_DEMO_PAYEE_EMAIL", "sb-carrier@personal.example.com")


def test_a_spent_quota_skips_the_seed_and_says_so(caplog):
    with patch.object(demo_seed, "seed", AsyncMock(side_effect=ResourceExhausted("429 Quota exceeded."))):
        with caplog.at_level(logging.WARNING):
            assert demo_seed.maybe_seed() is None
    assert "demo seed skipped" in caplog.text
    assert "ResourceExhausted" in caplog.text


def test_an_unavailable_store_skips_the_seed(caplog):
    with patch.object(demo_seed, "seed", AsyncMock(side_effect=ServiceUnavailable("backend down"))):
        with caplog.at_level(logging.WARNING):
            assert demo_seed.maybe_seed() is None
    assert "demo seed skipped" in caplog.text


def test_a_working_store_still_seeds():
    with patch.object(demo_seed, "seed", AsyncMock(return_value={"registered_payee": True})):
        assert demo_seed.maybe_seed() == {"registered_payee": True}


def test_a_missing_payee_setting_still_stops_the_start(monkeypatch):
    monkeypatch.delenv("VF_DEMO_PAYEE_EMAIL")
    with pytest.raises(demo_seed.DemoSeedError) as raised:
        demo_seed.maybe_seed()
    assert "VF_DEMO_PAYEE_EMAIL" in str(raised.value)


def test_any_other_failure_still_stops_the_start():
    with patch.object(demo_seed, "seed", AsyncMock(side_effect=RuntimeError("bug"))):
        with pytest.raises(RuntimeError):
            demo_seed.maybe_seed()
