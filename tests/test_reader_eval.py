"""
What the reader adds, measured as its own arm (payment_eval.py, scripts/evaluate_payment_ai.py).

Pinned here: without readings the arm is not run, never zero, and the report says how to run it; readings are credited with what they
add and charged for what they flag wrongly; a measurement and the /defense table call no model; and a live run in which no reading
came back stops instead of reporting that the reader added nothing.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib

import pytest

from vf_logistics import change_screen, defense_scenarios, payment_eval

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOLDOUT = ROOT / "data" / "payment_fraud_holdout.json"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def holdout():
    return payment_eval.load_corpus(HOLDOUT)


def readings(corpus, flagged):
    return {
        c["id"]: {"state": "flagged" if c["scenario"] in flagged else "clear", "reasons": [], "tokens": {"in": 1, "out": 1}}
        for c in corpus["cases"] if c.get("change_request_text")
    }


def script():
    spec = importlib.util.spec_from_file_location("evaluate_payment_ai", ROOT / "scripts" / "evaluate_payment_ai.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def args(**over):
    base = {"live_analyst": False, "replay": None, "save": None, "corpus": str(HOLDOUT),
            "live_reader": False, "replay_reader": None, "save_reader": None}
    base.update(over)
    return type("Args", (), base)


def test_without_readings_the_arm_is_not_run_and_the_report_says_how():
    summary = payment_eval.summarize(run(payment_eval.run(payment_eval.load_corpus())))
    assert summary["arms"]["reader"] is None
    assert summary["added"]["reader"] is None
    assert "--live-reader" in payment_eval.report(summary)


def test_readings_are_credited_with_what_they_catch(holdout):
    rows = run(payment_eval.run(holdout, readers=readings(holdout, {"bec_paraphrase", "bec_vietnamese"})))
    summary = payment_eval.summarize(rows)
    assert summary["added"]["reader"] == {"true_flags": 10, "false_flags": 0}
    assert summary["arms"]["reader"]["recall"]["count"] == summary["arms"]["site"]["recall"]["count"] + 10


def test_readings_are_charged_for_what_they_flag_wrongly(holdout):
    rows = run(payment_eval.run(holdout, readers=readings(holdout, {"honest_vietnamese_change"})))
    assert payment_eval.summarize(rows)["added"]["reader"] == {"true_flags": 0, "false_flags": 6}


def test_a_case_with_no_request_text_is_measured_all_the_same(holdout):
    rows = run(payment_eval.run(holdout, readers={}))
    assert all(row["floors"]["reader"] == row["floors"]["site"] for row in rows)


def test_a_measurement_and_the_defense_table_call_no_model(holdout, monkeypatch):
    calls = []

    async def complete(**kwargs):
        calls.append(kwargs)
        return "{}", 0, 0

    monkeypatch.setattr(change_screen.nebius_client, "complete_json", complete)
    run(payment_eval.run(holdout))
    run(defense_scenarios.run())
    assert calls == []


def test_the_script_replays_saved_readings(holdout, tmp_path):
    path = tmp_path / "readers.json"
    path.write_text(json.dumps(readings(holdout, {"bec_vietnamese"})), encoding="utf-8")
    text = run(script().main(args(replay_reader=str(path))))
    assert "+ model reading the change request added 4 true and 0 false flag(s)." in text


def test_a_live_run_in_which_no_reading_came_back_stops(monkeypatch):
    async def down(text, complete=None):
        return {"state": "unavailable", "reasons": [], "tokens": {"in": 0, "out": 0}}

    monkeypatch.setattr(change_screen, "read", down)
    with pytest.raises(SystemExit) as stopped:
        run(script().main(args(live_reader=True)))
    assert "could not be reached" in str(stopped.value)
