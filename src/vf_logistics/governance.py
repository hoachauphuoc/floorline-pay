"""
Governance: delegation boundaries, the execution gate, and the agent gateway.

The design question this answers is not "can the agent decide?" but "who decided
that the agent may decide this?"

An agent here never acquires authority by reasoning well. Authority is granted by
a human publishing a **Delegation Boundary**: a versioned, machine-readable
statement of exactly which actions the agent may take and within what limits.
The agent then operates autonomously *inside* that boundary and stops at its
edge.

That distinction is what keeps this both autonomous and governable. The human
does not approve shipments one at a time - that would just be a slow human
process with extra steps. The human approves *policy*, once, and thousands of
cases execute against it without supervision. Only cases that fall outside the
published boundary come back to a person.

Three properties are deliberate:

* **Fail closed.** With no ACTIVE boundary the agent is SUSPENDED and nothing
  executes. A fresh deployment can analyse but cannot act until a human has
  published authority. The absence of policy is never read as permission.

* **One publish at a time.** Publishing a new version marks the previous one
  SUPERSEDED. Every executed action records the boundary version that permitted
  it, so any past decision can be reconstructed against the policy in force at
  the time rather than the policy in force now.

* **The gateway is the only door.** `tools.py` is not called from the workflow
  any more. Everything goes through `AgentGateway.execute`, which refuses
  anything the gate has not explicitly allowed.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeGuard

from vf_logistics import tools
from vf_logistics.store import get_store, new_id, utcnow
from vf_logistics.verifier import DUAL_USE_HS_PREFIXES, is_placeholder_text

# Actions that change the world and therefore require delegated authority.
PROTECTED_ACTIONS = {
    "release_shipment",
    "hold_shipment",
    "assign_analyst",
    "draft_sar",
    "notify_webhook",
    "publish_decision",
}

# Conditions that always route to a human regardless of what a boundary permits.
# A boundary can narrow authority; it cannot waive these.
NON_WAIVABLE_HUMAN_TRIGGERS = {
    "injection_detected": "Document contained suspected prompt injection",
    "forbidden_field_attempted": "Document tried to set a decision field directly",
}

DEFAULT_BOUNDARY_AUTHOR = os.getenv("BOUNDARY_BOOTSTRAP_AUTHOR", "").strip()


# --------------------------------------------------------------------------
# Delegation boundaries
# --------------------------------------------------------------------------

def proposed_boundary(reason: str = "Initial delegation proposal") -> dict[str, Any]:
    """
    A starting boundary for a human to narrow and publish.

    Intentionally conservative. The agent may release only low-value, low-risk,
    fully-documented shipments on plain lanes, and may never auto-release
    anything touching dual-use goods or an enhanced due diligence destination.
    Everything else it may only *propose*.
    """
    return {
        "allowed_actions": sorted(PROTECTED_ACTIONS - {"release_shipment"}),
        "auto_release": {
            "permitted": True,
            "max_declared_value_usd": 25_000,
            "max_effective_risk": 39,
            "require_zero_deterministic_findings": True,
            # Read from the verifier's own dual-use table rather than copied. The copy
            # this replaced had seven of the ten prefixes: 8471 (computers), 9014
            # (navigation instruments) and 8479 (incl. isotope separation) were
            # flagged CRITICAL by the rules yet absent from the boundary, so a
            # published boundary did not say what the rules already enforced.
            "forbidden_hs_prefixes": sorted(DUAL_USE_HS_PREFIXES),
            "forbidden_destinations": sorted(
                ["pakistan", "iran", "north korea", "syria", "russia", "belarus"]
            ),
        },
        "require_human_when": [
            "score_disputed",
            "injection_detected",
            "forbidden_field_attempted",
            "compliance_blocked",
            "exposure_claim_unsupported",
        ],
        "sar_filing": {
            # The agent may prepare a filing. It may never submit one.
            "may_draft": True,
            "may_file": False,
        },
        "reason": reason,
    }


# --------------------------------------------------------------------------
# Permissions schema
# --------------------------------------------------------------------------

# The triggers _human_triggers() can actually detect. A name outside this set
# would be stored, displayed, and never fire.
KNOWN_HUMAN_TRIGGERS = frozenset({
    "score_disputed",
    "injection_detected",
    "forbidden_field_attempted",
    "compliance_blocked",
    "exposure_claim_unsupported",
})

# release_shipment is governed by auto_release, not by allowed_actions: check()
# skips the allowed_actions test for it. Listing it there would look like a grant
# and do nothing, so it is refused rather than silently ignored.
_LISTABLE_ACTIONS = frozenset(PROTECTED_ACTIONS - {"release_shipment"})

_TOP_LEVEL_KEYS = frozenset({
    "allowed_actions", "auto_release", "require_human_when", "sar_filing", "reason",
})
_AUTO_RELEASE_KEYS = frozenset({
    "permitted",
    "max_declared_value_usd",
    "max_effective_risk",
    "require_zero_deterministic_findings",
    "forbidden_hs_prefixes",
    "forbidden_destinations",
})
_SAR_KEYS = frozenset({"may_draft", "may_file"})
_HS_PREFIX = re.compile(r"^\d{4}$")
_MAX_LIST = 200
_MAX_TEXT = 500


class BoundaryValidationError(ValueError):
    """A permissions object that does not match the schema. Carries every error."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("invalid permissions: " + "; ".join(errors))


