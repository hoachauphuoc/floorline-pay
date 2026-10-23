"""
A measurement of what the payment checks catch, and what the AI layers add, on a synthetic corpus.

The corpus (data/payment_fraud_corpus.json) is SYNTHETIC: 84 invented payouts, 40 fraudulent and
44 legitimate, built by scripts/build_payment_corpus.py from a fixed seed. The carriers do not
exist and the search results the carrier look-up reads are written into the corpus. What it can
show is how the checks behave on the patterns it was built from, which are the ones the checks
were written against; it says nothing about how often these patterns occur, and the rates are not
a forecast of production.

A payout is flagged when the risk floor that verifier.validate gives it is FLAG_FLOOR or more,
the line at which the pipeline holds a case for a person. Three arms, each adding to the one before:

  floor     the deterministic payment checks, with the screen of the payee-change request
  identity  plus the carrier look-up, read from the recorded search results
  analyst   plus the model's verdict, when one was produced (a live run, or one replayed from a file)

An arm that did not run is reported as not run. Nothing here assumes the analyst helps: the
report shows what it added, in true and false flags, with Wilson 95% intervals on every rate, and
if it added nothing it says so. Every run asserts the one property the design depends on: no arm
has a lower floor than the arm before it.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any

from vf_logistics import change_screen, payment_ai, verifier
from vf_logistics.evaluation import wilson

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
CORPUS = ROOT / "data" / "payment_fraud_corpus.json"
FLAG_FLOOR = 40


def load_corpus(path: pathlib.Path = CORPUS) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases")
    if data.get("synthetic") is not True or not isinstance(cases, list) or not cases:
        raise ValueError("the payment corpus must declare itself synthetic and hold cases")
    return data


async def _unscreened(text: Any, stage: str) -> dict[str, Any]:
    # The phrases are matched here; Model Armor needs a Google project and is not part of a measurement.
    return {"available": False, "blocked": False, "deterministic": None}


async def prepare(case: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """The case's shipment as the pipeline sees it: payee change dated, request text screened."""
    shipment = dict(case["shipment"])
    ago = case.get("payee_changed_days_ago")
    if ago is not None:
        shipment["carrier_payee_changed_at"] = ((now or datetime.now(timezone.utc)) - timedelta(days=ago)).isoformat()
    text = case.get("change_request_text")
    if text and (await change_screen.screen(text, _unscreened))["flagged"]:
        shipment["carrier_payee_change_flagged"] = True
    return shipment


def _search_of(case: dict[str, Any]) -> Any:
    async def search(query: str, *args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], str]:
        return list(case.get("search_results") or []), "ok"

    return search


def _floor(shipment: dict[str, Any], ai: dict[str, Any] | None) -> int:
    return int(verifier.validate(shipment, payment_ai=ai)["risk_floor"])


