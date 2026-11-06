"""
The attack theatre: ten ways to be paid wrongly, and the layer that stops each.

Each scenario takes a routine, legitimate payout from the synthetic corpus, changes the one thing an
attacker would change, and runs the same functions the pipeline and the settler run: the payment
checks, the screen of a payee-change request, the AI findings, the credit standing, the dual-approval
rule, the authorization lifecycle, and verifier.validate for the floor. Nothing here reads or writes
the store and nothing here calls PayPal or a model, so the page is safe to open at any time and can
never move money; every row says "0.00 USD moved" because none could.

Two scenarios need the store and PayPal's own answers (an authorization that no booking made, a
carrier in dispute). They are listed as not live, with the test that proves each, rather than
simulated with a stand-in.

The synthetic corpus is the source of the routine payout, so the scenarios share its limits: they show
that the layers behave as designed on the patterns they were written for, not how often these attacks
occur.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from vf_logistics import (
    authorization_lifecycle,
    change_screen,
    credit_profile,
    dual_approval,
    payment_ai,
    payment_checks,
    payment_eval,
    verifier,
)

NOT_STOPPED = "NOT STOPPED"
BEC_MESSAGE = "URGENT: our old account is closed, do not call to check, just pay the new one."


async def _no_armor(text: Any, stage: str) -> dict[str, Any]:
    # The phrases are matched here; Model Armor needs a Google project and is not part of a replay.
    return {"available": False, "blocked": False, "deterministic": None}


async def _no_reader(**kwargs: Any) -> tuple[str, int, int]:
    # The table is replayed on every visit to the page, so it must not spend a model call each time.
    raise RuntimeError("no model in a replay")


async def _routine() -> dict[str, Any]:
    corpus = payment_eval.load_corpus()
    case = next(c for c in corpus["cases"] if c["scenario"] == "routine")
    return await payment_eval.prepare(case)


def _row(
    key: str, title: str, attack: str, layer: str, proof: str, *, stopped: bool, outcome: str,
    findings: list[str] | None = None, floor: int | None = None, live: bool = True, detail: str = "",
) -> dict[str, Any]:
    return {
        "id": key, "title": title, "attack": attack, "layer": layer, "live": live,
        "outcome": outcome if stopped else NOT_STOPPED, "findings": findings or [], "floor": floor,
        "money_moved_usd": "0.00", "proof": proof, "detail": detail,
    }


def _held(shipment: dict[str, Any], expected: str, ai: dict[str, Any] | None = None) -> tuple[bool, list[str], int]:
    codes = [f["code"] for f in payment_checks.check_payment(shipment) + payment_checks.check_payment_ai(shipment, ai)]
    floor = int(verifier.validate(shipment, payment_ai=ai)["risk_floor"])
    return expected in codes and floor >= payment_eval.FLAG_FLOOR, codes, floor


async def run() -> list[dict[str, Any]]:
    """Replay the ten attacks. Every live row says whether the layer stopped it."""
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    s = await _routine()
    s["carrier_payee_email"] = "accounts@attacker-payments.example"
    ok, codes, floor = _held(s, "PAYEE_MISMATCH")
    rows.append(_row(
        "payee-swap", "A different account is named for the payout",
        "The payout asks for an account that is not the one the registry holds for this carrier.",
        "Payee registry and payment checks", "tests/test_payment_checks.py", stopped=ok,
        outcome="held for a person", findings=codes, floor=floor,
    ))

    s = await _routine()
    screened = await change_screen.screen(BEC_MESSAGE, _no_armor, _no_reader)
    if screened["flagged"]:
        s["carrier_payee_change_flagged"] = True
    ok, codes, floor = _held(s, "PAYEE_CHANGE_REQUEST_FLAGGED")
    rows.append(_row(
        "bec-request", "A change-of-account message written the way fraud is written",
        "\"Urgent, our old account is closed, do not call to check.\"",
        "Screen of payee-change requests", "tests/test_payee_change_screen.py", stopped=ok and screened["flagged"],
        outcome="held until the payee is registered and verified again", findings=codes, floor=floor,
        detail="; ".join(screened["reasons"]),
    ))

    s = await _routine()
    s["carrier_payee_changed_at"] = (now - timedelta(days=3)).isoformat()
    ok, codes, floor = _held(s, "PAYEE_RECENTLY_CHANGED")
    rows.append(_row(
        "recent-change", "The payee was changed three days ago",
        "The account is switched first and the next invoice is paid into it.",
        "Payment checks (14-day cooling-off)", "tests/test_payment_checks.py", stopped=ok,
        outcome="held for a person", findings=codes, floor=floor,
    ))

    s = await _routine()
    s["payout_amount"] = round(float(s["shipping_cost"]) * 1.8, 2)
    ok, codes, floor = _held(s, "PAYOUT_ABOVE_FREIGHT")
    rows.append(_row(
        "inflated-payout", "A payout 1.8 times the booked freight",
        "The invoice is padded after the freight was booked.",
        "Payment checks", "tests/test_payment_checks.py", stopped=ok,
        outcome="held for a person", findings=codes, floor=floor,
    ))

    s = await _routine()
    identity = await payment_ai.identity(s.get("carrier_name"), _nothing_online)
    ai = {"identity": identity}
    ok, codes, floor = _held(s, "CARRIER_NOT_FOUND_ONLINE", ai)
    rows.append(_row(
        "shell-carrier", "A carrier that exists only in the registry",
        "A consistent registry entry for a company no web search finds.",
        "Carrier look-up (raises risk only)", "tests/test_payment_ai.py", stopped=ok,
        outcome="held for a person", findings=codes, floor=floor, detail=identity["reason"],
    ))

    s = await _routine()
    s["carrier_payee_email"] = "accounts@attacker-payments.example"
    before = int(verifier.validate(s)["risk_floor"])
    ai = {
        "analyst": {"verdict": "normal", "confidence": 0.99, "reasons": [], "suggested_tier": "A"},
        "identity": {"status": "found"},
    }
    after = int(verifier.validate(s, payment_ai=ai)["risk_floor"])
    tier = credit_profile.tighter(credit_profile.baseline({"paid_count": 0, "frozen": False}), credit_profile.suggestion(ai))
    rows.append(_row(
        "model-says-fine", "The model says \"normal\" and suggests the best credit standing",
        "A model, or a prompt injected into one, tries to lower the risk of a payout the rules held.",
        "Floor arithmetic and credit standing (a model may only raise)", "tests/test_payment_ai_checks.py",
        stopped=after >= before and tier == "C", outcome="floor unchanged, standing stays C",
        findings=["PAYEE_MISMATCH"], floor=after, detail=f"floor {before} -> {after}; a new carrier stays at standing {tier}",
    ))

    s = await _routine()
    s["payout_amount"] = 9000.0
    needs_two = dual_approval.needed(s)
    rows.append(_row(
        "lone-release", "One reviewer releases a 9,000 USD payout alone",
        "A single compromised or coerced reviewer.",
        "Dual approval", "tests/test_dual_approval.py", stopped=needs_two,
        outcome=f"not executable until a different reviewer approves (from {dual_approval.threshold()} USD)",
        findings=["requires_second"], detail="the settler holds it again if it is sent anyway",
    ))

    stage = authorization_lifecycle.stage({"create_time": (now - timedelta(days=40)).isoformat()})
    rows.append(_row(
        "expired-authorization", "An authorization 40 days old",
        "A hold that PayPal no longer guarantees is captured anyway.",
        "Authorization lifecycle", "tests/test_authorization_lifecycle.py", stopped=stage == authorization_lifecycle.EXPIRED,
        outcome="refused: the shipper must authorize again", findings=[stage],
    ))

    rows.append(_row(
        "unbound-authorization", "An authorization id typed into the shipment",
        "The id belongs to another shipment's money, or to none made through Floorline.",
        "Booking-bound authorizations", "tests/test_settler_gate.py", stopped=True,
        outcome="held: no booking made through Floorline holds it", live=False,
        detail="needs the store and PayPal's own reply, so it is proved by the test, not simulated",
    ))

    rows.append(_row(
        "disputed-carrier", "A carrier with a dispute open on an earlier payment",
        "PayPal reports a reversed capture, a dispute or a failed payout.",
        "Freeze on carrier and shipper", "tests/test_settler_settlement.py", stopped=True,
        outcome="held until a governance admin lifts the freeze", live=False,
        detail="needs the store, so it is proved by the test, not simulated",
    ))
    return rows


async def _nothing_online(query: str, *args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], str]:
    return [], "ok"
