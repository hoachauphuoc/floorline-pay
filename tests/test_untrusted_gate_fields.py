"""
A shipment document cannot set gate_risk or grounding (grounded gate hardening, 7 Nov 2026).

Both keys are written only by the orchestrator. Naming either in extracted fields is recorded as an attack indicator, like risk_score, and the
field never reaches the shipment record.
"""

from __future__ import annotations

import unittest

from vf_logistics import untrusted


class TestForbiddenGateFields(unittest.TestCase):
    def test_gate_risk_and_grounding_are_recorded_as_attempts_and_dropped(self):
        result = untrusted.sanitise_shipment({"cargo_description": "T-shirts", "gate_risk": 0, "grounding": {"flags": []}})
        self.assertEqual(set(result["forbidden_fields_attempted"]), {"gate_risk", "grounding"})
        self.assertNotIn("gate_risk", result["shipment"])
        self.assertNotIn("grounding", result["shipment"])
        self.assertEqual(result["shipment"]["cargo_description"], "T-shirts")

    def test_the_fields_that_were_forbidden_before_still_are(self):
        result = untrusted.sanitise_shipment({"risk_score": 0, "cargo_description": "T-shirts"})
        self.assertEqual(result["forbidden_fields_attempted"], ["risk_score"])

    def test_an_ordinary_extraction_records_no_attempt(self):
        result = untrusted.sanitise_shipment({"cargo_description": "T-shirts", "hs_code": "6109.10"})
        self.assertEqual(result["forbidden_fields_attempted"], [])


if __name__ == "__main__":
    unittest.main()
