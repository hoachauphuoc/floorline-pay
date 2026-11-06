"""
Run the PayPal side of Floorline Pay against the PayPal SANDBOX, with the keys in .env, and print what happened.

    python scripts/sandbox_e2e.py check                          create an order for approval; no money moves
    python scripts/sandbox_e2e.py payout --to EMAIL [--amount 1.00]   pay a sandbox personal account from the sandbox
                                                                 business account, then read the batch back
    python scripts/sandbox_e2e.py finish ORDER_ID --payee EMAIL  after the sandbox personal account has approved the order:
                                                                 authorize it, book it, release it through the real settler
                                                                 (capture, payout) and read the results back
    add --save-fixtures DIR to keep each PayPal reply, with e-mail addresses masked, as a JSON file

Everything runs in this process on the in-memory store: nothing is written to Firestore or to any file but the fixtures,
and the PayPal client is fixed to api-m.sandbox.paypal.com, so no real money can move. The keys are read from .env
(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET) and never printed: the output is step names, HTTP statuses, PayPal ids and
statuses, and the approval link of the order.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import sys
import uuid
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ["STORE_BACKEND"] = "memory"

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
os.environ["STORE_BACKEND"] = "memory"  # a value in .env must not send this run to Firestore

from vf_logistics import carrier_registry, payment_binding, paypal_client, settler  # noqa: E402
from vf_logistics.store import get_store  # noqa: E402

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
SAVE_TO: pathlib.Path | None = None
RETURN = "https://example.com/floorline-return"
CANCEL = "https://example.com/floorline-cancel"


def mask(value: Any) -> Any:
    if isinstance(value, str):
        return EMAIL.sub("redacted@example.invalid", value)
    if isinstance(value, list):
        return [mask(v) for v in value]
    if isinstance(value, dict):
        return {k: ("redacted" if "token" in k.lower() else mask(v)) for k, v in value.items()}
    return value


def show(step: str, receipt: dict[str, Any], **facts: Any) -> bool:
    ok = receipt["status"] == paypal_client.OK
    extra = " ".join(f"{k}={v}" for k, v in facts.items())
    error = "" if ok else f" error={receipt.get('error')}"
    print(f"{'OK  ' if ok else 'FAIL'} {step}: {receipt['status']} http={receipt.get('http_status')} {extra}{error}".rstrip())
    if SAVE_TO is not None:
        SAVE_TO.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^a-z0-9]+", "_", step.lower()).strip("_")
        (SAVE_TO / f"{name}.json").write_text(json.dumps(mask(receipt), indent=1) + "\n", encoding="utf-8")
    return ok


def body_of(receipt: dict[str, Any]) -> dict[str, Any]:
    body = receipt.get("body")
    return body if isinstance(body, dict) else {}


def configured() -> bool:
    if paypal_client.configured():
        return True
    print("PayPal is not configured: put PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (sandbox) in .env")
    return False


async def check(args: argparse.Namespace) -> int:
    if not configured():
        return 1
    receipt = await paypal_client.create_order_for_approval(
        "1.00", "USD", f"e2e-{uuid.uuid4().hex[:10]}", "E2E-CHECK-1", RETURN, CANCEL,
    )
    body = body_of(receipt)
    link = next((x for x in body.get("links") or [] if isinstance(x, dict) and x.get("rel") == "payer-action"), None)
    ok = show(
        "create order for approval", receipt, order_id=body.get("id"), order_status=body.get("status"),
        payer_action_link=link is not None,
    )
    if ok and link:
        print(f"Approve it as the sandbox personal account, then run: finish {body.get('id')} --payee EMAIL")
        print(link.get("href"))
    return 0 if ok and link else 1


async def payout(args: argparse.Namespace) -> int:
    if not configured():
        return 1
    key = f"e2e-payout-{uuid.uuid4().hex[:10]}"
    receipt = await paypal_client.create_payout(args.to, args.amount, "USD", key, "Floorline sandbox check")
    batch_id = (body_of(receipt).get("batch_header") or {}).get("payout_batch_id")
    ok = show("create payout", receipt, payout_batch_id=batch_id, sender_batch_id=key)
    if not ok or not batch_id:
        return 1
    await asyncio.sleep(4)
    batch = await paypal_client.get_payout_batch(batch_id, f"read-{batch_id}")
    items = [i.get("transaction_status") for i in body_of(batch).get("items") or [] if isinstance(i, dict)]
    header = body_of(batch).get("batch_header") or {}
    sent = (header.get("sender_batch_header") or {}).get("sender_batch_id")
    return 0 if show(
        "read payout batch", batch, batch_status=header.get("batch_status"), item_statuses=items,
        sender_batch_id_echoed=sent == key,
    ) else 1


async def finish(args: argparse.Namespace) -> int:
    if not configured():
        return 1
    order_id, payee = args.order_id, args.payee.strip().lower()
    authorized = await paypal_client.authorize_order(order_id, f"e2e-authorize-{order_id}")
    unit = (body_of(authorized).get("purchase_units") or [{}])[0]
    auth = (unit.get("payments", {}).get("authorizations") or [{}])[0]
    if not show(
        "authorize order", authorized, authorization_id=auth.get("id"), authorization_status=auth.get("status"),
        reference_id=unit.get("reference_id"), amount=(auth.get("amount") or {}).get("value"),
    ) or not auth.get("id"):
        return 1
    reference = str(unit.get("reference_id"))
    carrier = "Sandbox Carrier Co"
    made = await carrier_registry.register_payee(carrier, payee, "operator@sandbox.example", "password")
    await carrier_registry.verify_payee(made["carrier_key"], made["version"], "reviewer@sandbox.example", "password",
                                        "sandbox check: payee entered by the person running this script")
    await payment_binding.record_authorization(auth["id"], reference, (auth.get("amount") or {}).get("value", "0"), "USD", order_id)
    case_id = f"E2E-{uuid.uuid4().hex[:8]}"
    await get_store().put_case({"case_id": case_id, "state": "RELEASED_BY_HUMAN", "shipment": {
        "shipment_id": reference, "currency": "USD", "shipping_cost": 1.0, "payout_amount": 1.0, "carrier_name": carrier,
        "carrier_payee_email": payee, "paypal_authorization_id": auth["id"],
    }})
    key = f"e2e-settle-{uuid.uuid4().hex[:8]}"
    result = await settler.settle({"case_id": case_id, "outcome": "RELEASED_BY_HUMAN", "approval_id": "e2e", "idempotency_key": key})
    print(f"{'OK  ' if result['status'] == 'settled' else 'FAIL'} settler: status={result['status']} reason={result['reason']}")
    for step in ("capture", "payout"):
        part = result.get(step) or {}
        print(f"     {step}: status={part.get('status')} http={part.get('http_status')} paypal_id={part.get('paypal_id')}")
    batch_id = (result.get("payout") or {}).get("paypal_id")
    if result["status"] == "settled" and batch_id:
        await asyncio.sleep(4)
        batch = await paypal_client.get_payout_batch(batch_id, f"read-{batch_id}")
        items = [i.get("transaction_status") for i in body_of(batch).get("items") or [] if isinstance(i, dict)]
        show("read payout batch", batch, batch_status=(body_of(batch).get("batch_header") or {}).get("batch_status"), item_statuses=items)
    return 0 if result["status"] == "settled" else 1


def main() -> int:
    global SAVE_TO
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--save-fixtures", help="keep each PayPal reply, e-mail addresses masked, in this directory")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    p = sub.add_parser("payout")
    p.add_argument("--to", required=True)
    p.add_argument("--amount", default="1.00")
    f = sub.add_parser("finish")
    f.add_argument("order_id")
    f.add_argument("--payee", required=True)
    args = parser.parse_args()
    if args.save_fixtures:
        SAVE_TO = pathlib.Path(args.save_fixtures)
    return asyncio.run({"check": check, "payout": payout, "finish": finish}[args.command](args))


if __name__ == "__main__":
    raise SystemExit(main())
