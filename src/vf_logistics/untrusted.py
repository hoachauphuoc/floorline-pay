"""
Untrusted input handling.

A shipping document is attacker-controlled. It arrives as a PDF, goes into a
model, and the model's output drives decisions that hold or release physical
cargo. That is a prompt-injection channel into a system with real-world effects,
and it has to be treated as one.

Three independent layers, because none of them is sufficient alone:

1. `screen_text` looks for injection patterns and invisible characters before
   anything is trusted. Detection-based, therefore bypassable - it lowers the
   success rate, it does not eliminate the risk.

2. `sanitise_shipment` enforces a strict field whitelist and types. Anything the
   model emits outside the schema is discarded, so an injection cannot introduce
   new fields, override a risk score, or smuggle instructions into a field the
   orchestrator reads.

3. The real backstop lives in `verifier.py`. The deterministic risk floor is
   computed from numbers and code-resident lists, so even a completely
   successful injection cannot talk the floor down. Layers 1 and 2 reduce the
   attack surface; layer 3 is what makes the failure mode survivable.

Layer 3 is the one to trust. The other two are hygiene.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# The only fields the pipeline will accept from a document, with their types.
# Anything else the model returns is dropped rather than passed along.
SHIPMENT_SCHEMA: dict[str, type] = {
    "shipment_id": str,
    "origin": str,
    "destination": str,
    "weight_kg": float,
    "declared_value": float,
    "shipping_cost": float,
    "currency": str,
    "shipper_name": str,
    "shipper_company": str,
    "shipper_country": str,
    "shipper_tax_id": str,
    "receiver_name": str,
    "receiver_company": str,
    "receiver_country": str,
    "cargo_description": str,
    "hs_code": str,
    "route_details": str,
    "transit_points": str,
    "status": str,
}

# Fields a document is never allowed to set. A bill of lading does not get to
# nominate its own risk score or decide its own outcome.
FORBIDDEN_FIELDS = {
    "risk_score", "risk_level", "compliance_status", "compliance_score",
    "decision", "state", "actions", "auto_clear", "auto_clear_permitted",
    "risk_floor", "validation", "reconciliation", "effective_risk",
    "delegation_boundary", "approved", "published", "reviewer", "gate_risk", "grounding",
}

MAX_FIELD_CHARS = 600

# Patterns that indicate text is trying to be read as instructions rather than
# as cargo details.
INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(all\s+|any\s+)?(previous|prior|above|preceding)", "override attempt"),
    (r"disregard\s+(all\s+|the\s+)?(previous|prior|above|instructions)", "override attempt"),
    (r"\b(system|assistant|developer)\s*(prompt|message|instruction)?\s*:", "role injection"),
    (r"</?(system|instructions?|prompt)>", "tag injection"),
    (r"you\s+are\s+(now|a|an)\b", "persona reassignment"),
    (r"new\s+(instructions?|rules?|task)\s*:", "instruction injection"),
    (r"\bset\s+(the\s+)?risk[_\s]?score\b", "score manipulation"),
    (r"\brisk[_\s]?score\s*(=|:|to)\s*\d", "score manipulation"),
    (r"\b(pre[-\s]?cleared|pre[-\s]?approved|already\s+(cleared|approved))\b", "clearance assertion"),
    (r"\b(mark|classify|treat|flag)\s+(this|it)\s+as\s+(clean|safe|cleared|low)", "clearance assertion"),
    (r"\b(do\s+not|don't|never)\s+(flag|escalate|report|screen|investigate)", "suppression attempt"),
    (r"\bskip\s+(the\s+)?(compliance|screening|review|check)", "suppression attempt"),
    (r"\boverride\b.{0,20}\b(compliance|policy|rule|threshold)", "policy override"),
    (r"\breturn\s+(only\s+)?json\b.{0,40}\brisk", "output hijack"),
]

# Zero-width and bidi control characters, the standard way to hide text from a
# human reviewer while leaving it legible to a model.
INVISIBLE_CHARS = {
    "\u200b": "zero width space",
    "\u200c": "zero width non-joiner",
    "\u200d": "zero width joiner",
    "\u2060": "word joiner",
    "\ufeff": "byte order mark",
    "\u00ad": "soft hyphen",
    "\u202a": "bidi embedding",
    "\u202b": "bidi embedding",
    "\u202c": "bidi pop",
    "\u202d": "bidi override",
    "\u202e": "bidi override",
    "\u2066": "bidi isolate",
    "\u2067": "bidi isolate",
    "\u2068": "bidi isolate",
    "\u2069": "bidi isolate",
}


def screen_text(text: str) -> dict[str, Any]:
    """
    Look for injection attempts in text extracted from a document.

    Returns a verdict. `blocked` means do not let this near a decision without a
    human; it is not a claim that everything else is safe.
    """
    if not text:
        return {"blocked": False, "findings": [], "checked_chars": 0}

    findings: list[dict[str, str]] = []
    lowered = text.lower()

    for pattern, label in INJECTION_PATTERNS:
        match = re.search(pattern, lowered, re.I | re.S)
        if match:
            findings.append({
                "type": label,
                "pattern": pattern,
                "excerpt": text[max(0, match.start() - 40): match.end() + 40].strip(),
            })

    invisible = {name for ch, name in INVISIBLE_CHARS.items() if ch in text}
    if invisible:
        findings.append({
            "type": "hidden characters",
            "pattern": "invisible unicode",
            "excerpt": f"document contains {', '.join(sorted(invisible))}",
        })

    # Private-use and unassigned code points are another hiding place.
    exotic = {
        ch for ch in text
        if unicodedata.category(ch) in ("Co", "Cn") and ch not in ("\n", "\r", "\t")
    }
    if exotic:
        findings.append({
            "type": "hidden characters",
            "pattern": "private use code points",
            "excerpt": f"{len(exotic)} private-use or unassigned code point(s)",
        })

    return {
        "blocked": bool(findings),
        "findings": findings,
        "checked_chars": len(text),
    }


def strip_invisible(text: str) -> str:
    for ch in INVISIBLE_CHARS:
        text = text.replace(ch, "")
    return "".join(
        ch for ch in text
        if unicodedata.category(ch) not in ("Co", "Cn") or ch in ("\n", "\r", "\t")
    )


def sanitise_shipment(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Coerce model output into the shipment schema and nothing more.

    Unknown fields are dropped, forbidden fields are recorded as an attack
    indicator, strings are truncated and stripped of invisible characters, and
    numbers that will not parse become 0 rather than propagating a string into
    arithmetic downstream.
    """
    clean: dict[str, Any] = {}
    dropped: list[str] = []
    forbidden_seen: list[str] = []

    for key, value in (raw or {}).items():
        if key in FORBIDDEN_FIELDS:
            forbidden_seen.append(key)
            continue
        if key in ("extraction_notes", "extraction_confidence"):
            continue  # handled separately, not part of the shipment record
        if key not in SHIPMENT_SCHEMA:
            dropped.append(key)
            continue

        expected = SHIPMENT_SCHEMA[key]
        if expected is float:
            try:
                clean[key] = float(str(value).replace(",", "").strip())
            except (TypeError, ValueError):
                clean[key] = 0.0
        else:
            text = strip_invisible(str(value)).strip()
            clean[key] = text[:MAX_FIELD_CHARS]

    notes = raw.get("extraction_notes") or []
    if not isinstance(notes, list):
        notes = [str(notes)]
    notes = [strip_invisible(str(n))[:MAX_FIELD_CHARS] for n in notes[:20]]

    try:
        confidence = float(raw.get("extraction_confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "shipment": clean,
        "extraction_notes": notes,
        "extraction_confidence": max(0.0, min(confidence, 1.0)),
        "dropped_fields": dropped,
        "forbidden_fields_attempted": forbidden_seen,
    }


def searchable_text(shipment: dict[str, Any], notes: list[str]) -> str:
    """Concatenate the free-text fields worth screening for injection."""
    parts = [
        str(shipment.get(field, ""))
        for field in (
            "cargo_description", "route_details", "transit_points",
            "shipper_company", "receiver_company", "shipper_name",
            "receiver_name", "origin", "destination",
        )
    ]
    parts.extend(str(n) for n in notes)
    return "\n".join(p for p in parts if p)
