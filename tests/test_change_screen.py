"""
The screen for a payee-change request (change_screen.py).

Pinned here: the wording business-email-compromise messages lean on is flagged with a plain
reason (do not call, urgency, secrecy, a closed old account, a senior person's instruction);
an ordinary change request is not; Model Armor flagging the text is a flag, Model Armor
saying clean or being unreachable is not (and removes nothing); empty text is not screened;
and the screen never raises.

change_screen is imported inside each test so that, before it exists, every test fails
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest


def run(coro):
    return asyncio.run(coro)


def screen(text, armor=None):
    module = importlib.import_module("vf_logistics.change_screen")
    return run(module.screen(text, armor or clean))


async def clean(text, stage):
    return {"available": True, "blocked": False, "deterministic": None}


@pytest.mark.parametrize("text, reason", [
    ("Please do not call us to confirm, our phone line is down.", "asks not to be called back"),
    ("Don't verify this with anyone, just update it.", "asks not to be called back"),
    ("This is urgent, pay before the end of the day.", "pressure to act at once"),
    ("Please act immediately.", "pressure to act at once"),
    ("This is confidential, do not share it with the rest of your team.", "asks to keep it confidential"),
    ("Our old account is closed after an audit.", "says the old account is closed"),
    ("The CEO asked me to send the new account to you.", "invokes a senior person's instruction"),
])
def test_wording_that_business_email_compromise_leans_on_is_flagged_with_a_reason(text, reason):
    out = screen(text)
    assert out["flagged"] is True
    assert reason in out["reasons"]


def test_an_ordinary_change_request_is_not_flagged():
    out = screen("Hello, from the first of next month our payments should go to the account of our new finance "
                 "office. The details are in the attached letter on our letterhead. Regards, Mekong Haulage.")
    assert out == {"flagged": False, "reasons": [], "armor": "clean"}


def test_several_reasons_are_all_reported():
    out = screen("URGENT: do not call, this is confidential.")
    assert len(out["reasons"]) == 3


def test_model_armor_flagging_the_text_is_a_flag():
    async def flagged(text, stage):
        return {"available": True, "blocked": True, "deterministic": None}

    out = screen("Hello, please update our records.", flagged)
    assert out["flagged"] is True
    assert out["reasons"] == ["Model Armor flagged the text"]
    assert out["armor"] == "flagged"


def test_model_armor_matching_a_pattern_is_a_flag():
    async def patterns(text, stage):
        return {"available": True, "blocked": False, "deterministic": {"matched": True, "types": ["x"]}}

    assert screen("Hello.", patterns)["flagged"] is True


def test_the_stage_model_armor_is_told_names_the_request():
    seen = []

    async def spy(text, stage):
        seen.append(stage)
        return {"available": True, "blocked": False, "deterministic": None}

    screen("Hello.", spy)
    assert seen == ["payee_change_request"]


def test_a_model_armor_that_cannot_be_reached_adds_no_flag_and_removes_none():
    async def down(text, stage):
        raise RuntimeError("no network")

    assert screen("Hello, please update our records.", down) == {"flagged": False, "reasons": [], "armor": "unavailable"}
    out = screen("Please do not call.", down)
    assert out["flagged"] is True
    assert out["armor"] == "unavailable"


def test_model_armor_being_unconfigured_is_not_a_flag():
    async def unconfigured(text, stage):
        return {"available": False, "blocked": False, "requires_human": True, "deterministic": None}

    out = screen("Hello.", unconfigured)
    assert out["flagged"] is False
    assert out["armor"] == "unavailable"


@pytest.mark.parametrize("text", [None, "", "   ", 0])
def test_empty_text_is_not_screened(text):
    assert screen(text) == {"flagged": False, "reasons": [], "armor": "not_screened"}
