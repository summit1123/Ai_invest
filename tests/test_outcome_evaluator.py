from __future__ import annotations

import unittest

from ai_invest.domain.reason_codes import ReasonCode
from ai_invest.learning.outcome_evaluator import classify_outcome_label, evaluate_closed_trade


class OutcomeEvaluatorTests(unittest.TestCase):
    def test_classify_outcome_label(self) -> None:
        self.assertEqual(classify_outcome_label(realized_pnl_krw=10.0), "WIN")
        self.assertEqual(classify_outcome_label(realized_pnl_krw=-10.0), "LOSS")
        self.assertEqual(classify_outcome_label(realized_pnl_krw=0.5, flat_tolerance_krw=1.0), "FLAT")

    def test_cost_underestimated_pattern(self) -> None:
        ev = evaluate_closed_trade(
            qty=1.0,
            avg_entry_price=100.0,
            avg_exit_price=101.0,
            realized_pnl_krw=-1.0,
            fees_total_krw=2.0,
            flat_tolerance_krw=0.0,
        )
        self.assertEqual(ev.outcome_label, "LOSS")
        self.assertEqual(ev.error_type, ReasonCode.OC_COST_UNDERESTIMATED.value)
        self.assertIsNotNone(ev.fix_hypothesis)

    def test_loss_without_unambiguous_error_type(self) -> None:
        ev = evaluate_closed_trade(
            qty=1.0,
            avg_entry_price=100.0,
            avg_exit_price=99.0,
            realized_pnl_krw=-1.0,
            fees_total_krw=0.0,
            flat_tolerance_krw=0.0,
        )
        self.assertEqual(ev.outcome_label, "LOSS")
        self.assertIsNone(ev.error_type)


if __name__ == "__main__":
    unittest.main()

