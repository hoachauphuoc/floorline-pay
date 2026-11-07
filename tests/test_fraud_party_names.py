"""
The fraud agent names the parties from the company fields when the record has no personal name (trial 5, 7 Nov 2026).

The Book and pay form sends shipper_company and receiver_company. The prompt used to read only shipper_name and receiver_name, so the
model was told "Shipper: N/A" for every booked case. A personal name still wins when the record has one; with neither, the prompt says N/A.
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


class TestFraudPartyNames(unittest.TestCase):
    def test_it_names_the_parties_from_the_company_fields(self):
        text = prompt_for({
            "shipment_id": "S1",
            "shipper_company": "Saigon Textile Export JSC",
            "receiver_company": "Hanoi Garment Retail Co",
        })
        self.assertIn("Shipper: Saigon Textile Export JSC", text)
        self.assertIn("Receiver: Hanoi Garment Retail Co", text)

    def test_a_personal_name_wins_over_the_company(self):
        text = prompt_for({
            "shipment_id": "S1",
            "shipper_name": "Nguyen Van A",
            "shipper_company": "Saigon Textile Export JSC",
            "receiver_name": "Tran Thi B",
            "receiver_company": "Hanoi Garment Retail Co",
        })
        self.assertIn("Shipper: Nguyen Van A", text)
        self.assertIn("Receiver: Tran Thi B", text)
        self.assertNotIn("Saigon Textile Export JSC", text)

    def test_it_says_not_available_when_the_record_names_no_party(self):
        text = prompt_for({"shipment_id": "S1"})
        self.assertIn("Shipper: N/A", text)
        self.assertIn("Receiver: N/A", text)


if __name__ == "__main__":
    unittest.main()
