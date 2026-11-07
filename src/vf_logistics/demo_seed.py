"""
The starting state of the public demo, for a deployment whose in-memory store is empty after every restart.

VF_SEED_PAYMENT_DEMO=true makes the app run `seed` once as it starts. It does only two things, and both are visible in the
audit trail as demo data: it gives one carrier a payee that two named seed identities registered and verified (so a judge can
book, release and pay without first entering a payee), and it freezes another carrier (so the Settlement page has a party in
dispute to show). It calls no model, talks to no PayPal endpoint and writes no payment: every payment a judge sees was made
by that judge.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from google.api_core.exceptions import DeadlineExceeded, ResourceExhausted, RetryError, ServiceUnavailable

from vf_logistics import carrier_registry, payment_freeze

CARRIER = "Mekong Haulage Co"
FROZEN_CARRIER = "Aster Freight Co"
REGISTRANT = "demo-seed-registrant@floorline.example"
VERIFIER = "demo-seed-verifier@floorline.example"
_NOTE = "demo seed: the payee is the sandbox carrier account of this deployment"
logger = logging.getLogger(__name__)
_STORE_DOWN = (ResourceExhausted, ServiceUnavailable, DeadlineExceeded, RetryError)


class DemoSeedError(Exception):
    """The seed was asked for but cannot be made; the start is refused so the missing setting is seen in the logs."""


async def seed(payee_email: str) -> dict[str, Any]:
    """Make the demo's starting state. Safe to run again: a payee that is already verified is never replaced."""
    registered = False
    if await carrier_registry.current(CARRIER) is None:
        made = await carrier_registry.register_payee(CARRIER, payee_email, REGISTRANT, "password")
        await carrier_registry.verify_payee(made["carrier_key"], made["version"], VERIFIER, "password", _NOTE)
        registered = True
    frozen = await payment_freeze.freeze("carrier", FROZEN_CARRIER, "demo data: a dispute was opened on a payment", "demo-seed")
    return {"carrier": CARRIER, "registered_payee": registered, "frozen_carrier": FROZEN_CARRIER, "frozen": frozen}


def maybe_seed() -> dict[str, Any] | None:
    """Run the seed when VF_SEED_PAYMENT_DEMO is true; VF_DEMO_PAYEE_EMAIL names the sandbox account demo payouts go to."""
    if os.getenv("VF_SEED_PAYMENT_DEMO", "").strip().lower() != "true":
        return None
    payee = os.getenv("VF_DEMO_PAYEE_EMAIL", "").strip()
    if not payee:
        raise DemoSeedError(
            "VF_SEED_PAYMENT_DEMO is true but VF_DEMO_PAYEE_EMAIL is not set; "
            "set it to the sandbox carrier account that demo payouts go to",
        )
    try:
        return asyncio.run(seed(payee))
    except _STORE_DOWN as error:
        # A spent read quota or an outage must not stop the app from starting: a worker that cannot boot takes the live service down with it.
        # Nothing is seeded, so nothing is paid on a payee that was never verified; the next start seeds again.
        logger.warning("demo seed skipped, the store is unavailable (%s): %s", type(error).__name__, error)
        return None
