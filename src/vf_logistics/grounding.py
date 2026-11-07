"""
Check each flag the fraud model raised against the shipment record (grounding plan layer 2, shadow mode).

The prompt tells the model not to score absence, and a long trading history is not a risk signal, yet on CASE-SHP-PAY-DEMO-9 and DEMO-12 it
flagged a missing route, creation date and status, and called 412 shipments unusually high. This module labels each flag from the record alone:
it calls no model and decides nothing. Anything it cannot read stays "grounded", so it can never explain a flag away by accident.
"""

from __future__ import annotations

from typing import Any

GROUNDED = "grounded"
UNGROUNDED = "ungrounded"
ABSENCE = "absence"

_ABSENCE_WORDS = ("missing", "absent", "not provided", "not available", "unavailable", "no data", "lack of", "not stated", "n/a", "not specified", "absence of")
_FIELDS = (
    ("transaction count", "shipper_tx_count"),
    ("historical average", "avg_route_cost"),
    ("average cost", "avg_route_cost"),
    ("creation", "created_at"),
    ("created", "created_at"),
    ("route", "route_details"),
    ("status", "status"),
    ("metadata", "created_at"),
    ("metadata", "status"),
    ("metadata", "route_details"),
)
_ESTABLISHED_TRANSACTIONS = 100
_EMPTY = (None, "", "N/A", "not stated")
_MAX_TEXT = 200
_COST_WORDS = ("shipping cost", "freight")
_HIGH_WORDS = ("high", "excessive", "overcharg")
# A flag naming one of these, or trying to instruct the reader, is never ignored by the grounded gate, whatever else it says: it stays grounded.
_HEAVY_SIGNALS = (
    "sanction", "embargo", "ofac", "watchlist", "blacklist", "shell", "forg", "fake", "counterfeit", "launder", "stolen", "impersonat",
    "synthetic", "duplicate", "spike", "sudden", "new account", "conceal", "illicit", "suspicio", "mismatch", "inconsisten", "dual-use",
    "dual use", "diver", "terror", "weapon", "narcotic", "ignore", "instruction", "pre-clear", "pre-approv", "do not flag", "don't flag", "override",
)


def _text(flag: Any) -> str:
    if isinstance(flag, dict):
        return str(flag.get("description") or flag.get("flag") or flag.get("issue") or flag)
    return str(flag)


def _freight_is_unremarkable(shipment: dict[str, Any], validation: Any) -> bool:
    """True only when the deterministic freight check ran clean: no FREIGHT_* finding or observation, and the charge is below the declared value.

    The lane figure does not scale with the size of a shipment, so verifier.check_freight_ratio only records a high ratio as an observation. A model
    calling the freight unusually high where that check raised nothing is measuring against a yardstick the verifier already declines to score.
    Any doubt, a missing validation included, keeps the flag grounded.
    """
    if not isinstance(validation, dict):
        return False
    for number in (shipment.get("shipping_cost"), shipment.get("declared_value")):
        if isinstance(number, bool) or not isinstance(number, (int, float)) or number <= 0:
            return False
    if shipment["shipping_cost"] > shipment["declared_value"]:
        return False
    findings = validation.get("findings")
    observations = validation.get("observations")
    if not isinstance(findings, list) or not isinstance(observations, list):
        return False
    return not any(str(item.get("code", "")).startswith("FREIGHT") for item in findings + observations if isinstance(item, dict))


def classify_flag(flag: Any, shipment: Any, validation: Any = None) -> str:
    """Label one flag: absence if it truthfully says a field is missing, ungrounded if the record contradicts it, else grounded."""
    if not isinstance(shipment, dict) or flag is None or isinstance(flag, (int, float)):
        return GROUNDED
    text = _text(flag).lower()
    if any(signal in text for signal in _HEAVY_SIGNALS):
        return GROUNDED
    if any(word in text for word in _ABSENCE_WORDS):
        keys = {key for phrase, key in _FIELDS if phrase in text}
        if not keys:
            return GROUNDED
        if any(shipment.get(key) in _EMPTY for key in keys):
            return ABSENCE
        return UNGROUNDED
    if any(word in text for word in _COST_WORDS) and any(word in text for word in _HIGH_WORDS) and _freight_is_unremarkable(shipment, validation):
        return UNGROUNDED
    if "transaction count" in text and ("high" in text or "unusual" in text):
        count = shipment.get("shipper_tx_count")
        if isinstance(count, (int, float)) and not isinstance(count, bool) and count >= _ESTABLISHED_TRANSACTIONS:
            return UNGROUNDED
    return GROUNDED


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def grounded_risk(risk: Any, reconciliation: Any, grounding: Any) -> tuple[Any, str]:
    """The risk the agent gate reads for a payment case: (risk, "") unchanged, or (lowered risk, why).

    Lowered only when the case has flags and every one is ungrounded (the record contradicts it) or a true absence (the prompt says not to score
    those): then no flag supports the model's score, and the gate reads the deterministic floor plus any penalty from earlier human blocks of the
    shipper, never more than the case risk. A discount is not applied below the floor. A missing or malformed input returns the risk unchanged.
    """
    unchanged = (risk, "")
    if not _number(risk) or not isinstance(reconciliation, dict) or not isinstance(grounding, dict):
        return unchanged
    flags = grounding.get("flags")
    grounded = grounding.get(GROUNDED)
    ungrounded = grounding.get(UNGROUNDED)
    absence = grounding.get(ABSENCE)
    if not isinstance(flags, list) or not flags:
        return unchanged
    if not isinstance(grounded, int) or not isinstance(ungrounded, int) or not isinstance(absence, int):
        return unchanged
    if isinstance(grounded, bool) or isinstance(ungrounded, bool) or isinstance(absence, bool):
        return unchanged
    if grounded != 0 or ungrounded + absence != len(flags):
        return unchanged
    floor = reconciliation.get("risk_floor")
    adjustment = reconciliation.get("learning_adjustment", 0)
    if not isinstance(floor, (int, float)) or not isinstance(adjustment, (int, float)):
        return unchanged
    if isinstance(floor, bool) or isinstance(adjustment, bool) or floor < 0:
        return unchanged
    lowered = min(risk, 100, floor + max(adjustment, 0))
    if lowered >= risk:
        return unchanged
    note = (
        f"The model score {risk} is not counted: none of its {len(flags)} flag(s) is grounded in the record "
        f"({ungrounded} contradicted by it, {absence} a true absence); the agent gate reads {lowered}, the deterministic floor plus any penalty."
    )
    return lowered, note


def classify(flags: Any, shipment: Any, validation: Any = None) -> dict[str, Any]:
    """Label every flag and count the labels; a flag list of the wrong type gives no flags and zero counts."""
    labelled = []
    if isinstance(flags, list):
        labelled = [{"text": _text(flag)[:_MAX_TEXT], "label": classify_flag(flag, shipment, validation)} for flag in flags]
    return {
        "flags": labelled,
        GROUNDED: sum(1 for item in labelled if item["label"] == GROUNDED),
        UNGROUNDED: sum(1 for item in labelled if item["label"] == UNGROUNDED),
        ABSENCE: sum(1 for item in labelled if item["label"] == ABSENCE),
    }
