"""
Where the carrier payee registry is applied (carrier_registry.enrich in orchestrator.py and
payment_status.py).

Pinned here: a payment shipment is enriched from the registry when it is taken in, so the
floor and the review queue see the registry's payee and not the sender's claim; a shipment
that moves no money is taken in unchanged; and the case page shows the findings the registry
would raise.

carrier_registry is imported inside each test so that, before it exists, the tests fail
instead of the file failing to collect.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from test_evidence_eval_approve import Base  # noqa: E402

OPERATOR = "operator@forwarder.example"
REVIEWER = "reviewer@forwarder.example"
PAYEE = "payments@mekong-haulage.example"
ATTACKER = "billing-update@mekong-haulage-pay.example"
NOTE = "called the carrier on the number in the contract; they confirmed the account"


def run(coro):
    return asyncio.run(coro)


def carrier():
    return f"Mekong Haulage {uuid.uuid4().hex[:6]} Co"


def register_and_verify(name, payee):
    reg = importlib.import_module("vf_logistics.carrier_registry")
    made = run(reg.register_payee(name, payee, OPERATOR, "password"))
    run(reg.verify_payee(made["carrier_key"], made["version"], REVIEWER, "password", NOTE))


def paid(name, **extra):
    return {
        "shipment_id": "SHP-REG-1", "carrier_name": name, "shipping_cost": 1300.0,
        "payout_amount": 1250.0, "paypal_authorization_id": "0VF52814937998046", **extra,
    }


class TestWhereTheRegistryIsApplied(Base):
    def test_a_payment_shipment_is_enriched_when_it_is_taken_in(self):
        from vf_logistics import orchestrator

        name = carrier()
        register_and_verify(name, PAYEE)
        case = run(orchestrator.ingest_shipment(paid(
            name, shipment_id=f"SHP-{uuid.uuid4().hex[:8]}", carrier_payee_email=ATTACKER,
            carrier_registered_payee_email=ATTACKER,
        )))
        assert case["shipment"]["carrier_registered_payee_email"] == PAYEE

    def test_a_shipment_that_moves_no_money_is_taken_in_unchanged(self):
        from vf_logistics import orchestrator

        case = run(orchestrator.ingest_shipment({
            "shipment_id": f"SHP-{uuid.uuid4().hex[:8]}", "carrier_name": "Anyone",
            "carrier_registered_payee_email": ATTACKER, "shipping_cost": 10.0,
        }))
        assert case["shipment"]["carrier_registered_payee_email"] == ATTACKER

    def test_the_case_page_shows_the_findings_the_registry_would_raise(self):
        from vf_logistics import payment_status
        from vf_logistics.store import get_store

        case_id = f"CASE-{uuid.uuid4().hex[:10]}"
        run(get_store().put_case({"case_id": case_id, "state": "HELD_FOR_REVIEW", "shipment": paid(
            carrier(), carrier_payee_email=ATTACKER, carrier_registered_payee_email=ATTACKER,
        )}))
        state = run(payment_status.payment_state(case_id))
        assert "PAYEE_UNVERIFIED" in state["findings"]
