"""
What the payee comparison adds, measured as its own arm (payment_eval.py).

The arms before it keep their figures. Pinned here: on the main corpus it catches the four impersonated carriers nothing else saw, at
no false flag; it runs on top of an analyst when there is one and never lowers a floor; the report names it; and on the holdout a
real carrier with a proven free-mail payee is left alone.
"""

from __future__ import annotations

import asyncio

import pytest

from vf_logistics import payment_eval

QUIET = {"verdict": "normal", "confidence": 0.9, "reasons": [], "suggested_tier": None}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def corpus():
    return payment_eval.load_corpus()


@pytest.fixture(scope="module")
def summary(corpus):
    return payment_eval.summarize(run(payment_eval.run(corpus)))


def test_it_catches_the_four_impersonated_carriers_at_no_false_flag(summary):
    assert summary["scenarios"]["impersonated_carrier_free_mail"]["flagged"]["site"] == 4
    assert summary["added"]["site"] == {"true_flags": 4, "false_flags": 0}
    assert summary["arms"]["site"]["recall"]["count"] == 36
    assert summary["arms"]["site"]["false_positive_rate"]["count"] == 6


def test_the_arms_before_it_keep_their_figures(summary):
    assert summary["arms"]["floor"]["recall"]["count"] == 24
    assert summary["arms"]["identity"]["recall"]["count"] == 32


def test_it_runs_on_top_of_an_analyst_and_lowers_nothing(corpus):
    rows = run(payment_eval.run(corpus, {c["id"]: QUIET for c in corpus["cases"]}))
    for row in rows:
        assert row["floors"]["site"] >= row["floors"]["analyst"] >= row["floors"]["identity"]
    assert payment_eval.summarize(rows)["added"]["site"] == {"true_flags": 4, "false_flags": 0}


def test_the_report_names_it(summary):
    text = payment_eval.report(summary)
    assert "+ payee against the carrier's site: recall 36/36" in text
    assert "added 4 true and 0 false flag(s)" in text


def test_a_real_carrier_with_a_proven_free_mail_payee_is_left_alone():
    holdout = payment_eval.load_corpus(payment_eval.ROOT / "data" / "payment_fraud_holdout.json")
    scenarios = payment_eval.summarize(run(payment_eval.run(holdout)))["scenarios"]
    assert scenarios["real_carrier_free_mail_established"]["flagged"]["site"] == 0