async def measure(
    case: dict[str, Any], analyst: dict[str, Any] | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    """The floor of one case in each arm. Raises if an arm lowered the floor."""
    shipment = await prepare(case, now)
    identity = await payment_ai.identity(shipment.get("carrier_name"), _search_of(case))
    floors: dict[str, int | None] = {
        "floor": _floor(shipment, None),
        "identity": _floor(shipment, {"identity": identity}),
        "analyst": None if analyst is None else _floor(shipment, {"identity": identity, "analyst": analyst}),
    }
    previous = floors["floor"] or 0
    for arm in ("identity", "analyst"):
        current = floors[arm]
        if current is None:
            continue
        if current < previous:
            raise AssertionError(f"{case['id']}: the {arm} arm lowered the floor ({previous} -> {current})")
        previous = current
    return {
        "id": case["id"], "scenario": case["scenario"], "label": case["label"],
        "floors": floors, "identity": identity["status"],
    }


async def run(
    corpus: dict[str, Any], analysts: dict[str, dict[str, Any]] | None = None, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Measure every case; `analysts` maps a case id to the analyst's verdict for it."""
    return [await measure(case, (analysts or {}).get(case["id"]), now) for case in corpus["cases"]]


def _flagged(row: dict[str, Any], arm: str) -> bool | None:
    floor = row["floors"][arm]
    return None if floor is None else floor >= FLAG_FLOOR


def _rate(count: int, total: int) -> dict[str, Any]:
    interval = wilson(count, total)
    return {
        "count": count, "of": total,
        "rate": round(count / total, 4) if total else None,
        "low": interval[0] if interval else None, "high": interval[1] if interval else None,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Recall and false-positive rate for each arm, recall by scenario, and what each arm added."""
    fraud = [r for r in rows if r["label"] == "fraud"]
    legit = [r for r in rows if r["label"] == "legit"]
    arms: dict[str, Any] = {}
    for arm in ("floor", "identity", "analyst"):
        if any(_flagged(r, arm) is None for r in rows):
            arms[arm] = None
            continue
        arms[arm] = {
            "recall": _rate(sum(1 for r in fraud if _flagged(r, arm)), len(fraud)),
            "false_positive_rate": _rate(sum(1 for r in legit if _flagged(r, arm)), len(legit)),
        }
    scenarios: dict[str, Any] = {}
    for row in rows:
        entry = scenarios.setdefault(row["scenario"], {"label": row["label"], "cases": 0, "flagged": {}})
        entry["cases"] += 1
        for arm in ("floor", "identity", "analyst"):
            if arms[arm] is not None:
                entry["flagged"][arm] = entry["flagged"].get(arm, 0) + (1 if _flagged(row, arm) else 0)
    added: dict[str, Any] = {}
    for before, after in (("floor", "identity"), ("identity", "analyst")):
        if arms[before] is None or arms[after] is None:
            added[after] = None
            continue
        new = [r for r in rows if _flagged(r, after) and not _flagged(r, before)]
        added[after] = {
            "true_flags": sum(1 for r in new if r["label"] == "fraud"),
            "false_flags": sum(1 for r in new if r["label"] == "legit"),
        }
    return {"cases": len(rows), "fraud": len(fraud), "legit": len(legit), "arms": arms,
            "scenarios": scenarios, "added": added}


def _pct(rate: dict[str, Any]) -> str:
    if rate["rate"] is None:
        return "n/a"
    return f"{rate['count']}/{rate['of']} = {rate['rate'] * 100:.1f}% (95% CI {rate['low'] * 100:.1f}-{rate['high'] * 100:.1f}%)"


def report(summary: dict[str, Any]) -> str:
    """The summary as plain text, with the synthetic caveat first."""
    lines = [
        "SYNTHETIC CORPUS: invented payouts and carriers, built from the patterns the checks were written for.",
        f"These rates describe this corpus only. {summary['cases']} cases ({summary['fraud']} fraudulent, "
        f"{summary['legit']} legitimate). Flagged means a risk floor of {FLAG_FLOOR} or more.",
        "",
    ]
    labels = {
        "floor": "deterministic checks", "identity": "+ carrier look-up", "analyst": "+ analyst model",
    }
    for arm, text in labels.items():
        data = summary["arms"][arm]
        if data is None:
            lines.append(f"{text}: NOT RUN (no analyst verdicts were supplied; run with --live-analyst or --replay)")
            continue
        lines.append(f"{text}: recall {_pct(data['recall'])}; false positives {_pct(data['false_positive_rate'])}")
    lines.append("")
    for arm in ("identity", "analyst"):
        added = summary["added"][arm]
        if added is None:
            continue
        verdict = "added nothing" if not (added["true_flags"] or added["false_flags"]) else (
            f"added {added['true_flags']} true and {added['false_flags']} false flag(s)")
        lines.append(f"{labels[arm]} {verdict}.")
    lines.append("")
    lines.append("flagged / cases by scenario (for a legitimate scenario a flag is a false positive):")
    for name, entry in sorted(summary["scenarios"].items()):
        cells = ", ".join(f"{arm} {n}/{entry['cases']}" for arm, n in entry["flagged"].items())
        lines.append(f"  {entry['label']:<5} {name:<28} {cells}")
    return "\n".join(lines)
