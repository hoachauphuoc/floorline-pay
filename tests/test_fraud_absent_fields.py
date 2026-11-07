"""
The fraud agent says "not available to this agent" for a status or route the record does not carry (trials 5 and 6, 7 Nov 2026).

The Book and pay form sends neither field. A bare "N/A" is read by the model as a fact about the shipment, so absent fields are named
as an absence of input, as created_at and the route average already are. A value the record does carry is shown unchanged.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from vf_logistics.agents import fraud_detection_agent

REPLY = '{"risk_score": 10, "risk_level": "low"}'


def prompt_for(shipment: dict) -> str:
    complete = AsyncMock(return_value=(REPLY, 10, 10))
    with patch.object(fraud_detection_agent.nebius_client, "complete_json", complete):
        asyncio.run(fraud_detection_agent.analyze_shipment(shipment))
    return complete.call_args.kwargs["user_text"]


class TestFraudAbsentFields(unittest.TestCase):
    def test_it_names_an_absent_status_and_route_as_not_available(self):
        text = prompt_for({"shipment_id": "S1"})
        self.assertIn("Status: not available to this agent", text)
        self.assertIn("Route: not available to this agent", text)
        self.assertNotIn("Status: N/A", text)
        self.assertNotIn("Route: N/A", text)

    def test_it_shows_a_status_and_route_the_record_carries(self):
        text = prompt_for({"shipment_id": "S1", "status": "in transit", "route_details": "HCMC to Hanoi direct"})
        self.assertIn("Status: in transit", text)
        self.assertIn("Route: HCMC to Hanoi direct", text)


if __name__ == "__main__":
    unittest.main()
