"""
The payee against the carrier's own website (payment_ai.identity, payment_checks.check_payment_ai).

The look-up says "found" when the real company exists, so an impersonator using its name passed. Pinned here: a payee off the site
the search found is a finding; a payee on it, or on a subdomain of it, is not; a directory page names the carrier but is not its site,
so with no site nothing is claimed; a search that did not run claims nothing about the payee; a payee with three confirmed payouts
and no change is not checked, but a changed one or an unreadable history is; the finding never quotes the address.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from vf_logistics import payment_ai, payment_checks

SITE = {"title": "Mekong Haulage Co | freight", "content": "Road freight", "url": "https://www.mekong-haulage.example/about"}
LISTING = {"title": "Mekong Haulage Co - listing", "content": "Listed carrier", "url": "https://freight-listings.example/c/mekong"}
FREE_MAIL = "mekong.accounts@gmail.com"


def run(coro):
    return asyncio.run(coro)


def found(results, status="ok"):
    async def find(query, *args, **kwargs):
        return results, status

    return find


def look(results, payee, status="ok"):
    return run(payment_ai.identity("Mekong Haulage Co", found(results, status), payee))


def shipment(**over):
    base = {"carrier_name": "Mekong Haulage Co", "payout_amount": 2000.0, "shipping_cost": 2100.0, "carrier_payee_email": FREE_MAIL}
    base.update(over)
    return base


class TestTheComparison:
    def test_a_payee_off_the_site_the_search_found_is_off_site(self):
        assert look([SITE], FREE_MAIL)["payee_site"] == "off_site"

    def test_the_site_is_found_behind_a_directory_page(self):
        assert look([LISTING, SITE], FREE_MAIL)["payee_site"] == "off_site"

    def test_a_look_alike_domain_is_off_site(self):
        assert look([SITE], "pay@mekonghaulage-billing.example")["payee_site"] == "off_site"

    @pytest.mark.parametrize("payee", ["pay@mekong-haulage.example", "pay@billing.mekong-haulage.example", "PAY@Mekong-Haulage.example "])
    def test_a_payee_on_the_site_or_a_subdomain_of_it_is_on_site(self, payee):
        assert look([SITE], payee)["payee_site"] == "on_site"

    def test_a_directory_page_alone_claims_nothing(self):
        assert look([LISTING], FREE_MAIL)["payee_site"] == "unknown"

    @pytest.mark.parametrize("payee", [None, "", "not-an-address"])
    def test_no_readable_payee_claims_nothing(self, payee):
        assert look([SITE], payee)["payee_site"] == "unknown"

    def test_a_legal_form_is_not_needed_in_the_domain(self):
        result = {"title": "Saigon Plywood JSC", "content": "x", "url": "https://saigonplywood.example/"}
        out = run(payment_ai.identity("Saigon Plywood JSC", found([result]), FREE_MAIL))
        assert out["payee_site"] == "off_site"

    def test_a_url_that_cannot_be_read_is_not_a_site_and_does_not_raise(self):
        broken = {**SITE, "url": "http://[mekong-haulage"}
        assert look([broken], FREE_MAIL)["payee_site"] == "unknown"

    def test_a_search_that_did_not_run_claims_nothing_about_the_payee(self):
        out = look([SITE], FREE_MAIL, "timeout")
        assert out["status"] == "unverified"
        assert out.get("payee_site") != "off_site"

    def test_the_payee_address_is_not_kept_in_the_answer(self):
        assert FREE_MAIL not in json.dumps(look([SITE], FREE_MAIL))


class TestAssessing:
    def assessed(self, monkeypatch, history, **over):
        async def read(carrier, tenant_id=None):
            if isinstance(history, Exception):
                raise history
            return history

        async def complete(**kwargs):
            return '{"verdict": "normal", "confidence": 0.1, "reasons": []}', 1, 1

        monkeypatch.setattr(payment_ai.credit_profile, "history", read)
        return run(payment_ai.assess(shipment(**over), None, complete, found([SITE])))["identity"]

    def test_a_new_carrier_is_checked(self, monkeypatch):
        assert self.assessed(monkeypatch, {"paid_count": 0})["payee_site"] == "off_site"

    def test_a_payee_with_three_confirmed_payouts_and_no_change_is_not_checked(self, monkeypatch):
        assert self.assessed(monkeypatch, {"paid_count": 3})["payee_site"] == "unknown"

    def test_a_changed_payee_is_checked_however_long_the_history(self, monkeypatch):
        out = self.assessed(monkeypatch, {"paid_count": 9}, carrier_payee_changed_at="2026-09-01T00:00:00+00:00")
        assert out["payee_site"] == "off_site"

    def test_a_history_that_cannot_be_read_does_not_prove_the_payee(self, monkeypatch):
        assert self.assessed(monkeypatch, RuntimeError("store down"))["payee_site"] == "off_site"


class TestTheFinding:
    def codes(self, identity):
        return [f["code"] for f in payment_checks.check_payment_ai(shipment(), {"identity": identity})]

    def test_off_site_is_a_finding_that_says_what_to_do_and_quotes_no_address(self):
        findings = payment_checks.check_payment_ai(shipment(), {"identity": {"status": "found", "payee_site": "off_site"}})
        assert [f["code"] for f in findings] == ["PAYEE_OFF_CARRIER_SITE"]
        assert findings[0]["floor"] == 55
        assert findings[0]["blocks_auto_clear"] is True
        assert "number taken from that website" in findings[0]["detail"]
        assert "@" not in findings[0]["detail"]

    @pytest.mark.parametrize("value", ["on_site", "unknown", None])
    def test_anything_else_adds_nothing(self, value):
        assert self.codes({"status": "found", "payee_site": value}) == []
