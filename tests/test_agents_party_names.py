"""
The investigation and debate agents name the parties from the company fields (grounding plan layer 1, 7 Nov 2026).

The Book and pay form sends shipper_company, receiver_company and shipper_tax_id, never a personal name. The investigation agent read only
shipper_name and its entity profile printed no tax id or country, so on CASE-SHP-PAY-DEMO-9 it reported "Shipper name absent; no
registration number or tax ID" about a record that carried both. A personal name still wins when the record has one.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from vf_logistics import tavily_client
from vf_logistics.agents import debate_agent, investigation_agent

REPLY = '{"confidence_level": 0.5}'

SHIPMENT = {
    "shipment_id": "S1",
    "shipper_company": "Saigon Textile Export JSC",
    "receiver_company": "Hanoi Garment Retail Co",
    "shipper_country": "Vietnam",
    "shipper_tax_id": "0301234567",
}
ENTITY = {
    "name": None,
    "company": "Saigon Textile Export JSC",
    "country": "Vietnam",
    "tax_id": "0301234567",
    "transaction_count": 412,
}


def investigate(shipment: dict, entity: dict) -> tuple[str, list[str]]:
    complete = AsyncMock(return_value=(REPLY, 10, 10))
    search = AsyncMock(return_value=([], tavily_client.OK))
    case = {"case_id": "C1", "primary_shipment": shipment, "primary_entity": entity}
    with patch.object(investigation_agent.nebius_client, "complete_json", complete), \
            patch.object(investigation_agent.tavily_client, "search_with_status", search):
        asyncio.run(investigation_agent.investigate_case(case))
    queries = [call.args[0] for call in search.call_args_list]
    return complete.call_args.kwargs["user_text"], queries


class TestInvestigationPartyNames(unittest.TestCase):
    def test_the_shipment_block_names_the_shipper_from_the_company(self):
        text, _ = investigate(SHIPMENT, ENTITY)
        self.assertIn("Shipper: Saigon Textile Export JSC", text)

    def test_the_entity_profile_carries_name_tax_id_and_country(self):
        text, _ = investigate(SHIPMENT, ENTITY)
        self.assertIn("Name: Saigon Textile Export JSC", text)
        self.assertIn("Tax ID: 0301234567", text)
        self.assertIn("Country: Vietnam", text)

    def test_the_web_search_names_both_companies(self):
        _, queries = investigate(SHIPMENT, ENTITY)
        self.assertTrue(any("Saigon Textile Export JSC" in q and "Hanoi Garment Retail Co" in q for q in queries))

    def test_a_personal_name_wins_over_the_company(self):
        shipment = dict(SHIPMENT, shipper_name="Nguyen Van A", receiver_name="Tran Thi B")
        entity = dict(ENTITY, name="Nguyen Van A")
        text, queries = investigate(shipment, entity)
        self.assertIn("Shipper: Nguyen Van A", text)
        self.assertIn("Name: Nguyen Van A", text)
        self.assertTrue(any("Nguyen Van A" in q and "Tran Thi B" in q for q in queries))

    def test_it_says_not_available_when_the_record_names_no_party(self):
        text, queries = investigate({"shipment_id": "S1"}, {"transaction_count": 3})
        self.assertIn("Shipper: N/A", text)
        self.assertIn("Name: N/A", text)
        self.assertNotIn("Tax ID: 0301234567", text)
        self.assertEqual(queries, [])


class TestDebatePartyNames(unittest.TestCase):
    def test_the_context_names_the_receiver_company(self):
        text = debate_agent._build_debate_context({"case_id": "C1", "shipment": SHIPMENT, "steps": []})
        self.assertIn("Receiver Company: Hanoi Garment Retail Co", text)
        self.assertIn("Shipper Company: Saigon Textile Export JSC", text)


if __name__ == "__main__":
    unittest.main()
