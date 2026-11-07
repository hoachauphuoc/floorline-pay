"""
The internal counterparty book.

verifier.py refuses to take a shipper's trading history from a bill of lading,
because a document able to assert its own history could claim a long one. The
finding it raises says the history "is not available from internal records" -
but until this module existed there were no internal records to consult, so the
finding was permanent for every document and no uploaded or staged file could
ever clear autonomously. That was a gap, not a policy: the control was written
around an enrichment step that had not been built.

This is that step. It answers one question - "is this a counterparty we already
know, and what is our own record of them?" - from data the document cannot
influence. The document supplies the claimed identity; the answer comes from
here.

Identity is matched on tax ID *and* company name together, and both must agree.
Matching on tax ID alone would be weaker than it looks: the tax ID is itself
read off the untrusted document, so a forged bill of lading carrying a real
customer's number would inherit that customer's clean history. Requiring the
name to agree means impersonation has to be wholesale rather than opportunistic,
and a number that matches while the name does not is reported as a mismatch -
actively worse for the shipment than being unknown, which is the correct
treatment for paperwork bearing someone else's tax number.

The residual exposure is worth stating plainly rather than hiding: this verifies
a *claimed* identity against internal records, and the claim still arrives on
paper. A production system ties intake to a booking reference issued against a
customer account, so identity is established before any document is read. The
delegation ceiling in governance.py is what bounds the damage in the meantime -
a spoofed identity still cannot release cargo above the limit.
"""

from __future__ import annotations

import re
from typing import Any

# Legal-form suffixes carry the least identifying information and vary most
# between a document and a database ("Pte Ltd" / "PTE. LTD." / "Pte. Limited").
# They are removed before comparison so formatting noise does not read as a
# different company, while the distinctive part of the name still has to match.
#
# The second line was missing until the official lists were indexed, and the
# public enforcement cases showed what that cost. OFAC writes "LLC TESTKOMPLEKT"
# where a Russian invoice says "OOO Testkomplekt" -- the same legal form in two
# languages -- and the two normalised to different keys, so a designated buyer
# named the way its own paperwork names it screened CLEAN. The forms added are the
# ones on this corridor's lists: Russian and CIS (OOO, AO, OAO, ZAO, PAO, TOV),
# Gulf free zones (FZE, FZCO, FZC, FZ-LLC) and the common Asian and European ones.
_SUFFIXES = {
    "co", "ltd", "limited", "jsc", "pte", "inc", "incorporated",
    "corp", "corporation", "group", "trading", "company", "plc", "llc",
    "ooo", "oao", "zao", "pao", "ao", "tov", "fze", "fzco", "fzc", "fz",
    "llp", "sdn", "bhd", "gmbh", "bv", "nv", "srl",
}


def _norm_tax_id(value: Any) -> str:
    """Digits only. Placeholders such as "not stated" normalise to empty."""
    return re.sub(r"\D", "", str(value or ""))


def _norm_company(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = [w for w in text.split() if w and w not in _SUFFIXES]
    return " ".join(words)


# Our own record of counterparties we have shipped for before.
#
# `tx_count` and `prior_flags` are what the fraud and compliance agents are
# denied from the document: settled facts from our books. Keyed by normalised
# tax ID; `company` is the name that must also agree.
_BOOK: dict[str, dict[str, Any]] = {
    "0301234567": {
        "company": "Saigon Textile Export JSC",
        "tx_count": 412,
        "prior_flags": 0,
        "known_since": "2019-04-02",
    },
    "0302887411": {
        "company": "Viet Tien Garment JSC",
        "tx_count": 287,
        "prior_flags": 0,
        "known_since": "2020-01-17",
    },
    "0400512983": {
        "company": "Truong Hai Logistics Co Ltd",
        "tx_count": 158,
        "prior_flags": 1,
        "known_since": "2021-06-30",
    },
    "0311204558": {
        "company": "Minh Phuong Ceramics Co Ltd",
        "tx_count": 96,
        "prior_flags": 0,
        "known_since": "2022-03-11",
    },
    # Fictional demo counterparty, added 7 Nov 2026 by a person: it has no web footprint, so an agent payment trial on it tests the payment path
    # and is not decided by what web search returns for a real company's name. Disclose it wherever the trial is cited.
    "0399000101": {
        "company": "Lotus Demo Ceramics Co Ltd",
        "tx_count": 120,
        "prior_flags": 0,
        "known_since": "2023-05-02",
    },
}


def lookup(tax_id: Any, company: Any) -> dict[str, Any]:
    """
    Resolve a claimed shipper identity against the internal book.

    Returns a result whose `status` is one of:

      verified           both tax ID and company name agree; `tx_count` and
                         `prior_flags` are ours to trust
      identity_mismatch  the tax ID is on file but under a different company;
                         treated as worse than unknown
      unknown            no usable tax ID, or one we have never traded under
    """
    key = _norm_tax_id(tax_id)
    if not key:
        return {"status": "unknown", "reason": "no usable tax ID on the document"}

    entry = _BOOK.get(key)
    if entry is None:
        return {
            "status": "unknown",
            "reason": f"tax ID {key} is not in the counterparty book",
        }

    if _norm_company(company) != _norm_company(entry["company"]):
        return {
            "status": "identity_mismatch",
            "reason": (
                f"tax ID {key} is on file for {entry['company']}, but the "
                f"document names {str(company or 'nothing').strip()}"
            ),
            "on_file_company": entry["company"],
        }

    return {
        "status": "verified",
        "matched_on": "tax_id + company",
        "tax_id": key,
        "company": entry["company"],
        "tx_count": entry["tx_count"],
        "prior_flags": entry["prior_flags"],
        "known_since": entry["known_since"],
    }


def enrich(shipment: dict[str, Any]) -> dict[str, Any]:
    """
    Attach verified counterparty history to a sanitised shipment, in place.

    Must run after untrusted.sanitise_shipment(), so that the document cannot
    present these fields itself, and before the agents and verifier read the
    record. Only a `verified` result supplies history; anything else leaves the
    shipment without it and lets the unverified-history floor stand.

    The resolved status is recorded on the shipment as well, because "the tax
    number on this document belongs to a different company" is a finding in its
    own right and verifier.py has to be able to see it. Neither field is in
    untrusted.py's schema, so a document cannot assert either one about itself.

    Returns the lookup result for the audit trail: a release that rested on a
    registry match should say so on the case.
    """
    result = lookup(shipment.get("shipper_tax_id"), shipment.get("shipper_company"))

    shipment["shipper_identity_status"] = result["status"]
    if result.get("reason"):
        shipment["shipper_identity_reason"] = result["reason"]

    if result["status"] == "verified":
        shipment["shipper_tx_count"] = result["tx_count"]

    return result
