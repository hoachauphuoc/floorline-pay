"""
Measure what the payment checks catch, and what the AI layers add, on the synthetic corpus.

    python scripts/evaluate_payment_ai.py                   deterministic checks and the carrier look-up
                                                            (recorded search results); no model, no cost
    python scripts/evaluate_payment_ai.py --live-analyst    also ask the payment-risk analyst about every
                                                            case (one model call each; needs NEBIUS_API_KEY)
    python scripts/evaluate_payment_ai.py --replay FILE     use analyst verdicts saved by an earlier run
    python scripts/evaluate_payment_ai.py --save FILE       write the analyst verdicts of this run to FILE

The corpus is synthetic (see vf_logistics.payment_eval): the rates describe it only. With no analyst
verdicts the analyst arm is reported as not run, never as zero.

The model key is read from NEBIUS_API_KEY by nebius_client; nothing here reads, prints or writes it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vf_logistics import change_screen, payment_ai, payment_eval  # noqa: E402


async def ask_analyst(corpus: dict) -> dict[str, dict]:
    """The analyst's verdict on every case, from the features the pipeline would show it."""
    verdicts: dict[str, dict] = {}
    for case in corpus["cases"]:
        shipment = await payment_eval.prepare(case)
        verdicts[case["id"]] = await payment_ai.analyst(payment_ai.features(shipment, case["history"]))
    return verdicts


async def ask_reader(corpus: dict) -> dict[str, dict]:
    """The model's reading of every change request in the corpus."""
    return {c["id"]: await change_screen.read(c["change_request_text"]) for c in corpus["cases"] if c.get("change_request_text")}


async def main(args: argparse.Namespace) -> str:
    chosen = getattr(args, "corpus", None)
    corpus = payment_eval.load_corpus(pathlib.Path(chosen)) if chosen else payment_eval.load_corpus()
    analysts: dict[str, dict] | None = None
    if args.replay:
        analysts = json.loads(pathlib.Path(args.replay).read_text(encoding="utf-8"))
    elif args.live_analyst:
        analysts = await ask_analyst(corpus)
        # A run with no answers would be saved and reported as an analyst that added nothing, which it did not measure.
        if all(answer.get("verdict") == "unknown" for answer in analysts.values()):
            raise SystemExit("no verdict came back: the model could not be reached (is NEBIUS_API_KEY set?)")
    if args.save and analysts is not None:
        pathlib.Path(args.save).write_text(json.dumps(analysts, indent=1) + "\n", encoding="utf-8")
    readers: dict[str, dict] | None = None
    if getattr(args, "replay_reader", None):
        readers = json.loads(pathlib.Path(args.replay_reader).read_text(encoding="utf-8"))
    elif getattr(args, "live_reader", False):
        readers = await ask_reader(corpus)
        # A run with no answers would be reported as a reader that added nothing, which it did not measure.
        if all(reading.get("state") == "unavailable" for reading in readers.values()):
            raise SystemExit("no reading came back: the model could not be reached (is NEBIUS_API_KEY set?)")
    if getattr(args, "save_reader", None) and readers is not None:
        pathlib.Path(args.save_reader).write_text(json.dumps(readers, indent=1) + "\n", encoding="utf-8")
    rows = await payment_eval.run(corpus, analysts, readers=readers)
    return payment_eval.report(payment_eval.summarize(rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live-analyst", action="store_true", help="ask the model about every case (costs tokens)")
    parser.add_argument("--replay", help="analyst verdicts saved by an earlier run")
    parser.add_argument("--save", help="write this run's analyst verdicts to a file")
    parser.add_argument("--corpus", help="measure this corpus instead of the main one, such as data/payment_fraud_holdout.json")
    parser.add_argument("--live-reader", action="store_true", help="ask the model to read every change request (costs tokens)")
    parser.add_argument("--replay-reader", help="readings saved by an earlier run")
    parser.add_argument("--save-reader", help="write this run's readings to a file")
    print(asyncio.run(main(parser.parse_args())))
