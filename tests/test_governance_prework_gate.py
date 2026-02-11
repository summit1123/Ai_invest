from __future__ import annotations

import unittest

from ai_invest.meetings.governance_meeting import should_block_prework


class GovernancePreworkGateTests(unittest.TestCase):
    def test_no_block_when_not_required(self) -> None:
        self.assertFalse(should_block_prework(require_prework_reports=False, prework={"missing": ["research_agent"]}))

    def test_block_when_required_and_missing(self) -> None:
        self.assertTrue(should_block_prework(require_prework_reports=True, prework={"missing": ["risk_manager"], "stale": []}))

    def test_block_when_required_and_stale(self) -> None:
        self.assertTrue(should_block_prework(require_prework_reports=True, prework={"missing": [], "stale": ["ops_manager"]}))

    def test_no_block_when_required_and_all_fresh(self) -> None:
        self.assertFalse(should_block_prework(require_prework_reports=True, prework={"missing": [], "stale": []}))


if __name__ == "__main__":
    unittest.main()

