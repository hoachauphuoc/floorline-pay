"""
A PayPal answer that is not JSON (settler.py, paypal_client.py).

An HTML error page from PayPal or its edge makes paypal_client answer "bad_response". The settler took it for PayPal's final
word: the decision was acknowledged, a block's void never ran again, and a release whose capture reply was garbled paid the
carrier nothing. Both are retried; the PayPal request ids make the repeat safe, and a retry moves no money by itself.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_settler import decision, new_case, pp, run  # noqa: E402, F401

BAD_RESPONSE = {"status": "bad_response", "http_status": None, "body": None, "error": "reply is not JSON"}
REFUSED = {"status": "http_error", "http_status": 422, "body": {"name": "NO"}, "error": "NO"}


def blocked():
    return decision(new_case(state="BLOCKED_BY_HUMAN"), outcome="BLOCKED_BY_HUMAN")


def test_a_block_whose_void_answer_is_not_json_is_retried(pp):  # noqa: F811
    settler, capture, payout, void = pp
    void.return_value = BAD_RESPONSE
    assert run(settler.settle(blocked()))["status"] == "retry"


def test_a_capture_whose_answer_is_not_json_is_retried_and_nothing_is_paid(pp):  # noqa: F811
    settler, capture, payout, void = pp
    capture.return_value = BAD_RESPONSE
    assert run(settler.settle(decision(new_case())))["status"] == "retry"
    payout.assert_not_called()


def test_a_final_refusal_still_fails(pp):  # noqa: F811
    settler, capture, payout, void = pp
    void.return_value = REFUSED
    assert run(settler.settle(blocked()))["status"] == "failed"
