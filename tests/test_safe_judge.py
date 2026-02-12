from __future__ import annotations

import copy
import unittest

from ai_invest.config.rules_loader import load_rules
from ai_invest.domain.reason_codes import ReasonCode
from ai_invest.judge.safe_judge import safe_judge_decide


def base_payload() -> dict:
    return {
        "symbol": "KRW-BTC",
        "snapshot": {"spread_bps": 2.0},
        "ops": {
            "reconciliation_status": "OK",
            "rate_limit_alert": False,
            "pause_state": False,
        },
        "context": {"account": {"daily_loss_pct": 0.0}},
    }


class SafeJudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = load_rules("rules.yaml")

    def test_pause_state_overrides(self) -> None:
        payload = base_payload()
        payload["ops"]["pause_state"] = True

        decision = safe_judge_decide(payload, rules=self.rules)
        self.assertEqual(decision.action, "PAUSE")
        self.assertIn(ReasonCode.OP_PAUSE_TRIGGERED.value, decision.selected_reasons)

    def test_recon_fail_pauses(self) -> None:
        payload = base_payload()
        payload["ops"]["reconciliation_status"] = "FAIL"

        decision = safe_judge_decide(payload, rules=self.rules)
        self.assertEqual(decision.action, "PAUSE")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_RECON_FAIL.value])

    def test_rate_limit_pauses(self) -> None:
        payload = base_payload()
        payload["ops"]["rate_limit_alert"] = True

        decision = safe_judge_decide(payload, rules=self.rules)
        self.assertEqual(decision.action, "PAUSE")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_RATE_LIMIT_STORM.value])

    def test_daily_loss_limit_pauses(self) -> None:
        payload = base_payload()
        payload["context"]["account"]["daily_loss_pct"] = self.rules.risk.max_daily_loss_pct

        decision = safe_judge_decide(payload, rules=self.rules)
        self.assertEqual(decision.action, "PAUSE")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_DAILY_LOSS_LIMIT_HIT.value])

    def test_spread_gate_holds(self) -> None:
        payload = base_payload()
        payload["snapshot"]["spread_bps"] = self.rules.cost_guard.max_spread_bps_entry + 0.01

        decision = safe_judge_decide(payload, rules=self.rules)
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_SPREAD_TOO_WIDE.value])

    def test_regime_blocked_holds(self) -> None:
        payload = base_payload()

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            regime={"trade_allowed": False},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_REGIME_BLOCKED.value])

    def test_risk_veto_holds(self) -> None:
        payload = base_payload()

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            risk={"veto": True},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_RISK_VETO.value])

    def test_market_buy_when_all_gates_pass(self) -> None:
        payload = base_payload()

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "BUY", "confidence": 0.7},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_PASS.value])
        self.assertIsNotNone(decision.expected_cost_bps)
        self.assertAlmostEqual(
            decision.expected_cost_bps,
            payload["snapshot"]["spread_bps"] + self.rules.cost_guard.entry_cost_buffer_bps,
        )

    def test_market_long_maps_to_buy(self) -> None:
        payload = base_payload()

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "LONG", "confidence": 0.6},
        )
        self.assertEqual(decision.action, "BUY")

    def test_trade_plan_disallow_buy_blocks_buy(self) -> None:
        payload = base_payload()
        payload["context"]["trade_plan"] = {"allowed_actions": {"buy": False, "sell": True}, "target_position_pct": 10.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "BUY", "confidence": 0.7},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_TRADE_PLAN_FLAT.value])

    def test_trade_plan_disallow_sell_blocks_sell(self) -> None:
        payload = base_payload()
        payload["context"]["trade_plan"] = {"allowed_actions": {"buy": True, "sell": False}, "target_position_pct": 10.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "SELL", "confidence": 0.7},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_SIGNAL_CONFLICT.value])

    def test_effective_target_uses_min_of_plan_and_signal(self) -> None:
        payload = base_payload()
        payload["context"]["trade_plan"] = {"allowed_actions": {"buy": True, "sell": True}, "target_position_pct": 5.0}
        payload["context"]["position"] = {"current_position_pct": 1.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "BUY", "confidence": 0.72, "signal_target_pct": 3.0},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "BUY")
        self.assertAlmostEqual(float(decision.effective_target_pct or 0.0), 3.0, places=6)
        self.assertAlmostEqual(float(decision.gates.get("effective_target_pct") or 0.0), 3.0, places=6)

    def test_signal_target_zero_blocks_buy_even_if_plan_positive(self) -> None:
        payload = base_payload()
        payload["context"]["trade_plan"] = {"allowed_actions": {"buy": True, "sell": True}, "target_position_pct": 5.0}
        payload["context"]["position"] = {"current_position_pct": 0.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "BUY", "confidence": 0.72, "signal_target_pct": 0.0},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_TRADE_PLAN_FLAT.value])

    def test_execution_plan_target_takes_precedence_over_legacy_target(self) -> None:
        payload = base_payload()
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": True, "sell": True},
            "target_position_pct": 5.0,
            "execution_plan": {"final_numbers": {"target_position_pct": 2.5}},
        }
        payload["context"]["position"] = {"current_position_pct": 1.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "BUY", "confidence": 0.72, "signal_target_pct": 3.0},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "BUY")
        self.assertAlmostEqual(float(decision.effective_target_pct or 0.0), 2.5, places=6)

    def test_activation_gate_hold_blocks_sell_and_buy(self) -> None:
        payload = base_payload()
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": True, "sell": True},
            "target_position_pct": 4.0,
            "activation_gate": {"decision": "HOLD", "decision_effective": "HOLD"},
        }

        decision_sell = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "SELL", "confidence": 0.66},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision_sell.action, "HOLD")
        self.assertEqual(decision_sell.selected_reasons, [ReasonCode.RG_TRADE_PLAN_FLAT.value])

        decision_buy = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "BUY", "confidence": 0.66, "signal_target_pct": 2.0},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision_buy.action, "HOLD")
        self.assertEqual(decision_buy.selected_reasons, [ReasonCode.RG_TRADE_PLAN_FLAT.value])

    def test_target_reached_uses_effective_target(self) -> None:
        payload = base_payload()
        payload["context"]["trade_plan"] = {"allowed_actions": {"buy": True, "sell": True}, "target_position_pct": 5.0}
        payload["context"]["position"] = {"current_position_pct": 3.1}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "BUY", "confidence": 0.72, "signal_target_pct": 3.0},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_TRADE_PLAN_TARGET_REACHED.value])

    def test_multiple_reasons_are_capped_to_three(self) -> None:
        # Construct a scenario where HOLD reasons would pile up if not capped.
        payload = base_payload()
        payload["snapshot"]["spread_bps"] = self.rules.cost_guard.max_spread_bps_entry + 1.0
        payload["ops"]["reconciliation_status"] = "OK"
        payload["ops"]["rate_limit_alert"] = False
        payload["context"]["account"]["daily_loss_pct"] = 0.0

        decision = safe_judge_decide(
            copy.deepcopy(payload),
            rules=self.rules,
            regime={"trade_allowed": False},
            risk={"veto": True},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertLessEqual(len(decision.selected_reasons), 3)


if __name__ == "__main__":
    unittest.main()
