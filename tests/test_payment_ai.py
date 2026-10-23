"""
The payment-risk analyst and the carrier look-up (payment_ai.py).

Pinned here: the analyst is shown numbers and categories and never a name or an address; a
reply is read as elevated or normal only when it says so clearly, a reason that carries an
address is dropped and a credit suggestion is kept only if it is A to D; a model that cannot be
reached, or a reply that cannot be read, is "unknown" and raises nothing; a look-up that did not
run is "unverified", never found; a look-up that ran is found only when a result names the
carrier by the words that make it that carrier; and assess() never raises.

payment_ai is imported inside each test so that, before it exists, every test fails instead of
the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest

PAYEE = "payments@mekong-haulage.example"


def run(coro):
    return asyncio.run(coro)


def mod():
    return importlib.import_module("vf_logistics.payment_ai")


def shipment(**over):
    base = {
        "carrier_name": "Mekong Haulage Co", "currency": "USD", "payout_amount": 2000.0, "shipping_cost": 1600.0,
        "carrier_payee_email": PAYEE, "carrier_registered_payee_email": PAYEE,
        "carrier_payee_changed_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    }
    base.update(over)
    return base


def reply(**over):
    body = {"verdict": "elevated", "confidence": 0.8, "reasons": ["payout above the booked freight"], "suggested_tier": "C"}
    body.update(over)
    return json.dumps(body)


def model(text):
    seen = {}

    async def complete(**kwargs):
        seen.update(kwargs)
        if isinstance(text, Exception):
            raise text
        return text, 10, 20

    return complete, seen


class TestWhatTheAnalystSees:
    def test_the_features_are_numbers_and_categories_only(self):
        out = mod().features(shipment(), {"paid_count": 4, "frozen": False})
        assert out["payout"] == 2000.0
        assert out["booked_freight"] == 1600.0
        assert out["payout_to_freight"] == 1.25
        assert out["payee_domain_kind"] == "own_domain"
        assert out["payee_domain_resembles_carrier"] is True
        assert out["payee_matches_registry"] is True
        assert out["payee_changed_days_ago"] == 3
        assert out["payee_change_request_flagged"] is False
        assert out["carrier_payments_made"] == 4
        assert out["carrier_frozen"] is False

    def test_no_name_and_no_address_reach_the_model(self):
        seen = json.dumps(mod().features(shipment(), {"paid_count": 4, "frozen": False}))
        for secret in ("@", "mekong", "Mekong", "haulage", "Haulage"):
            assert secret not in seen

    def test_a_free_mail_payee_a_mismatch_and_a_flagged_request_are_visible(self):
        out = mod().features(
            shipment(carrier_payee_email="someone@gmail.com", carrier_payee_change_flagged=True), {},
        )
        assert out["payee_domain_kind"] == "free_mail"
        assert out["payee_domain_resembles_carrier"] is False
        assert out["payee_matches_registry"] is False
        assert out["payee_change_request_flagged"] is True

    def test_missing_figures_are_none_not_zero(self):
        out = mod().features({"carrier_name": "X Co"}, {})
        assert out["payout"] is None
        assert out["payout_to_freight"] is None
        assert out["payee_domain_kind"] == "none"
        assert out["payee_changed_days_ago"] is None


class TestReadingTheReply:
    def test_a_clear_verdict_is_kept(self):
        out = mod().interpret(json.loads(reply()))
        assert out == {"verdict": "elevated", "confidence": 0.8,
                       "reasons": ["payout above the booked freight"], "suggested_tier": "C"}

    @pytest.mark.parametrize("parsed", [None, [], {}, {"verdict": "maybe"}, {"verdict": None}, {"risk": "high"}])
    def test_anything_else_is_unknown(self, parsed):
        out = mod().interpret(parsed)
        assert out["verdict"] == "unknown"
        assert out["confidence"] == 0.0
        assert out["suggested_tier"] is None

    @pytest.mark.parametrize("raw, expected", [(5, 1.0), (-1, 0.0), ("0.4", 0.4), ("high", 0.0), (None, 0.0)])
    def test_the_confidence_is_clamped_and_never_an_error(self, raw, expected):
        assert mod().interpret({"verdict": "normal", "confidence": raw})["confidence"] == expected

    def test_reasons_with_an_address_are_dropped_and_the_rest_capped(self):
        out = mod().interpret({"verdict": "elevated", "confidence": 0.9, "reasons": [
            "mail to boss@x.example", "one", "two", "three", "four " + "y" * 300]})
        assert out["reasons"] == ["one", "two", "three"]
        long = mod().interpret({"verdict": "elevated", "reasons": ["y" * 300]})
        assert len(long["reasons"][0]) == 160

    @pytest.mark.parametrize("tier, expected", [("A", "A"), ("D", "D"), ("Z", None), ("a", None), (None, None), (3, None)])
    def test_only_a_to_d_is_kept_as_a_suggestion(self, tier, expected):
        assert mod().interpret({"verdict": "normal", "suggested_tier": tier})["suggested_tier"] == expected


class TestAskingTheModel:
    def test_the_model_gets_the_features_as_data(self):
        complete, seen = model(reply())
        out = run(mod().analyst({"payout": 2000.0, "carrier_frozen": False}, complete))
        assert out["verdict"] == "elevated"
        assert json.loads(seen["user_text"]) == {"carrier_frozen": False, "payout": 2000.0}
        assert "payment-risk analyst" in seen["system_prompt"]
        assert seen["temperature"] == 0.1

    def test_a_model_that_cannot_be_reached_is_unknown(self):
        complete, _ = model(RuntimeError("no route"))
        out = run(mod().analyst({}, complete))
        assert out["verdict"] == "unknown"
        assert "could not be reached" in out["reasons"][0]
        assert "RuntimeError" in out["reasons"][0]

    @pytest.mark.parametrize("text", ["", "not json", "[1, 2]", '{"verdict": "fine"}'])
    def test_a_reply_that_cannot_be_read_is_unknown(self, text):
        complete, _ = model(text)
        assert run(mod().analyst({}, complete))["verdict"] == "unknown"

    def test_a_reply_wrapped_in_prose_is_still_read(self):
        complete, _ = model("Here is my answer:\n" + reply(verdict="normal"))
        assert run(mod().analyst({}, complete))["verdict"] == "normal"


def search(results, status="ok"):
    seen = {}

    async def find(query, *args, **kwargs):
        seen["query"] = query
        if isinstance(results, Exception):
            raise results
        return results, status

    return find, seen


class TestTheCarrierLookUp:
    def test_a_result_that_names_the_carrier_finds_it(self):
        find, seen = search([
            {"title": "Mekong Haulage Co - trucking Vietnam", "content": "Cross-border freight", "url": "https://mekong.example/about"},
            {"title": "Unrelated", "content": "Something else", "url": "https://other.example"},
        ])
        out = run(mod().identity("Mekong Haulage Co", find))
        assert out["status"] == "found"
        assert out["urls"] == ["https://mekong.example/about"]
        assert '"Mekong Haulage Co"' in seen["query"]

    def test_the_words_that_make_it_that_carrier_must_all_appear(self):
        find, _ = search([{"title": "Mekong Freight Services", "content": "no relation", "url": "https://x.example"}])
        assert run(mod().identity("Mekong Haulage Zebra Co", find))["status"] == "not_found"

    def test_generic_words_alone_do_not_find_a_carrier(self):
        find, _ = search([{"title": "Global Logistics Transport Company", "content": "freight", "url": "https://x.example"}])
        assert run(mod().identity("Quang Ninh Logistics Co", find))["status"] == "not_found"

    def test_a_search_that_ran_and_found_nothing_is_not_found(self):
        find, _ = search([])
        out = run(mod().identity("Mekong Haulage Co", find))
        assert out["status"] == "not_found"
        assert out["urls"] == []

    @pytest.mark.parametrize("status", ["no_api_key", "timeout", "rate_limited", "http_error", "transport_error", "bad_response"])
    def test_a_search_that_did_not_run_is_unverified_whatever_it_returned(self, status):
        find, _ = search([{"title": "Mekong Haulage Co", "content": "x", "url": "https://x.example"}], status)
        out = run(mod().identity("Mekong Haulage Co", find))
        assert out["status"] == "unverified"
        assert status in out["reason"]
        assert "not evidence" in out["reason"]

    def test_a_search_that_raises_is_unverified(self):
        find, _ = search(RuntimeError("down"))
        assert run(mod().identity("Mekong Haulage Co", find))["status"] == "unverified"

    @pytest.mark.parametrize("name", [None, "", "   "])
    def test_no_name_is_unverified(self, name):
        find, seen = search([])
        assert run(mod().identity(name, find))["status"] == "unverified"
        assert "query" not in seen


class TestAssessing:
    def test_both_answers_are_returned(self):
        complete, _ = model(reply())
        find, _ = search([{"title": "Mekong Haulage Co", "content": "freight", "url": "https://m.example"}])
        out = run(mod().assess(shipment(), None, complete, find))
        assert out["analyst"]["verdict"] == "elevated"
        assert out["identity"]["status"] == "found"

    def test_it_never_raises_when_everything_fails(self, monkeypatch):
        async def broken(*args, **kwargs):
            raise RuntimeError("store down")

        monkeypatch.setattr(mod().credit_profile, "history", broken)
        complete, _ = model(RuntimeError("no route"))
        find, _ = search(RuntimeError("down"))
        out = run(mod().assess(shipment(), None, complete, find))
        assert out["analyst"]["verdict"] == "unknown"
        assert out["identity"]["status"] == "unverified"
