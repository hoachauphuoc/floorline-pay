"""
The case-decisions receiver (decision_receiver.py), two gaps found by the API review of 25 Oct 2026.

1. A repeat of a key that carried a different case or outcome was acknowledged as a duplicate, yet the settler ran on what
   the repeat said. The key's first application is the decision: the settler now gets the stored record.
2. A push whose JSON is not an object, or whose data is not a string, raised and answered 500. It is refused with 422, like
   any other message that is not a decision, and nothing is settled.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_decision_receiver import envelope  # noqa: E402
from vf_logistics import decision_receiver  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def key():
    return f"exec-{uuid.uuid4().hex[:8]}"


def settled():
    return AsyncMock(return_value={"status": "settled", "reason": "captured and paid"})


class TestARepeatSettlesTheFirstDecision:
    def test_a_repeat_carrying_another_case_and_outcome_settles_the_stored_one(self):
        k = key()
        first = {"case_id": "CASE-FIRST", "outcome": "BLOCKED_BY_HUMAN", "approval_id": "apr-1"}
        other = {"case_id": "CASE-OTHER", "outcome": "RELEASED_BY_HUMAN", "approval_id": "apr-9"}
        with patch.object(decision_receiver.settler, "settle", settled()) as settle:
            run(decision_receiver.receive(envelope(key=k, message_id="m-1", data=first)))
            status, reply = run(decision_receiver.receive(envelope(key=k, message_id="m-2", data=other)))
        assert status == 200
        assert reply["outcome"] == "duplicate"
        sent = settle.await_args_list[-1].args[0]
        assert sent["case_id"] == "CASE-FIRST"
        assert sent["outcome"] == "BLOCKED_BY_HUMAN"
        assert sent["approval_id"] == "apr-1"
        assert sent["idempotency_key"] == k

    def test_a_first_delivery_settles_what_it_carries(self):
        k = key()
        body = {"case_id": "CASE-ONE", "outcome": "RELEASED_BY_HUMAN", "approval_id": "apr-1"}
        with patch.object(decision_receiver.settler, "settle", settled()) as settle:
            status, reply = run(decision_receiver.receive(envelope(key=k, data=body)))
        assert (status, reply["outcome"]) == (200, "applied")
        sent = settle.await_args.args[0]
        assert (sent["case_id"], sent["outcome"], sent["approval_id"], sent["idempotency_key"]) == (
            "CASE-ONE", "RELEASED_BY_HUMAN", "apr-1", k)


class TestAPushThatIsNotAnEnvelope:
    @pytest.mark.parametrize("payload", [[1, 2], "message", 7])
    def test_a_push_that_is_not_a_json_object_is_refused_with_422(self, payload):
        with patch.object(decision_receiver.settler, "settle", settled()) as settle:
            status, reply = run(decision_receiver.receive(payload))
        assert status == 422
        assert reply["outcome"] == "rejected"
        assert "Pub/Sub message" in reply["reason"]
        settle.assert_not_called()

    @pytest.mark.parametrize("data", [123, ["x"], {"a": 1}])
    def test_data_that_is_not_a_string_is_refused_with_422(self, data):
        payload = envelope(key=key())
        payload["message"]["data"] = data
        with patch.object(decision_receiver.settler, "settle", settled()) as settle:
            status, reply = run(decision_receiver.receive(payload))
        assert status == 422
        assert reply["outcome"] == "rejected"
        assert "case_id" in reply["reason"]
        settle.assert_not_called()
