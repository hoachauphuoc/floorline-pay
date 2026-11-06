"""
A live measurement of the analyst in which the model could not be reached (scripts/evaluate_payment_ai.py).

Pinned here: with no verdict back the run stops and saves nothing, because a file of unanswered cases replays as an analyst that added
nothing, which nobody measured; one verdict is enough for the run to go on and be saved.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib

import pytest

from vf_logistics import payment_ai

ROOT = pathlib.Path(__file__).resolve().parent.parent
UNREACHED = {"verdict": "unknown", "confidence": 0.0, "reasons": ["the analyst could not be reached (OpenAIError)"], "suggested_tier": None}
NORMAL = {"verdict": "normal", "confidence": 0.9, "reasons": [], "suggested_tier": None}


def script():
    spec = importlib.util.spec_from_file_location("evaluate_payment_ai", ROOT / "scripts" / "evaluate_payment_ai.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def args(saved):
    class Args:
        live_analyst = True
        replay = None
        save = str(saved)

    return Args()


def test_a_live_run_in_which_no_verdict_came_back_stops_and_saves_nothing(monkeypatch, tmp_path):
    async def down(feature_set, complete=None):
        return UNREACHED

    monkeypatch.setattr(payment_ai, "analyst", down)
    saved = tmp_path / "analyst.json"
    with pytest.raises(SystemExit) as stopped:
        asyncio.run(script().main(args(saved)))
    assert "no verdict came back" in str(stopped.value)
    assert "NEBIUS_API_KEY" in str(stopped.value)
    assert not saved.exists()


def test_one_verdict_is_enough_for_the_run_to_be_measured_and_saved(monkeypatch, tmp_path):
    answers = iter([NORMAL])

    async def mostly_down(feature_set, complete=None):
        return next(answers, UNREACHED)

    monkeypatch.setattr(payment_ai, "analyst", mostly_down)
    saved = tmp_path / "analyst.json"
    text = asyncio.run(script().main(args(saved)))
    assert text.startswith("SYNTHETIC CORPUS")
    assert len(json.loads(saved.read_text(encoding="utf-8"))) == 80
