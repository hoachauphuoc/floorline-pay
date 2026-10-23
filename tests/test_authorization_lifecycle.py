"""
Where a PayPal authorization is in its life (authorization_lifecycle.py).

Pinned here: inside the three-day honor period it is honored; between three and 29 days it
must be renewed; from 29 days, or from PayPal's own expiration time if that is sooner, it is
expired; and a reply with no create_time it can read, or one without a time zone, is
unknown, which the settler treats as a reason to pay nothing.

authorization_lifecycle is imported inside each test so that, before it exists, every test
fails instead of the file failing to collect.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def mod():
    return importlib.import_module("vf_logistics.authorization_lifecycle")


def made(days_ago, **more):
    return {"create_time": (NOW - timedelta(days=days_ago)).isoformat(), **more}


@pytest.mark.parametrize("days, expected", [
    (0, "honored"),
    (2.9, "honored"),
    (3, "reauthorize"),
    (28.9, "reauthorize"),
    (29, "expired"),
    (40, "expired"),
])
def test_the_stage_follows_the_age(days, expected):
    assert mod().stage(made(days), NOW) == expected


def test_paypals_own_expiration_time_can_end_it_sooner():
    soon = (NOW - timedelta(hours=1)).isoformat()
    assert mod().stage(made(5, expiration_time=soon), NOW) == "expired"
    later = (NOW + timedelta(days=20)).isoformat()
    assert mod().stage(made(5, expiration_time=later), NOW) == "reauthorize"


@pytest.mark.parametrize("facts", [
    {},
    {"create_time": None},
    {"create_time": "yesterday"},
    {"create_time": "2026-10-20T12:00:00"},
    {"create_time": 1766000000},
])
def test_a_creation_time_that_cannot_be_read_is_unknown(facts):
    assert mod().stage(facts, NOW) == "unknown"


def test_the_z_suffix_that_paypal_uses_is_read():
    assert mod().stage({"create_time": "2026-10-23T11:00:00Z"}, NOW) == "honored"
