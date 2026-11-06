"""
A decision the settler already settled or voided, delivered again (settler.py).

Pub/Sub delivers a decision again when its acknowledgement was lost. The settler ran every check again, and the payout it had
already made counted against the limits it was checked against: at a limit, or while payments were paused or the carrier
frozen, a paid payment came back held or retry and a newer row hid the payment. The answer stored for the key is the answer.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_settler_controls import OPERATOR, carrier, decision, mod, pp, run  # noqa: E402, F401


def test_a_settled_decision_delivered_again_while_payments_are_paused_answers_settled(pp):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(carrier())
    assert run(settler.settle(d))["status"] == "settled"
    run(mod("payment_controls").pause("a carrier was phished", OPERATOR, "password"))
    again = run(settler.settle(d))
    assert again["status"] == "settled"
    assert "captured and paid" in again["reason"]
    capture.assert_awaited_once()
    payout.assert_awaited_once()


def test_a_voided_block_delivered_again_does_not_void_twice(pp):  # noqa: F811
    settler, capture, payout, void = pp
    d = decision(carrier(), outcome="BLOCKED_BY_HUMAN")
    assert run(settler.settle(d))["status"] == "voided"
    assert run(settler.settle(d))["status"] == "voided"
    void.assert_awaited_once()


def test_a_decision_not_settled_before_still_meets_the_pause(pp):  # noqa: F811
    settler, capture, payout, void = pp
    run(mod("payment_controls").pause("a carrier was phished", OPERATOR, "password"))
    result = run(settler.settle(decision(carrier())))
    assert result["status"] == "retry"
    assert "payments are paused" in result["reason"]
    capture.assert_not_called()