def _is_number(value: Any) -> TypeGuard[float]:
    # bool is an int subclass in Python, and True as a value ceiling means 1.
    # A TypeGuard rather than bool so the callers' float(...) after the check is
    # known to receive a number, not Any | None.
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _string_list(
    value: Any, where: str, errors: list[str], allowed: frozenset[str] | None = None,
) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{where} must be a list of strings")
        return []
    if len(value) > _MAX_LIST:
        errors.append(f"{where} has {len(value)} entries; at most {_MAX_LIST}")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{where} entries must be non-empty strings, got {item!r}")
        elif allowed is not None and item not in allowed:
            errors.append(f"{where} has unknown entry {item!r}")
        else:
            out.append(item)
    if len(set(out)) != len(out):
        errors.append(f"{where} has duplicate entries")
    return out


def validate_permissions(permissions: Any) -> list[str]:
    """
    Every way `permissions` departs from the schema, or [] when it is valid.

    Finding A03 of the authorization review of bd6a210: publish accepted any
    non-empty object, and _check_release() read `permitted` by Python truthiness,
    so {"permitted": "false"} was stored and evaluated as PERMITTED. The intent
    "do not allow" came out as "allow". Nothing here coerces: a boolean is a JSON
    boolean, a number is a JSON number, and a key this module does not read is an
    error rather than a silent no-op, because a misspelt key is a control the
    author believes is in force and is not.

    Absence is allowed where absence narrows authority -- no auto_release means no
    release, no allowed_actions means nothing else either -- and refused where it
    would widen it: a release grant without both ceilings used to skip the
    missing check entirely.
    """
    errors: list[str] = []
    if not isinstance(permissions, dict) or not permissions:
        return ["permissions must be a non-empty object"]

    for key in sorted(set(permissions) - _TOP_LEVEL_KEYS):
        errors.append(f"unknown key {key!r}")

    if "allowed_actions" in permissions:
        actions = permissions["allowed_actions"]
        if isinstance(actions, list) and "release_shipment" in actions:
            errors.append(
                "allowed_actions may not list release_shipment; release is "
                "governed by auto_release"
            )
            actions = [a for a in actions if a != "release_shipment"]
        _string_list(actions, "allowed_actions", errors, _LISTABLE_ACTIONS)

    if "require_human_when" in permissions:
        _string_list(
            permissions["require_human_when"], "require_human_when", errors,
            KNOWN_HUMAN_TRIGGERS,
        )

    if "reason" in permissions:
        reason = permissions["reason"]
        if not isinstance(reason, str) or len(reason) > _MAX_TEXT:
            errors.append(f"reason must be a string of at most {_MAX_TEXT} characters")

    if "sar_filing" in permissions:
        sar = permissions["sar_filing"]
        if not isinstance(sar, dict):
            errors.append("sar_filing must be an object")
        else:
            for key in sorted(set(sar) - _SAR_KEYS):
                errors.append(f"sar_filing has unknown key {key!r}")
            for key in _SAR_KEYS & set(sar):
                if not isinstance(sar[key], bool):
                    errors.append(f"sar_filing.{key} must be true or false")
            if sar.get("may_file") is True:
                errors.append("sar_filing.may_file cannot be delegated; the agent never files")

    if "auto_release" in permissions:
        _validate_auto_release(permissions["auto_release"], errors)

    return errors


