"""
grounding.py labels each flag the fraud model raised against the shipment record (grounding plan layer 2, 7 Nov 2026).

On CASE-SHP-PAY-DEMO-9 and DEMO-12 the model listed a missing route, creation date and status as risk flags although the prompt says absence is
not scored, and called a count of 412 shipments unusually high. A flag that contradicts the record is ungrounded; a true statement that a
field is absent is labelled absence; anything the module cannot read stays grounded, so the module can never talk a flag away by accident.
"""

from __future__ import annotations

import unittest

RECORD = {
    "shipment_id": "S1",
    "declared_value": 5000,
    "shipping_cost": 1500,
    "shipper_tx_count": 412,
    "route_details": "HCMC to Hanoi",
}


def grounding():
    from vf_logistics import grounding as module

    return module


class TestAbsenceFlags(unittest.TestCase):
    def test_a_missing_field_that_is_really_absent_is_labelled_absence(self):
        self.assertEqual(grounding().classify_flag("Missing creation date", RECORD), "absence")

    def test_a_missing_field_that_the_record_carries_is_ungrounded(self):
        self.assertEqual(grounding().classify_flag("Route information is missing", RECORD), "ungrounded")

    def test_one_absent_field_among_present_ones_keeps_the_flag_an_absence(self):
        flag = "Missing route, creation date and status data"
        self.assertEqual(grounding().classify_flag(flag, RECORD), "absence")

    def test_an_absence_flag_about_an_unknown_field_stays_grounded(self):
        self.assertEqual(grounding().classify_flag("Missing insurance certificate", RECORD), "grounded")

    def test_placeholder_values_count_as_absent(self):
        record = dict(RECORD, status="N/A", created_at="")
        self.assertEqual(grounding().classify_flag("Status is missing", record), "absence")
        self.assertEqual(grounding().classify_flag("Creation date unavailable", record), "absence")


class TestTransactionCountFlags(unittest.TestCase):
    def test_calling_an_established_shippers_count_unusually_high_is_ungrounded(self):
        flag = "Shipper transaction count unusually high (412)"
        self.assertEqual(grounding().classify_flag(flag, RECORD), "ungrounded")

    def test_a_small_count_called_high_stays_grounded(self):
        record = dict(RECORD, shipper_tx_count=12)
        flag = "Shipper transaction count unusually high (12)"
        self.assertEqual(grounding().classify_flag(flag, record), "grounded")


class TestFailClosed(unittest.TestCase):
    def test_a_flag_about_anything_else_stays_grounded(self):
        self.assertEqual(grounding().classify_flag("Price manipulation: cost unusually high", RECORD), "grounded")

    def test_a_flag_that_is_not_text_stays_grounded(self):
        self.assertEqual(grounding().classify_flag(None, RECORD), "grounded")
        self.assertEqual(grounding().classify_flag(12345, RECORD), "grounded")

    def test_a_flag_given_as_an_object_is_read_from_its_description(self):
        flag = {"severity": "HIGH", "description": "Missing creation date"}
        self.assertEqual(grounding().classify_flag(flag, RECORD), "absence")

    def test_a_record_that_is_not_a_dict_leaves_every_flag_grounded(self):
        self.assertEqual(grounding().classify_flag("Missing creation date", None), "grounded")


class TestClassify(unittest.TestCase):
    def test_it_labels_every_flag_and_counts_each_label(self):
        flags = [
            "Missing creation date",
            "Route information is missing",
            "Price manipulation: cost unusually high",
            "Shipper transaction count unusually high (412)",
        ]
        result = grounding().classify(flags, RECORD)
        self.assertEqual([f["label"] for f in result["flags"]], ["absence", "ungrounded", "grounded", "ungrounded"])
        self.assertEqual(result["flags"][2]["text"], "Price manipulation: cost unusually high")
        self.assertEqual((result["grounded"], result["ungrounded"], result["absence"]), (1, 2, 1))

    def test_no_flags_or_a_flag_list_of_the_wrong_type_gives_zero_counts(self):
        for flags in (None, [], "Missing creation date", {"a": 1}):
            result = grounding().classify(flags, RECORD)
            self.assertEqual(result["flags"], [])
            self.assertEqual((result["grounded"], result["ungrounded"], result["absence"]), (0, 0, 0))

    def test_a_long_flag_text_is_cut_to_200_characters(self):
        result = grounding().classify(["x" * 500], RECORD)
        self.assertEqual(len(result["flags"][0]["text"]), 200)


if __name__ == "__main__":
    unittest.main()
