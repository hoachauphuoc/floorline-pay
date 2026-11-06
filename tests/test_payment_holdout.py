"""
The holdout corpus (data/payment_fraud_holdout.json, scripts/build_payment_holdout.py).

The main corpus is built from the patterns the checks were written against, so a new layer cannot prove itself there. Pinned here:
the holdout declares itself synthetic, is what its builder produces, holds other carriers than the main corpus, holds the hard
cases on both sides (fraud nothing can see yet, honest payouts that look like fraud), no arm lowers a floor on it, and the script
measures the file it is given.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib

from vf_logistics import payment_eval

ROOT = pathlib.Path(__file__).resolve().parent.parent
HOLDOUT = ROOT / "data" / "payment_fraud_holdout.json"


def run(coro):
    return asyncio.run(coro)


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def holdout():
    return payment_eval.load_corpus(HOLDOUT)


def test_it_declares_itself_synthetic_and_is_what_the_builder_produces():
    assert holdout()["synthetic"] is True
    assert json.loads(HOLDOUT.read_text(encoding="utf-8")) == script("build_payment_holdout").build()


def test_it_holds_36_fraudulent_and_44_legitimate_payouts_with_unique_ids():
    cases = holdout()["cases"]
    labels = [c["label"] for c in cases]
    assert labels.count("fraud") == 36
    assert labels.count("legit") == 44
    assert len({c["id"] for c in cases}) == 80


def test_nothing_in_it_looks_like_a_real_address():
    for case in holdout()["cases"]:
        shipment = case["shipment"]
        for field in ("carrier_payee_email", "carrier_registered_payee_email"):
            assert shipment[field].endswith(".example") or shipment[field].endswith("@gmail.com"), shipment[field]
        for result in case["search_results"]:
            assert ".example/" in result["url"]


def test_its_carriers_are_not_those_of_the_main_corpus():
    main = {c["shipment"]["carrier_name"].split()[0] for c in payment_eval.load_corpus()["cases"]}
    other = {c["shipment"]["carrier_name"].split()[0] for c in holdout()["cases"]}
    assert not main & other


def test_it_holds_the_hard_cases_on_both_sides():
    scenarios = {c["scenario"] for c in holdout()["cases"]}
    assert {"lookalike_payee_domain", "cloned_site", "impersonated_listing_only", "bec_paraphrase", "bec_vietnamese"} <= scenarios
    assert {"real_carrier_free_mail_new", "group_finance_domain", "honest_urgent_change", "honest_vietnamese_change"} <= scenarios


def test_no_arm_lowers_a_floor_on_it():
    rows = run(payment_eval.run(holdout()))
    assert len(rows) == 80


def test_the_script_measures_the_file_it_is_given():
    class Args:
        live_analyst = False
        replay = None
        save = None
        corpus = str(HOLDOUT)

    text = run(script("evaluate_payment_ai").main(Args()))
    assert text.startswith("SYNTHETIC CORPUS")
    assert "lookalike_payee_domain" in text
