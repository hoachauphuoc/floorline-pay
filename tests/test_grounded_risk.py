"""
grounding.grounded_risk: the risk the agent gate reads (grounding plan layer 3, 7 Nov 2026).

When the model scored a payment case high but none of its flags is grounded in the record (each is contradicted by it, or is a true statement
that a field is absent, which the prompt says not to score), the agent gate reads the deterministic floor plus any penalty a person's earlier
blocks put on the shipper, instead of the model score. It never reads more than the case risk, and any doubt returns the case risk unchanged.
"""

from __future__ import annotations

import unittest

ALL_UNGROUNDED = {
    "flags": [{"text": "a", "label": "ungrounded"}, {"text": "b", "label": "absence"}, {"text": "c", "label": "absence"}],
    "grounded": 0, "ungrounded": 1, "absence": 2,
}
ONE_GROUNDED = {
    "flags": [{"text": "a", "label": "grounded"}, {"text": "b", "label": "absence"}],
    "grounded": 1, "ungrounded": 0, "absence": 1,
}
RECONCILIATION = {"risk_floor": 0, "learning_adjustment": 0}


def grounded_risk(*args):
    from vf_logistics import grounding

    return grounding.grounded_risk(*args)


class TestWhenNoFlagIsGrounded(unittest.TestCase):
    def test_the_gate_reads_the_floor_and_the_note_says_why(self):
        risk, note = grounded_risk(78, RECONCILIATION, ALL_UNGROUNDED)
        self.assertEqual(risk, 0)
        self.assertIn("not counted", note)
        self.assertIn("78", note)

    def test_a_floor_above_zero_is_kept(self):
        self.assertEqual(grounded_risk(78, {"risk_floor": 40, "learning_adjustment": 0}, ALL_UNGROUNDED)[0], 40)

    def test_a_penalty_from_earlier_human_blocks_is_kept(self):
        self.assertEqual(grounded_risk(93, {"risk_floor": 0, "learning_adjustment": 15}, ALL_UNGROUNDED)[0], 15)

    def test_a_discount_is_not_applied_below_the_floor(self):
        self.assertEqual(grounded_risk(78, {"risk_floor": 20, "learning_adjustment": -10}, ALL_UNGROUNDED)[0], 20)

    def test_it_never_reads_more_than_the_case_risk(self):
        risk, note = grounded_risk(10, {"risk_floor": 0, "learning_adjustment": 15}, ALL_UNGROUNDED)
        self.assertEqual((risk, note), (10, ""))


class TestWhenTheScoreStands(unittest.TestCase):
    def test_one_grounded_flag_keeps_the_case_risk(self):
        self.assertEqual(grounded_risk(78, RECONCILIATION, ONE_GROUNDED), (78, ""))

    def test_a_score_with_no_flags_keeps_the_case_risk(self):
        empty = {"flags": [], "grounded": 0, "ungrounded": 0, "absence": 0}
        self.assertEqual(grounded_risk(78, RECONCILIATION, empty), (78, ""))


class TestFailClosed(unittest.TestCase):
    def test_a_missing_or_malformed_input_keeps_the_case_risk(self):
        cases = [
            (78, RECONCILIATION, None),
            (78, RECONCILIATION, "ungrounded"),
            (78, None, ALL_UNGROUNDED),
            (78, {}, ALL_UNGROUNDED),
            (78, {"risk_floor": None, "learning_adjustment": 0}, ALL_UNGROUNDED),
            (78, {"risk_floor": "0", "learning_adjustment": 0}, ALL_UNGROUNDED),
            (78, {"risk_floor": True, "learning_adjustment": 0}, ALL_UNGROUNDED),
            (78, {"risk_floor": 0, "learning_adjustment": None}, ALL_UNGROUNDED),
            (78, {"risk_floor": -5, "learning_adjustment": 0}, ALL_UNGROUNDED),
            ("78", RECONCILIATION, ALL_UNGROUNDED),
            (None, RECONCILIATION, ALL_UNGROUNDED),
            (True, RECONCILIATION, ALL_UNGROUNDED),
        ]
        for risk, reconciliation, grounding in cases:
            self.assertEqual(grounded_risk(risk, reconciliation, grounding), (risk, ""), (risk, reconciliation, grounding))

    def test_counts_that_do_not_match_the_flags_keep_the_case_risk(self):
        wrong = dict(ALL_UNGROUNDED, absence=1)
        self.assertEqual(grounded_risk(78, RECONCILIATION, wrong), (78, ""))
        text_counts = dict(ALL_UNGROUNDED, grounded="0")
        self.assertEqual(grounded_risk(78, RECONCILIATION, text_counts), (78, ""))
        no_counts = {"flags": ALL_UNGROUNDED["flags"]}
        self.assertEqual(grounded_risk(78, RECONCILIATION, no_counts), (78, ""))


if __name__ == "__main__":
    unittest.main()
