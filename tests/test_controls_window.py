"""
The pause switch and the published limits are read past the newest page (payment_controls._newest).

The controls rows of a tenant are read one page at a time and the action is picked in code. A pause older than one page
fell outside it, so pause_state answered "not paused": the switch failed open. It now reads on, and when it cannot reach the
end within the pages it may read it fails with a 503 instead of guessing.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

from vf_logistics import store as store_mod


def run(coro):
    return asyncio.run(coro)


def controls():
    return importlib.import_module("vf_logistics.payment_controls")


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch):
    monkeypatch.setattr(store_mod, "_store", None)


def pause_then_publish(times):
    c = controls()
    run(c.pause("a carrier was phished", "operator@forwarder.example", "password"))
    for _ in range(times):
        run(c.publish_limits(dict(c.DEFAULT_LIMITS), "tuning", "admin@forwarder.example", "password"))


def test_a_pause_older_than_one_page_is_still_found(monkeypatch):
    pause_then_publish(3)
    monkeypatch.setattr(controls(), "_PAGE", 2)
    state = run(controls().pause_state())
    assert state["paused"] is True
    assert state["reason"] == "a carrier was phished"


def test_rows_that_cannot_be_read_to_the_end_fail_with_a_503(monkeypatch):
    pause_then_publish(3)
    monkeypatch.setattr(controls(), "_PAGE", 1)
    monkeypatch.setattr(controls(), "_PAGES", 2)
    with pytest.raises(controls().ControlsError) as refused:
        run(controls().pause_state())
    assert refused.value.status == 503
    assert "payments are held" in str(refused.value)


def test_a_pause_among_the_newest_rows_is_found_as_before(monkeypatch):
    pause_then_publish(0)
    monkeypatch.setattr(controls(), "_PAGE", 2)
    assert run(controls().pause_state())["paused"] is True
