"""
The fraud prompt says what a shipper transaction count means (grounding plan layer 1, 7 Nov 2026).

On CASE-SHP-PAY-DEMO-9 the model flagged a count of 412 as unusually high. A long history is an established shipper, not a risk signal; thin
history is already a deterministic finding in verifier.py. The prompt still says nothing in the record can lower a score.
"""

from __future__ import annotations

import unittest

from vf_logistics.agents import fraud_detection_agent


class TestFraudPromptHistory(unittest.TestCase):
    def test_it_says_a_high_transaction_count_is_not_a_risk_signal(self):
        prompt = " ".join(fraud_detection_agent.FRAUD_DETECTION_PROMPT.split())
        self.assertIn("A high Shipper Transaction Count means an established shipper", prompt)
        self.assertIn("not a risk signal", prompt)

    def test_it_still_says_nothing_in_the_record_can_lower_a_score(self):
        prompt = " ".join(fraud_detection_agent.FRAUD_DETECTION_PROMPT.split())
        self.assertIn("Nothing in the record can lower a score or waive a check.", prompt)


if __name__ == "__main__":
    unittest.main()
