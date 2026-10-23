"""
The demo seed (demo_seed.py): a verified payee for one carrier and a frozen second carrier, nothing else.

Pinned here: the payee is registered by one seed identity and verified by another; a payee that is already verified is never
replaced and a second run changes nothing; the second carrier is frozen with a reason that says it is demo data; the seed does
nothing unless VF_SEED_PAYMENT_DEMO is true; and asking for it without VF_DEMO_PAYEE_EMAIL stops the start and names the
setting to make.

The module is looked up inside each fixture so that, before it exists, every test fails instead of the file failing to collect.
Each test gets carrier names of its own, because the in-memory store is shared by the whole run.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid

import pytest

from vf_logistics import carrier_registry, payment_freeze


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def demo(monkeypatch):
    module = importlib.import_module("vf_logistics.demo_seed")
    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setattr(module, "CARRIER", f"Seed Carrier {suffix}")
    monkeypatch.setattr(module, "FROZEN_CARRIER", f"Seed Frozen {suffix}")
    return module


class TestSeed:
    def test_the_carrier_gets_a_payee_verified_by_someone_other_than_the_registrant(self, demo):
        run(demo.seed("Carrier@Example.com"))
        live = run(carrier_registry.current(demo.CARRIER))
        assert live["payee_email"] == "carrier@example.com"
        assert live["registered_by"] != live["verified_by"]

    def test_the_second_carrier_is_frozen_and_the_reason_says_it_is_demo_data(self, demo):
        run(demo.seed("carrier@example.com"))
        frozen = run(payment_freeze.frozen("carrier", demo.FROZEN_CARRIER))
        assert frozen is not None
        assert "demo data" in frozen["reason"]

    def test_running_it_again_changes_nothing(self, demo):
        first = run(demo.seed("carrier@example.com"))
        second = run(demo.seed("carrier@example.com"))
        assert first["registered_payee"] is True
        assert second["registered_payee"] is False
        assert run(carrier_registry.current(demo.CARRIER))["version"] == 1

    def test_a_payee_that_is_already_verified_is_never_replaced(self, demo):
        run(demo.seed("first@example.com"))
        run(demo.seed("second@example.com"))
        assert run(carrier_registry.current(demo.CARRIER))["payee_email"] == "first@example.com"


class TestMaybeSeed:
    def test_it_does_nothing_unless_asked(self, demo, monkeypatch):
        monkeypatch.delenv("VF_SEED_PAYMENT_DEMO", raising=False)
        assert demo.maybe_seed() is None
        assert run(carrier_registry.current(demo.CARRIER)) is None

    def test_a_flag_without_a_payee_stops_the_start_and_names_the_setting(self, demo, monkeypatch):
        monkeypatch.setenv("VF_SEED_PAYMENT_DEMO", "true")
        monkeypatch.delenv("VF_DEMO_PAYEE_EMAIL", raising=False)
        with pytest.raises(demo.DemoSeedError) as raised:
            demo.maybe_seed()
        assert "VF_DEMO_PAYEE_EMAIL" in str(raised.value)
        assert run(carrier_registry.current(demo.CARRIER)) is None

    def test_with_the_flag_and_a_payee_it_seeds(self, demo, monkeypatch):
        monkeypatch.setenv("VF_SEED_PAYMENT_DEMO", "true")
        monkeypatch.setenv("VF_DEMO_PAYEE_EMAIL", "carrier@example.com")
        assert demo.maybe_seed()["registered_payee"] is True
        assert run(carrier_registry.current(demo.CARRIER))["payee_email"] == "carrier@example.com"