def _validate_auto_release(rules: Any, errors: list[str]) -> None:
    if not isinstance(rules, dict):
        errors.append("auto_release must be an object")
        return
    for key in sorted(set(rules) - _AUTO_RELEASE_KEYS):
        errors.append(f"auto_release has unknown key {key!r}")

    permitted = rules.get("permitted")
    if not isinstance(permitted, bool):
        errors.append(
            "auto_release.permitted is required and must be true or false "
            f"(got {permitted!r})"
        )

    if "require_zero_deterministic_findings" in rules and not isinstance(
        rules["require_zero_deterministic_findings"], bool
    ):
        errors.append("auto_release.require_zero_deterministic_findings must be true or false")

    for key, low, high in (
        ("max_effective_risk", 0, 100),
        ("max_declared_value_usd", 0, None),
    ):
        if key in rules:
            value = rules[key]
            if not _is_number(value) or value < low or (high is not None and value > high):
                bound = f"{low}-{high}" if high is not None else f">= {low}"
                errors.append(f"auto_release.{key} must be a number {bound} (got {value!r})")
        elif permitted is True:
            # A missing ceiling used to mean "no ceiling": the check that reads
            # it was skipped. Granting release must state both limits.
            errors.append(f"auto_release.{key} is required when permitted is true")

    if "forbidden_hs_prefixes" in rules:
        for prefix in _string_list(
            rules["forbidden_hs_prefixes"], "auto_release.forbidden_hs_prefixes", errors,
        ):
            if not _HS_PREFIX.match(prefix):
                errors.append(
                    f"auto_release.forbidden_hs_prefixes entry {prefix!r} must be 4 digits"
                )

    if "forbidden_destinations" in rules:
        for dest in _string_list(
            rules["forbidden_destinations"], "auto_release.forbidden_destinations", errors,
        ):
            # Matched against a lowercased destination, so an entry with a capital
            # letter can never match and forbids nothing.
            if dest != dest.strip().lower():
                errors.append(
                    f"auto_release.forbidden_destinations entry {dest!r} must be "
                    "lowercase with no surrounding spaces"
                )


def require_valid_permissions(permissions: Any) -> None:
    """Raise BoundaryValidationError unless `permissions` matches the schema."""
    errors = validate_permissions(permissions)
    if errors:
        raise BoundaryValidationError(errors)


