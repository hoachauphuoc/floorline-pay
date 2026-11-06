"""
Floorline's read-only tools for an outside agent, over the Model Context Protocol.

    FLOORLINE_API_URL=https://<service> FLOORLINE_API_KEY=<key> python -m vf_logistics.mcp_server

An agent that is about to pay a carrier can ask Floorline first: would these payout details be flagged, what is the state of this
payment, what is its risk, who is frozen, and would the checks stop the attacks they were built for. Every tool reads. None
captures, pays, voids, releases, approves, lifts a freeze or pauses payments: money moves only in the settler, after a person's
decision, and this server has no route to it. The key stays in this process; the agent never sees it.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from vf_logistics import payment_checks

_CASE_ID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_TIMEOUT_SECONDS = 15.0


class FloorlineError(Exception):
    """A tool could not answer; the message says what to fix and that nothing was changed."""


server = FastMCP(
    "floorline-pay",
    instructions=(
        "Read-only tools of Floorline Pay, a payment check that sits in front of a carrier payout. They report; they never move money. "
        "A payout is released only by a person signed in to Floorline."
    ),
)


def _settings() -> tuple[str, str]:
    base = os.getenv("FLOORLINE_API_URL", "").strip().rstrip("/")
    key = os.getenv("FLOORLINE_API_KEY", "").strip()
    if not base or not key:
        raise FloorlineError("set FLOORLINE_API_URL and FLOORLINE_API_KEY to reach a Floorline service; nothing was changed")
    return base, key


def _case(case_id: str) -> str:
    if not _CASE_ID.match(case_id):
        raise FloorlineError("a case id is made of letters, digits, dots, dashes and underscores, up to 80 of them")
    return case_id


async def _get(path: str) -> Any:
    base, key = _settings()
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.get(f"{base}/api/v1/{path}", headers={"X-VF-API-Key": key})
    if response.status_code != 200:
        raise FloorlineError(f"Floorline answered {response.status_code} for {path}; nothing was changed")
    return response.json()


@server.tool()
def check_payout_risk(shipment: dict[str, Any]) -> dict[str, Any]:
    """The deterministic findings for payout details (payee, amount, a recent payee change), and the risk floor they set.

    A model can raise that floor and never lower it. A shipment that names no payout, authorization or payee moves no money and has no findings.
    """
    findings = payment_checks.check_payment(shipment)
    return {"findings": findings, "risk_floor": max((int(f["floor"]) for f in findings), default=0)}


@server.tool()
async def get_payment_state(case_id: str) -> Any:
    """What the settler did with the money of one case: awaiting a decision, paid, returned, held or failed, and why."""
    return await _get(f"payments/cases/{_case(case_id)}")


@server.tool()
async def get_payment_risk(case_id: str) -> Any:
    """The payment risk of one case: the findings, the analyst's reading, the carrier's credit standing and whether a second approver is needed."""
    return await _get(f"payments/cases/{_case(case_id)}/risk")


@server.tool()
async def list_frozen_parties() -> Any:
    """The carriers and shippers whose payments are frozen, with the reason. Only a person lifts a freeze."""
    return await _get("payments/freezes")


@server.tool()
async def replay_attacks() -> Any:
    """Replay the attacks Floorline is built to stop on a routine payout: for each, the layer that stops it and the money that moved (none)."""
    return await _get("defense/scenarios")


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
