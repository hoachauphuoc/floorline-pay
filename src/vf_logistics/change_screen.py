"""
A screen for the message that asks to change a carrier's payee.

Most payment diversion starts with a message that looks like it comes from the carrier: new
bank details, an urgent deadline, a request not to call to check. The registry already makes
a person other than the registrant call the carrier back before a payee counts. This screen
adds a second look at the words themselves, from two sources:

  - a short list of phrases that business-email-compromise messages lean on (new account,
    urgency, secrecy, do not call, pressure from an executive), matched here and always run
  - Model Armor, for text that tries to manipulate a model or matches its own patterns

A flagged request raises the risk (payment_checks holds the payout until a person registers
and verifies a payee again) and never lowers anything: a request that matches nothing is not
thereby shown to be genuine, and Model Armor being unreachable is not itself a flag. The
text of the request is not kept; the reasons are.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from vf_logistics import model_armor

_MAX_CHARS = 4000

# (reason shown to the reviewer, pattern). Plain wording, each one a thing a genuine carrier
# changing its bank has no need to say. "New bank details" is not here: every genuine change
# says it, so it would flag them all.
_PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("asks not to be called back", re.compile(
        r"\b(do not|don't|dont|no need to|avoid|rather than)\b.{0,30}\b(call|phone|verify|verification|confirm|confirmation)", re.I)),
    ("pressure to act at once", re.compile(
        r"\b(urgent|urgently|immediately|asap|today only|right away|before (the )?end of (the )?day)\b", re.I)),
    ("asks to keep it confidential", re.compile(r"\b(confidential|secret|between us|do not (share|tell|forward))\b", re.I)),
    ("says the old account is closed", re.compile(
        r"\b(old|previous|current)\b.{0,15}\b(account|bank)\b.{0,25}\b(closed|closing|frozen|audit|no longer)\b", re.I)),
    ("invokes a senior person's instruction", re.compile(
        r"\b(ceo|cfo|director|managing director|owner|boss)\b.{0,30}\b(asked|instructed|requested|wants|approved)\b", re.I)),
)

Armor = Callable[[str, str], Awaitable[dict[str, Any]]]


async def screen(text: Any, armor: Armor | None = None) -> dict[str, Any]:
    """Screen the text of a payee-change request. Never raises; unreadable input flags nothing."""
    words = str(text or "").strip()[:_MAX_CHARS]
    if not words:
        return {"flagged": False, "reasons": [], "armor": "not_screened"}
    reasons = [reason for reason, pattern in _PHRASES if pattern.search(words)]
    state = "unavailable"
    try:
        verdict = await (armor or model_armor.screen)(words, "payee_change_request")
        deterministic = verdict.get("deterministic")
        if verdict.get("blocked") or (isinstance(deterministic, dict) and deterministic.get("matched")):
            reasons.append("Model Armor flagged the text")
            state = "flagged"
        elif verdict.get("available"):
            state = "clean"
    except Exception:  # noqa: BLE001 - an unreachable screen adds no flag and removes none
        state = "unavailable"
    return {"flagged": bool(reasons), "reasons": reasons, "armor": state}
