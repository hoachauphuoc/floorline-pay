"""
The HS reference carries two ordinary headings that are not controlled: 6109 (knitted T-shirts) and 4412 (plywood).

Trials 7 and 8 (7 Nov 2026): a heading missing from the table is one the classifier invents the contents of, so a correct declaration was
called a mismatch twice. The two headings are plain, not dual-use and not residual; the dual-use coverage the table exists for is unchanged.
"""

from __future__ import annotations

import unittest

from vf_logistics import hs_reference


class TestHsReferenceOrdinaryGoods(unittest.TestCase):
    def test_it_names_knitted_t_shirts_under_6109(self):
        record = hs_reference.lookup("6109.10")
        self.assertIsNotNone(record)
        self.assertIn("t-shirt", record["covers"].lower())
        self.assertFalse(hs_reference.is_dual_use("6109"))
        self.assertFalse(hs_reference.is_residual("6109"))

    def test_it_names_plywood_under_4412(self):
        record = hs_reference.lookup("4412.33")
        self.assertIsNotNone(record)
        self.assertIn("plywood", record["covers"].lower())
        self.assertFalse(hs_reference.is_dual_use("4412"))
        self.assertFalse(hs_reference.is_residual("4412"))

    def test_the_prompt_block_carries_both_headings(self):
        block = hs_reference.reference_block(include_excludes=False)
        self.assertIn("6109", block)
        self.assertIn("4412", block)

    def test_the_dual_use_coverage_is_unchanged(self):
        report = hs_reference.coverage_report()
        self.assertEqual(report["missing_from_reference"], [])
        self.assertEqual(report["rules_heading_not_marked_dual_use"], [])


if __name__ == "__main__":
    unittest.main()
