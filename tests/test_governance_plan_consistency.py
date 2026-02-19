from __future__ import annotations

import unittest

from ai_invest.meetings.governance_meeting import _build_plan_consistency_checks


class GovernancePlanConsistencyTests(unittest.TestCase):
    def test_blocked_plan_requires_buy_false_and_zero_target(self) -> None:
        out = _build_plan_consistency_checks(
            hard_plan_block=False,
            hard_plan_block_reasons=[],
            soft_plan_block=True,
            soft_plan_block_reasons=["ops.veto=true"],
            activation_decision_effective="PAPER",
            paper_data_collection_applied=True,
            allowed_actions={"buy": True, "sell": True},
            target_position_pct=3.0,
            notes="",
        )
        self.assertFalse(bool(out.get("passed")))
        self.assertIn("blocked_plan_must_not_buy", list(out.get("failed_checks") or []))
        self.assertIn("blocked_plan_target_must_be_zero", list(out.get("failed_checks") or []))

    def test_hold_decision_requires_flat_execution(self) -> None:
        out = _build_plan_consistency_checks(
            hard_plan_block=False,
            hard_plan_block_reasons=[],
            soft_plan_block=False,
            soft_plan_block_reasons=[],
            activation_decision_effective="HOLD",
            paper_data_collection_applied=False,
            allowed_actions={"buy": False, "sell": False},
            target_position_pct=0.0,
            notes="",
        )
        self.assertTrue(bool(out.get("passed")))
        self.assertEqual(list(out.get("failed_checks") or []), [])

    def test_no_trade_notes_conflict_detected(self) -> None:
        out = _build_plan_consistency_checks(
            hard_plan_block=False,
            hard_plan_block_reasons=[],
            soft_plan_block=False,
            soft_plan_block_reasons=[],
            activation_decision_effective="PAPER",
            paper_data_collection_applied=False,
            allowed_actions={"buy": True, "sell": True},
            target_position_pct=2.0,
            notes="execution blocked / no-trade 권고",
        )
        self.assertFalse(bool(out.get("passed")))
        self.assertIn("notes_no_trade_must_not_conflict", list(out.get("failed_checks") or []))


if __name__ == "__main__":
    unittest.main()
