"""
The compliance agent names the parties from the company fields (grounding plan layer 1, 7 Nov 2026).

The Book and pay form sends shipper_company, receiver_company and shipper_tax_id, never a personal name. The agent printed "Name: N/A" for both
parties, so on CASE-SHP-PAY-DEMO-13 the model asked for "full legal names" of parties the record named, and the two adverse-media searches, which
used the personal-name fields only, never ran. A personal name still wins when the record has one; a placeholder name falls back to the company.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from vf_logistics import tavily_client
from vf_logistics.agents import compliance_agent

REPLY = '{"compliance_status": "CLEARED", "compliance_score": 90}'

SHIPMENT = {
    "shipment_id": "S1",
    "shipper_company": "Viet Tien Garment JSC",
    "shipper_tax_id": "0302887411",
    "shipper_country": "Vietnam",
    "receiver_company": "Hanoi Garment Retail Co",
    "receiver_country": "Vietnam",
}


def screen(shipment: dict) -> tuple[str, list[str]]:
    complete = AsyncMock(return_value=(REPLY, 10, 10))
    search = AsyncMock(return_value=([], tavily_client.OK, False))
    with patch.object(compliance_agent.nebius_client, "complete_json", complete), \
            patch.object(tavily_client, "search_cached", search):
        asyncio.run(compliance_agent.screen_shipment(shipment))
    queries = [call.args[0] for call in search.call_args_list]
    return complete.call_args.kwargs["user_text"], queries


class TestComplianceNamesTheParties(unittest.TestCase):
    def test_the_record_names_both_parties_from_the_company_fields(self):
        text, _ = screen(SHIPMENT)
        self.assertIn("- Name: Viet Tien Garment JSC", text)
        self.assertIn("- Name: Hanoi Garment Retail Co", text)
        self.assertNotIn("Name: N/A", text)

    def test_the_adverse_media_searches_name_both_companies(self):
        _, queries = screen(SHIPMENT)
        self.assertTrue(any("Viet Tien Garment JSC" in q and "sanctions" in q for q in queries))
        self.assertTrue(any("Hanoi Garment Retail Co" in q for q in queries))
        self.assertEqual(len(queries), 2)

    def test_a_personal_name_wins_over_the_company(self):
        shipment = dict(SHIPMENT, shipper_name="Nguyen Van A", receiver_name="Tran Thi B")
        text, queries = screen(shipment)
        self.assertIn("- Name: Nguyen Van A", text)
        self.assertIn("- Name: Tran Thi B", text)
        self.assertTrue(any("Nguyen Van A" in q for q in queries))
        self.assertTrue(any("Tran Thi B" in q for q in queries))
        self.assertFalse(any("Viet Tien Garment JSC" in q for q in queries))

    def test_a_placeholder_name_falls_back_to_the_company_for_the_search(self):
        shipment = dict(SHIPMENT, shipper_name="N/A", receiver_name="not stated")
        _, queries = screen(shipment)
        self.assertTrue(any("Viet Tien Garment JSC" in q for q in queries))
        self.assertTrue(any("Hanoi Garment Retail Co" in q for q in queries))

    def test_it_says_not_available_and_searches_nothing_when_the_record_names_no_party(self):
        text, queries = screen({"shipment_id": "S1"})
        self.assertIn("- Name: N/A", text)
        self.assertEqual(queries, [])


if __name__ == "__main__":
    unittest.main()
