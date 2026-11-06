"""
Floorline's read-only MCP server (mcp_server.py).

Pinned here: the server offers exactly the five tools and none of them can move money, change a freeze or pause payments; each
reading tool makes one GET to its own path under /api/v1 with the key in the header and nothing else; a case id that could change the
path is refused before any request; a missing address or key, and any answer that is not a 200, are errors that say so; and the
local payout check needs no network.

The module is looked up inside each fixture so that, before it exists, every test fails instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib

import httpx
import pytest

_RealAsyncClient = httpx.AsyncClient

TOOLS = {"check_payout_risk", "get_payment_state", "get_payment_risk", "list_frozen_parties", "replay_attacks"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def floorline(monkeypatch):
    module = importlib.import_module("vf_logistics.mcp_server")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"path": request.url.path})

    monkeypatch.setenv("FLOORLINE_API_URL", "https://floorline.example/")
    monkeypatch.setenv("FLOORLINE_API_KEY", "demo-key")
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: _RealAsyncClient(transport=httpx.MockTransport(handler), **kw))
    return module, calls


class TestTheTools:
    def test_the_server_offers_exactly_the_five_reading_tools(self, floorline):
        module, _ = floorline
        assert {tool.name for tool in run(module.server.list_tools())} == TOOLS

    def test_no_tool_can_move_money_or_change_a_freeze(self, floorline):
        module, _ = floorline
        names = {tool.name for tool in run(module.server.list_tools())}
        for word in ("capture", "void", "release", "approve", "lift", "pause", "resume", "book", "settle", "send", "execute"):
            assert not any(word in name for name in names), word

    @pytest.mark.parametrize("tool, args, path", [
        ("get_payment_state", ("CASE-1",), "/api/v1/payments/cases/CASE-1"),
        ("get_payment_risk", ("CASE-1",), "/api/v1/payments/cases/CASE-1/risk"),
        ("list_frozen_parties", (), "/api/v1/payments/freezes"),
        ("replay_attacks", (), "/api/v1/defense/scenarios"),
    ])
    def test_a_reading_tool_makes_one_get_to_its_own_path_with_the_key(self, floorline, tool, args, path):
        module, calls = floorline
        assert run(getattr(module, tool)(*args)) == {"path": path}
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].url.path == path
        assert calls[0].headers["X-VF-API-Key"] == "demo-key"

    @pytest.mark.parametrize("case_id", ["", "../x", "a/b", "a b", "x" * 81, "CASE-1?x=1"])
    def test_a_case_id_that_could_change_the_path_is_refused_before_any_request(self, floorline, case_id):
        module, calls = floorline
        with pytest.raises(module.FloorlineError) as raised:
            run(module.get_payment_state(case_id))
        assert "letters, digits" in str(raised.value)
        assert calls == []


class TestWhenFloorlineCannotAnswer:
    @pytest.mark.parametrize("missing", ["FLOORLINE_API_URL", "FLOORLINE_API_KEY"])
    def test_a_missing_address_or_key_is_an_error_that_names_both_settings(self, floorline, monkeypatch, missing):
        module, calls = floorline
        monkeypatch.delenv(missing)
        with pytest.raises(module.FloorlineError) as raised:
            run(module.list_frozen_parties())
        assert "FLOORLINE_API_URL and FLOORLINE_API_KEY" in str(raised.value)
        assert calls == []

    @pytest.mark.parametrize("status", [401, 403, 404, 500])
    def test_an_answer_that_is_not_a_200_is_an_error_with_its_status(self, floorline, monkeypatch, status):
        module, _ = floorline
        monkeypatch.setattr(
            module.httpx, "AsyncClient",
            lambda **kw: _RealAsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status)), **kw),
        )
        with pytest.raises(module.FloorlineError) as raised:
            run(module.list_frozen_parties())
        assert str(status) in str(raised.value)
        assert "nothing was changed" in str(raised.value)


class TestThePayoutCheck:
    def test_a_payee_that_is_not_the_registered_one_sets_a_floor_and_needs_no_network(self, floorline):
        module, calls = floorline
        out = module.check_payout_risk({
            "payout_amount": 900, "shipping_cost": 900, "paypal_authorization_id": "AUTH-1",
            "carrier_payee_email": "new@carrier.example", "carrier_registered_payee_email": "old@carrier.example",
        })
        assert [f["code"] for f in out["findings"]] == ["PAYEE_MISMATCH"]
        assert out["risk_floor"] == 80
        assert calls == []

    def test_a_shipment_that_moves_no_money_has_no_findings_and_no_floor(self, floorline):
        module, _ = floorline
        assert module.check_payout_risk({"shipment_id": "SHP-1"}) == {"findings": [], "risk_floor": 0}
