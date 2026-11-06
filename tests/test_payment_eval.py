"""
The synthetic payment corpus and its evaluation (payment_eval.py, scripts/build_payment_corpus.py,
scripts/evaluate_payment_ai.py).

Pinned here: the corpus declares itself synthetic, is exactly what the builder produces, holds 36
fraudulent and 44 legitimate invented payouts and nothing that looks like a real address; on it the
deterministic checks catch 24 of 36 with no false flag, the carrier look-up takes that to 32 of 36
at the price of 6 false flags, and the analyst arm is NOT RUN without verdicts, never zero; an
analyst that is supplied is measured as what it added, including when it added nothing and when
it only added false flags; an arm that lowers a floor stops the run; and the report says
synthetic before it says anything else.

payment_eval is imported inside each test so that, before it exists, every test fails instead of
the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def ev():
    return importlib.import_module("vf_logistics.payment_eval")


def builder():
    spec = importlib.util.spec_from_file_location("build_payment_corpus", ROOT / "scripts" / "build_payment_corpus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def corpus():
    return ev().load_corpus()


@pytest.fixture(scope="module")
def rows(corpus):
    return run(ev().run(corpus))


def verdicts(corpus, elevated):
    """An analyst verdict for every case: elevated for the scenarios named, normal for the rest."""
    return {
        c["id"]: {"verdict": "elevated" if c["scenario"] in elevated else "normal", "confidence": 0.9,
                  "reasons": ["synthetic"], "suggested_tier": None}
        for c in corpus["cases"]
    }


class TestTheCorpus:
    def test_it_declares_itself_synthetic_and_is_what_the_builder_produces(self, corpus):
        assert corpus["synthetic"] is True
        assert json.loads((ROOT / "data" / "payment_fraud_corpus.json").read_text(encoding="utf-8")) == builder().build()

    def test_it_holds_36_fraudulent_and_44_legitimate_payouts_with_unique_ids(self, corpus):
        labels = [c["label"] for c in corpus["cases"]]
        assert labels.count("fraud") == 36
        assert labels.count("legit") == 44
        assert len({c["id"] for c in corpus["cases"]}) == 80

    def test_nothing_in_it_looks_like_a_real_address(self, corpus):
        for case in corpus["cases"]:
            shipment = case["shipment"]
            for field in ("carrier_payee_email", "carrier_registered_payee_email"):
                assert shipment[field].endswith(".example") or shipment[field].endswith("@gmail.com"), shipment[field]
            for result in case["search_results"]:
                assert ".example/" in result["url"]

    def test_a_corpus_that_does_not_say_it_is_synthetic_is_refused(self, tmp_path):
        path = tmp_path / "corpus.json"
        path.write_text(json.dumps({"cases": [{"id": "x"}]}), encoding="utf-8")
        with pytest.raises(ValueError):
            ev().load_corpus(path)


class TestWhatTheChecksCatch:
    def test_the_deterministic_checks_catch_24_of_36_and_flag_no_legitimate_payout(self, rows):
        arm = ev().summarize(rows)["arms"]["floor"]
        assert (arm["recall"]["count"], arm["recall"]["of"]) == (24, 36)
        assert (arm["false_positive_rate"]["count"], arm["false_positive_rate"]["of"]) == (0, 44)

    def test_the_look_up_takes_it_to_32_of_36_at_the_price_of_6_false_flags(self, rows):
        summary = ev().summarize(rows)
        arm = summary["arms"]["identity"]
        assert (arm["recall"]["count"], arm["false_positive_rate"]["count"]) == (32, 6)
        assert summary["added"]["identity"] == {"true_flags": 8, "false_flags": 6}

    def test_the_scenarios_the_checks_cannot_see_stay_unflagged_without_the_analyst(self, rows):
        scenarios = ev().summarize(rows)["scenarios"]
        assert scenarios["impersonated_carrier_free_mail"]["flagged"] == {"floor": 0, "identity": 0, "site": 4}
        assert scenarios["shell_carrier"]["flagged"] == {"floor": 0, "identity": 8, "site": 8}
        assert scenarios["small_carrier_not_online"]["flagged"] == {"floor": 0, "identity": 6, "site": 6}
        for name in ("payee_mismatch", "recent_payee_change", "inflated_payout", "bec_change_request"):
            assert scenarios[name]["flagged"]["floor"] == scenarios[name]["cases"]

    def test_the_analyst_arm_is_not_run_without_verdicts_never_zero(self, rows):
        summary = ev().summarize(rows)
        assert summary["arms"]["analyst"] is None
        assert summary["added"]["analyst"] is None
        text = ev().report(summary)
        assert "NOT RUN" in text
        assert text.splitlines()[0].startswith("SYNTHETIC CORPUS")

    def test_each_rate_carries_a_wilson_interval_around_it(self, rows):
        recall = ev().summarize(rows)["arms"]["floor"]["recall"]
        assert recall["low"] < recall["rate"] < recall["high"]
        zero = ev().summarize(rows)["arms"]["floor"]["false_positive_rate"]
        assert zero["rate"] == 0.0
        assert zero["low"] == 0.0
        assert zero["high"] > 0.0


class TestMeasuringTheAnalyst:
    def test_an_analyst_that_catches_what_the_checks_miss_is_credited_with_exactly_that(self, corpus):
        out = ev().summarize(run(ev().run(corpus, verdicts(corpus, {"impersonated_carrier_free_mail"}))))
        assert out["added"]["analyst"] == {"true_flags": 4, "false_flags": 0}
        assert out["arms"]["analyst"]["recall"]["count"] == 36
        assert out["arms"]["analyst"]["false_positive_rate"]["count"] == 6

    def test_an_analyst_that_says_normal_about_everything_adds_nothing_and_the_report_says_so(self, corpus):
        summary = ev().summarize(run(ev().run(corpus, verdicts(corpus, set()))))
        assert summary["added"]["analyst"] == {"true_flags": 0, "false_flags": 0}
        assert "+ analyst model added nothing." in ev().report(summary)

    def test_an_analyst_that_alarms_at_everything_is_charged_for_its_false_flags(self, corpus):
        everything = {c["scenario"] for c in corpus["cases"]}
        summary = ev().summarize(run(ev().run(corpus, verdicts(corpus, everything))))
        assert summary["arms"]["analyst"]["false_positive_rate"]["count"] == 44
        assert summary["added"]["analyst"]["false_flags"] == 38

    def test_an_analyst_cannot_lower_a_floor(self, corpus):
        quiet = {"verdict": "normal", "confidence": 0.99, "reasons": [], "suggested_tier": "A"}
        rows = run(ev().run(corpus, {c["id"]: quiet for c in corpus["cases"]}))
        for row in rows:
            assert row["floors"]["analyst"] >= row["floors"]["identity"] >= row["floors"]["floor"]

    def test_an_arm_that_lowers_the_floor_stops_the_run(self, corpus, monkeypatch):
        module = ev()
        real = module._floor

        def lowering(shipment, ai):
            floor = real(shipment, ai)
            return floor - 10 if ai is not None and "analyst" in ai else floor

        monkeypatch.setattr(module, "_floor", lowering)
        case = next(c for c in corpus["cases"] if c["scenario"] == "payee_mismatch")
        with pytest.raises(AssertionError, match="lowered the floor"):
            run(module.measure(case, {"verdict": "normal", "confidence": 0.9}))


class TestTheScript:
    def test_the_default_run_needs_no_model_and_reports_the_synthetic_corpus(self, capsys):
        spec = importlib.util.spec_from_file_location("evaluate_payment_ai", ROOT / "scripts" / "evaluate_payment_ai.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class Args:
            live_analyst = False
            replay = None
            save = None

        text = run(module.main(Args()))
        assert text.startswith("SYNTHETIC CORPUS")
        assert "24/36" in text
        assert "NOT RUN" in text
