"""
The HS reference carries the two ceramic tableware headings, 6911 and 6912, as ordinary goods (7 Nov 2026).

Trial 15: a heading missing from the table is one the classifier invents the contents of, so a correct 6912 declaration for ceramic tableware was
called a mismatch (it suggested 6905, which is roofing tiles). Both headings are plain, not dual-use and not residual.
"""

from __future__ import annotations

import unittest

from vf_logistics import hs_reference


class TestHsReferenceCeramicTableware(unittest.TestCase):
    def test_it_names_porcelain_tableware_under_6911(self):
        record = hs_reference.lookup("6911.10")
        self.assertIsNotNone(record)
        self.assertIn("porcelain", record["covers"].lower())
        self.assertFalse(hs_reference.is_dual_use("6911"))
        self.assertFalse(hs_reference.is_residual("6911"))

    def test_it_names_other_ceramic_tableware_under_6912(self):
        record = hs_reference.lookup("6912.00")
        self.assertIsNotNone(record)
        self.assertIn("tableware", record["covers"].lower())
        self.assertFalse(hs_reference.is_dual_use("6912"))
        self.assertFalse(hs_reference.is_residual("6912"))

    def test_the_prompt_block_carries_both_headings(self):
        block = hs_reference.reference_block(include_excludes=False)
        self.assertIn("6911", block)
        self.assertIn("6912", block)

    def test_the_dual_use_coverage_is_unchanged(self):
        report = hs_reference.coverage_report()
        self.assertEqual(report["missing_from_reference"], [])
        self.assertEqual(report["rules_heading_not_marked_dual_use"], [])


if __name__ == "__main__":
    unittest.main()
