"""
The counterparty book holds one fictional demo shipper (7 Nov 2026).

Trials 14 to 16: a real company name returns web results that the compliance model reads as adverse. The demo shipper has no web footprint, so a trial
on it tests the payment path, not the web. It is a normal book entry: both the tax ID and the company must agree, and the other entries are unchanged.
"""

from __future__ import annotations

import unittest

from vf_logistics import shipper_registry

COMPANY = "Lotus Demo Ceramics Co Ltd"
TAX_ID = "0399000101"


class TestDemoShipperInTheBook(unittest.TestCase):
    def test_tax_id_and_company_together_verify(self):
        result = shipper_registry.lookup(TAX_ID, COMPANY)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["prior_flags"], 0)
        self.assertGreaterEqual(result["tx_count"], 100)

    def test_the_tax_id_under_another_company_is_a_mismatch(self):
        result = shipper_registry.lookup(TAX_ID, "Someone Else Co Ltd")
        self.assertEqual(result["status"], "identity_mismatch")
        self.assertEqual(result["on_file_company"], COMPANY)

    def test_the_company_under_another_tax_id_is_unknown(self):
        result = shipper_registry.lookup("0399000102", COMPANY)
        self.assertEqual(result["status"], "unknown")

    def test_enrich_records_the_verified_history(self):
        shipment = {"shipper_tax_id": TAX_ID, "shipper_company": COMPANY}
        shipper_registry.enrich(shipment)
        self.assertEqual(shipment["shipper_identity_status"], "verified")
        self.assertGreaterEqual(shipment["shipper_tx_count"], 100)

    def test_the_existing_entries_are_unchanged(self):
        saigon = shipper_registry.lookup("0301234567", "Saigon Textile Export JSC")
        self.assertEqual((saigon["status"], saigon["tx_count"], saigon["prior_flags"]), ("verified", 412, 0))
        truong = shipper_registry.lookup("0400512983", "Truong Hai Logistics Co Ltd")
        self.assertEqual((truong["status"], truong["tx_count"], truong["prior_flags"]), ("verified", 158, 1))


if __name__ == "__main__":
    unittest.main()