async def publish_boundary(
    permissions: dict[str, Any], author: str, note: str,
    tenant_id: str | None = None,
    author_auth: str | None = None,
) -> dict[str, Any]:
    """
    Make a boundary official.

    This is the only function in the codebase that grants an agent authority,
    and it takes a human's name as a required argument for that reason.

    `permissions` must be a non-empty mapping. Publishing None or {} used to be
    accepted and silently produced an ACTIVE boundary granting whatever the
    caller's fallback supplied, which is the opposite of a safe default for the
    one function that hands out authority. It must also match the schema
    (validate_permissions); a mistyped grant is refused before anything is
    written, not stored and read generously later.
    """
    if not isinstance(permissions, dict) or not permissions:
        raise ValueError(
            "permissions must be a non-empty object. To remove the agent's "
            "authority use revoke_boundary() instead."
        )
    require_valid_permissions(permissions)

    store = get_store()
    # The version, id, status and `supersedes` are allotted by the store inside
    # one transaction (review findings A02 and T01): computed here from the active
    # boundary, a publish after a revoke reused v1 and overwrote the original.
    boundary: dict[str, Any] = await store.publish_boundary({
        "permissions": permissions,
        "published_by": author,
        "note": note,
        "published_at": utcnow(),
    }, tenant_id=tenant_id)
    version = boundary["version"]

    await store.add_audit({
        "audit_id": new_id("audit"),
        "case_id": "-",
        "action": "publish_delegation_boundary",
        "status": "done",
        "detail": {
            "boundary_id": boundary["boundary_id"],
            "version": version,
            "published_by": author,
            # How the author signed in (password, one_click, api_key...). None for
            # a caller that did not say, which keeps old call sites valid.
            "actor_auth": author_auth,
            "note": note,
            "superseded": boundary["supersedes"],
            # The full grant, so the append-only trail can answer "what exactly
            # was the agent allowed to do" on its own, without the boundary record.
            "permissions": permissions,
        },
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return boundary


async def revoke_boundary(
    author: str, note: str, tenant_id: str | None = None,
    author_auth: str | None = None,
) -> dict[str, Any]:
    """
    Withdraw the agent's authority, leaving no active boundary behind.

    The counterpart to publish_boundary: after this returns there is nothing
    for agent_readiness() to operate under, so it reports SUSPENDED and the
    execution gate denies every protected action. This is the mechanism the
    governance kill switch uses; it is deliberately a separate function from
    publish_boundary rather than "publish an empty boundary", so that revoking
    can never be mistaken for granting.
    """
    store = get_store()
    # In place, in one step, and keeping the content: a revoked boundary is
    # history an auditor reads, not a record to replace.
    current = await store.revoke_active_boundary(author, note, tenant_id=tenant_id)

    if not current:
        return {
            "revoked": False,
            "reason": "No active boundary to revoke; the agent is already suspended.",
            "boundary": None,
        }

    await store.add_audit({
        "audit_id": new_id("audit"),
        "case_id": "-",
        "action": "revoke_delegation_boundary",
        "status": "done",
        "detail": {
            "boundary_id": current.get("boundary_id"),
            "version": current.get("version"),
            "revoked_by": author,
            "actor_auth": author_auth,
            "note": note,
        },
        "at": utcnow(),
    }, tenant_id=tenant_id)
    return {
        "revoked": True,
        "reason": (
            f"{current.get('boundary_id')} revoked by {author}. The agent is "
            "suspended and cannot execute protected actions."
        ),
        "boundary": current,
    }


async def agent_readiness(tenant_id: str | None = None) -> dict[str, Any]:
    """READY only against an active published boundary. Otherwise SUSPENDED."""
    boundary = await get_store().active_boundary(tenant_id=tenant_id)
    if not boundary:
        return {
            "state": "SUSPENDED",
            "reason": (
                "No delegation boundary has been published. The agent may "
                "analyse and propose, but cannot execute protected actions."
            ),
            "boundary": None,
        }

    drift = await drift_check(boundary, tenant_id=tenant_id)
    if drift["material"]:
        return {
            "state": "SUSPENDED",
            "reason": (
                "Material drift from the behaviour this boundary was published "
                f"for: {drift['reason']} A human should review and re-publish."
            ),
            "boundary": boundary,
            "drift": drift,
        }

    return {
        "state": "READY",
        "reason": f"Operating under {boundary['boundary_id']} "
                  f"published by {boundary.get('published_by')}",
        "boundary": boundary,
        "drift": drift,
    }


# --------------------------------------------------------------------------
# Drift detection
# --------------------------------------------------------------------------

# A boundary is published against an expectation of how the traffic behaves. If
# the traffic changes shape, the boundary no longer means what its author
# intended, even though nothing about it has changed.
DRIFT_MIN_SAMPLE = int(os.getenv("DRIFT_MIN_SAMPLE", "8"))
DRIFT_MAX_AUTO_RELEASE_RATE = float(os.getenv("DRIFT_MAX_AUTO_RELEASE_RATE", "0.85"))
DRIFT_MAX_VETO_RATE = float(os.getenv("DRIFT_MAX_VETO_RATE", "0.60"))
DRIFT_MAX_INJECTION_RATE = float(os.getenv("DRIFT_MAX_INJECTION_RATE", "0.20"))


async def drift_check(
    boundary: dict[str, Any], tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Compare recent behaviour against what the boundary was published for.

    Three signals, each of which means the author's assumptions no longer hold:

    * **Auto-release rate too high.** Either the traffic really did get cleaner,
      or something upstream is feeding the agent shipments that trivially pass.
      Both deserve a human look before more cargo is released.
    * **Veto rate too high.** The agents and the deterministic checks are
      disagreeing routinely, which means the model is no longer scoring the way
      it did when this boundary was written.
    * **Injection rate too high.** Someone is probing the document intake path.

    Suspending on drift is not a fault condition. It is the system declining to
    keep exercising authority granted for circumstances that no longer apply.
    """
    cases = [
        c for c in await get_store().list_cases(60, tenant_id=tenant_id)
        if not c.get("is_marker") and c.get("state") != "OBJECT_PROCESSED"
    ]

    sample = len(cases)
    metrics: dict[str, Any] = {
        "sample": sample,
        "auto_release_rate": 0.0,
        "veto_rate": 0.0,
        "injection_rate": 0.0,
        "boundary_version": boundary.get("version"),
    }

    if sample < DRIFT_MIN_SAMPLE:
        return {
            "material": False,
            "reason": f"only {sample} recent case(s); below the {DRIFT_MIN_SAMPLE} "
                      "needed to judge drift.",
            # Same key set as the main return below. Omitting `reasons` here
            # forced every consumer to handle two shapes for one function.
            "reasons": [],
            "metrics": metrics,
        }

    auto = sum(1 for c in cases if c.get("state") == "AUTO_CLEARED")
    vetoed = sum(
        1 for c in cases
        if (c.get("reconciliation") or {}).get("source") == "deterministic floor"
    )
    injected = sum(
        1 for c in cases
        if ((c.get("input_security") or {}).get("injection_screening") or {}).get("blocked")
        or ((c.get("input_security") or {}).get("model_armor") or {}).get("blocked")
    )

    metrics["auto_release_rate"] = round(auto / sample, 3)
    metrics["veto_rate"] = round(vetoed / sample, 3)
    metrics["injection_rate"] = round(injected / sample, 3)

    reasons = []
    if metrics["auto_release_rate"] > DRIFT_MAX_AUTO_RELEASE_RATE:
        reasons.append(
            f"{metrics['auto_release_rate']:.0%} of recent cases were auto-released "
            f"(limit {DRIFT_MAX_AUTO_RELEASE_RATE:.0%})."
        )
    if metrics["veto_rate"] > DRIFT_MAX_VETO_RATE:
        reasons.append(
            f"deterministic checks overrode the agent on "
            f"{metrics['veto_rate']:.0%} of cases (limit {DRIFT_MAX_VETO_RATE:.0%})."
        )
    if metrics["injection_rate"] > DRIFT_MAX_INJECTION_RATE:
        reasons.append(
            f"{metrics['injection_rate']:.0%} of documents were flagged for prompt "
            f"injection (limit {DRIFT_MAX_INJECTION_RATE:.0%})."
        )

    return {
        "material": bool(reasons),
        # `reason` is the joined prose agent_readiness() interpolates into its
        # explanation; `reasons` is the same content unjoined so a UI can render
        # one item per cause. Both are returned because the two consumers want
        # different shapes and a single key silently gave one of them nothing.
        "reason": " ".join(reasons) or "within expected behaviour.",
        "reasons": reasons,
        "metrics": metrics,
    }


# --------------------------------------------------------------------------
# Execution gate
# --------------------------------------------------------------------------

def _human_triggers(case: dict[str, Any], permissions: dict[str, Any]) -> list[str]:
    """Which configured or non-waivable triggers this case has hit."""
    configured = set(permissions.get("require_human_when", []))
    hit: list[str] = []

    security = (case.get("input_security") or {}).get("injection_screening") or {}
    if security.get("blocked"):
        hit.append("injection_detected")
    if (case.get("input_security") or {}).get("forbidden_fields_attempted"):
        hit.append("forbidden_field_attempted")

    if (case.get("reconciliation") or {}).get("score_disputed"):
        hit.append("score_disputed")

    if str(case.get("compliance_status") or "").upper() == "BLOCKED":
        hit.append("compliance_blocked")

    codes = {
        f.get("code") for f in (case.get("validation") or {}).get("findings", [])
    }
    if "EXPOSURE_CLAIM_UNSUPPORTED" in codes:
        hit.append("exposure_claim_unsupported")

    # Non-waivable triggers count even if the boundary omits them.
    return [
        t for t in hit
        if t in configured or t in NON_WAIVABLE_HUMAN_TRIGGERS
    ]


def check(
    action: str, case: dict[str, Any], boundary: dict[str, Any] | None
) -> dict[str, Any]:
    """
    Deterministic execution gate. Fail closed.

    Returns {allowed, reason, boundary_version}. No model is consulted: whether
    an action is permitted is a policy lookup, not a judgement call.
    """
    if action not in PROTECTED_ACTIONS:
        return {"allowed": True, "reason": "not a protected action", "boundary_version": None}

    if not boundary:
        return {
            "allowed": False,
            "reason": "DENIED: no active delegation boundary (agent SUSPENDED)",
            "boundary_version": None,
        }

    perms = boundary.get("permissions") or {}
    version = boundary.get("version")

    if action not in set(perms.get("allowed_actions", [])) and action != "release_shipment":
        return {
            "allowed": False,
            "reason": f"DENIED: '{action}' is outside {boundary['boundary_id']}",
            "boundary_version": version,
        }

    triggers = _human_triggers(case, perms)
    if triggers:
        return {
            "allowed": False,
            "reason": "DENIED: case requires human review (" + ", ".join(triggers) + ")",
            "boundary_version": version,
            "human_triggers": triggers,
        }

    if action == "release_shipment":
        return _check_release(case, perms, boundary)

    if action == "draft_sar" and perms.get("sar_filing", {}).get("may_draft") is not True:
        return {
            "allowed": False,
            "reason": f"DENIED: drafting filings not delegated under {boundary['boundary_id']}",
            "boundary_version": version,
        }

    return {
        "allowed": True,
        "reason": f"within {boundary['boundary_id']}",
        "boundary_version": version,
    }


def _check_release(
    case: dict[str, Any], perms: dict[str, Any], boundary: dict[str, Any]
) -> dict[str, Any]:
    """
    Releasing cargo is the one irreversible action, so it gets its own gate.

    Every condition here is evaluated against the shipment record and the
    deterministic findings, never against the agent's narrative.
    """
    rules = perms.get("auto_release") or {}
    version = boundary.get("version")

    def deny(reason: str) -> dict[str, Any]:
        return {"allowed": False, "reason": f"DENIED: {reason}", "boundary_version": version}

    # `is True`, not truthiness. A stored {"permitted": "false"} is a non-empty
    # string and used to read as a grant (review finding A03). publish now refuses
    # it, and this refuses it again for anything already in the store.
    if rules.get("permitted") is not True:
        return deny("auto-release not delegated")

    shipment = case.get("shipment") or {}
    validation = case.get("validation") or {}
    reconciliation = case.get("reconciliation") or {}

    # Both ceilings must be stated numbers. An absent one used to skip its
    # check, so a grant that forgot a limit had none.
    ceiling = rules.get("max_effective_risk")
    max_value = rules.get("max_declared_value_usd")
    if not _is_number(ceiling) or not _is_number(max_value):
        return deny("auto-release grant does not state both a risk and a value ceiling")
    risk_ceiling, value_ceiling = float(ceiling), float(max_value)

    risk = case.get("risk_score")
    # For a payment case whose model score no flag supports, the orchestrator names a lower risk (switch AGENT_GROUNDED_GATE, off by default).
    # It counts only as a real number between the deterministic floor and the risk score; anything else leaves the risk score as it is.
    gate_risk = case.get("gate_risk")
    floor = reconciliation.get("risk_floor")
    if (
        _is_number(risk) and _is_number(gate_risk) and _is_number(floor)
        and not isinstance(gate_risk, bool) and not isinstance(floor, bool)
        and float(floor) <= float(gate_risk) <= float(risk)
    ):
        risk = gate_risk
    if not _is_number(risk) or float(risk) > risk_ceiling:
        return deny(f"effective risk {risk} exceeds delegated ceiling {ceiling}")

    if rules.get("require_zero_deterministic_findings", True) is not False:
        # A missing count is not a count of zero: it means no record that the
        # deterministic checks ran, and that cannot satisfy a zero-findings rule.
        count = validation.get("finding_count")
        if not _is_number(count):
            return deny("no deterministic finding count on the case")
        if count:
            return deny(f"{count} deterministic finding(s) present")

    if reconciliation.get("auto_clear_permitted") is not True:
        return deny("deterministic validation vetoed auto-clear")

    # The shipment's side of each limit fails closed the way the grant's side
    # does above. An absent or unreadable value must not read as 0, which passes
    # any ceiling: the verifier raises no finding for it, so this check is the
    # only thing between such a shipment and an automated release.
    declared: Any = shipment.get("declared_value")
    try:
        value = float(declared)
    except (TypeError, ValueError):
        return deny("declared value is not stated, so the value ceiling cannot be checked")
    if not math.isfinite(value) or value < 0:
        return deny(f"declared value {value!r} is not a usable amount")
    if value > value_ceiling:
        return deny(f"declared value {value:,.0f} USD exceeds delegated {value_ceiling:,.0f} USD")

    hs = "".join(ch for ch in str(shipment.get("hs_code") or "") if ch.isdigit())[:4]
    if hs and hs in set(rules.get("forbidden_hs_prefixes", [])):
        return deny(f"HS prefix {hs} is excluded from auto-release")

    forbidden_destinations = rules.get("forbidden_destinations", [])
    # A blank destination contains no excluded name, so it would pass the loop
    # below unread. The verifier does not flag a blank destination either: a
    # receiver in Iran with the destination left empty validates clean.
    if forbidden_destinations and is_placeholder_text(shipment.get("destination")):
        return deny("destination is not stated, so excluded destinations cannot be checked")
    dest = str(shipment.get("destination") or "").lower()
    for blocked in forbidden_destinations:
        if blocked in dest:
            return deny(f"destination '{blocked}' is excluded from auto-release")

    return {
        "allowed": True,
        "reason": f"within auto-release limits of {boundary['boundary_id']}",
        "boundary_version": version,
    }


# --------------------------------------------------------------------------
# Policy simulation
# --------------------------------------------------------------------------

async def simulate_boundary(
    candidate_permissions: dict[str, Any],
    action: str = "release_shipment",
    sample: int = 40,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Replay recent cases against a candidate boundary without publishing it.

    A delegation boundary is abstract until someone can see what it would have
    done. This answers the only question a policy author actually has -- "if I
    tighten this, what stops being automatic?" -- by evaluating `check()` twice
    per case, once under the active boundary and once under the candidate, and
    reporting the cases whose outcome differs.

    Safe to call freely: `check()` is pure, so nothing is written, no boundary is
    published, and no action is executed. The candidate is wrapped in a
    throwaway boundary envelope carrying version None, which is why the
    per-case results report the decision rather than a version.
    """
    if not isinstance(candidate_permissions, dict) or not candidate_permissions:
        raise ValueError("candidate permissions must be a non-empty object")
    # The same schema as publish, so a preview cannot describe a boundary that
    # publishing would then refuse -- or, worse, one it would read differently.
    require_valid_permissions(candidate_permissions)

    store = get_store()
    current = await store.active_boundary(tenant_id=tenant_id)
    cases = [
        c for c in await store.list_cases(sample, tenant_id=tenant_id)
        if not c.get("is_marker") and c.get("state") != "OBJECT_PROCESSED"
    ]

    candidate = {
        "boundary_id": "BOUNDARY-candidate",
        "version": None,
        "status": "SIMULATED",
        "permissions": candidate_permissions,
    }

    flipped: list[dict[str, Any]] = []
    counts = {
        "unchanged_allow": 0,
        "unchanged_deny": 0,
        "now_denied": 0,
        "now_allowed": 0,
    }

    for case in cases:
        before = check(action, case, current)
        after = check(action, case, candidate)

        if before["allowed"] == after["allowed"]:
            counts["unchanged_allow" if after["allowed"] else "unchanged_deny"] += 1
            continue

        counts["now_allowed" if after["allowed"] else "now_denied"] += 1
        flipped.append({
            "case_id": case.get("case_id"),
            "shipment_id": case.get("shipment_id"),
            "state": case.get("state"),
            "risk_score": case.get("risk_score"),
            "declared_value": (case.get("shipment") or {}).get("declared_value"),
            "hs_code": (case.get("shipment") or {}).get("hs_code"),
            "destination": (case.get("shipment") or {}).get("destination"),
            "was": "ALLOWED" if before["allowed"] else "DENIED",
            "becomes": "ALLOWED" if after["allowed"] else "DENIED",
            "before_reason": before["reason"],
            "after_reason": after["reason"],
        })

    direction = (
        "tighter" if counts["now_denied"] and not counts["now_allowed"]
        else "looser" if counts["now_allowed"] and not counts["now_denied"]
        else "mixed" if (counts["now_denied"] and counts["now_allowed"])
        else "no change"
    )

    return {
        "action": action,
        "cases_evaluated": len(cases),
        "active_boundary": (current or {}).get("boundary_id"),
        "counts": counts,
        "flipped_count": len(flipped),
        "direction": direction,
        # Capped so a large sample cannot return an unbounded payload; the
        # counts above still reflect every case evaluated.
        "flipped": flipped[:20],
    }


# --------------------------------------------------------------------------
# Agent gateway
# --------------------------------------------------------------------------

_ACTION_IMPLS: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
    "release_shipment": tools.release_shipment,
    "hold_shipment": tools.hold_shipment,
    "assign_analyst": tools.assign_analyst,
    "draft_sar": tools.draft_sar,
    "notify_webhook": tools.notify_webhook,
    "publish_decision": tools.publish_decision,
}


async def execute(
    action: str, case: dict[str, Any], /, *, tenant_id: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    The only path from the workflow to a real-world action.

    A denial is recorded in the audit log exactly like a success. An action that
    was refused is as much a governance fact as one that was taken, and a system
    that silently drops denials cannot be audited.
    """
    # Readiness rather than the raw boundary: an agent suspended for material
    # drift holds a perfectly valid boundary and still must not act on it.
    readiness = await agent_readiness(tenant_id=tenant_id)
    boundary = readiness.get("boundary")

    if readiness["state"] != "READY":
        verdict = {
            "allowed": False,
            "reason": f"DENIED: agent is {readiness['state']}. {readiness['reason']}",
            "boundary_version": (boundary or {}).get("version"),
        }
    else:
        verdict = check(action, case, boundary)

    if not verdict["allowed"]:
        receipt = {
            "audit_id": new_id("audit"),
            "case_id": case.get("case_id"),
            "action": action,
            "status": "denied",
            "detail": {
                "gate_reason": verdict["reason"],
                "boundary_version": verdict.get("boundary_version"),
                "human_triggers": verdict.get("human_triggers", []),
                "requested_args": {
                    k: (str(v)[:200] if isinstance(v, str) else v)
                    for k, v in kwargs.items()
                },
            },
            "at": utcnow(),
        }
        await get_store().add_audit(receipt, tenant_id=tenant_id)
        return receipt

    impl = _ACTION_IMPLS.get(action)
    if impl is None:
        raise ValueError(f"unknown action {action}")

    receipt = await impl(case["case_id"], tenant_id=tenant_id, **kwargs)
    receipt.setdefault("detail", {})["boundary_version"] = verdict.get("boundary_version")
    receipt["gate_reason"] = verdict["reason"]
    return receipt
