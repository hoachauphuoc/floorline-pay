"""
More grounding rules, measured on the 12 live trial cases (grounding plan GR3b, 7 Nov 2026).

N/A, "not specified" and "metadata" are absence words and fields. A flag that calls the freight cost high is ungrounded only when the deterministic
freight check ran clean (no FREIGHT_* finding or observation in the validation), the charge is below the declared value, and both are real numbers.
Any doubt, a missing validation included, keeps the flag grounded.
"""

from __future__ import annotations

import unittest

COST_FLAG = "Price manipulation: shipping cost of 1500 USD for 100kg from Ho Chi Minh City to Hanoi is unusually high"
RECORD = {"shipping_cost": 1500, "declared_value": 5000, "shipper_tx_count": 287}
CLEAN = {"findings": [], "observations": []}


def grounding():
    from vf_logistics import grounding as module

    return module


class TestMoreAbsenceWords(unittest.TestCase):
    def test_a_field_marked_n_a_that_the_record_lacks_is_an_absence(self):
        self.assertEqual(grounding().classify_flag("Route is N/A, preventing validation of the path", RECORD), "absence")

    def test_a_field_not_specified_that_the_record_carries_is_ungrounded(self):
        record = dict(RECORD, route_details="direct sailing")
        self.assertEqual(grounding().classify_flag("Route not specified", record), "ungrounded")

    def test_missing_shipment_metadata_is_an_absence_when_any_of_its_fields_is_missing(self):
        record = dict(RECORD, route_details="direct sailing", status="pending")
        self.assertEqual(grounding().classify_flag("Missing shipment metadata", record), "absence")

    def test_missing_shipment_metadata_is_ungrounded_when_all_its_fields_are_there(self):
        record = dict(RECORD, route_details="direct sailing", status="pending", created_at="2026-11-07")
        self.assertEqual(grounding().classify_flag("Missing shipment metadata", record), "ungrounded")

    def test_absence_of_the_historical_average_is_an_absence_when_the_record_lacks_it(self):
        flag = "Absence of historical average cost prevents benchmarking shipping cost against typical rates."
        self.assertEqual(grounding().classify_flag(flag, RECORD), "absence")


class TestCostFlag(unittest.TestCase):
    def test_a_high_cost_flag_is_ungrounded_when_the_freight_check_ran_clean(self):
        self.assertEqual(grounding().classify_flag(COST_FLAG, RECORD, CLEAN), "ungrounded")

    def test_it_stays_grounded_without_a_validation(self):
        self.assertEqual(grounding().classify_flag(COST_FLAG, RECORD), "grounded")
        self.assertEqual(grounding().classify_flag(COST_FLAG, RECORD, None), "grounded")

    def test_it_stays_grounded_when_the_freight_check_raised_a_finding(self):
        validation = {"findings": [{"code": "FREIGHT_ANOMALY"}], "observations": []}
        self.assertEqual(grounding().classify_flag(COST_FLAG, RECORD, validation), "grounded")

    def test_it_stays_grounded_when_the_freight_check_recorded_an_observation(self):
        validation = {"findings": [], "observations": [{"code": "FREIGHT_ABOVE_LANE_TYPICAL"}]}
        self.assertEqual(grounding().classify_flag(COST_FLAG, RECORD, validation), "grounded")

    def test_it_stays_grounded_when_the_charge_is_above_the_declared_value(self):
        record = dict(RECORD, declared_value=500)
        self.assertEqual(grounding().classify_flag(COST_FLAG, record, CLEAN), "grounded")

    def test_it_stays_grounded_when_a_number_is_missing_or_not_a_number(self):
        for record in (dict(RECORD, shipping_cost=None), dict(RECORD, declared_value="5000"), dict(RECORD, shipping_cost=0), dict(RECORD, shipping_cost=True)):
            self.assertEqual(grounding().classify_flag(COST_FLAG, record, CLEAN), "grounded")

    def test_it_stays_grounded_when_the_validation_has_no_observation_list(self):
        self.assertEqual(grounding().classify_flag(COST_FLAG, RECORD, {"findings": []}), "grounded")
        self.assertEqual(grounding().classify_flag(COST_FLAG, RECORD, "clean"), "grounded")

    def test_a_flag_about_the_declared_value_alone_is_not_touched(self):
        flag = "Declared value of 5000 USD is unusually high for T-shirts"
        self.assertEqual(grounding().classify_flag(flag, RECORD, CLEAN), "grounded")

    def test_classify_passes_the_validation_to_every_flag(self):
        result = grounding().classify([COST_FLAG, "Weight looks wrong"], RECORD, CLEAN)
        self.assertEqual([f["label"] for f in result["flags"]], ["ungrounded", "grounded"])
        self.assertEqual((result["grounded"], result["ungrounded"], result["absence"]), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
