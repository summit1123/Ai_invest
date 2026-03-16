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
        self.rules.raw.setdefault("universe", {})["mode"] = "paper"

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
        payload["snapshot"]["spread_bps"] = 50.0

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

    def test_trade_plan_disallow_sell_still_allows_exit(self) -> None:
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
        self.assertEqual(decision.action, "SELL")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_PASS.value])

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

    def test_activation_gate_hold_allows_sell_but_blocks_buy(self) -> None:
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
        self.assertEqual(decision_sell.action, "SELL")
        self.assertEqual(decision_sell.selected_reasons, [ReasonCode.RG_PASS.value])

        decision_buy = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "BUY", "confidence": 0.66, "signal_target_pct": 2.0},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision_buy.action, "HOLD")
        self.assertEqual(decision_buy.selected_reasons, [ReasonCode.RG_MICRO_BLOCKED_POLICY.value])

    def test_regime_blocked_still_allows_sell_exit(self) -> None:
        payload = base_payload()

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "SELL", "confidence": 0.66},
            regime={"trade_allowed": False},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "SELL")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_PASS.value])

    def test_risk_veto_still_allows_sell_exit(self) -> None:
        payload = base_payload()

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "SELL", "confidence": 0.66},
            regime={"trade_allowed": True},
            risk={"veto": True},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "SELL")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_PASS.value])

    def test_spread_gate_still_allows_sell_exit(self) -> None:
        payload = base_payload()
        payload["snapshot"]["spread_bps"] = 50.0

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "SELL", "confidence": 0.66},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "SELL")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_PASS.value])

    def test_hold_uses_market_reason_codes(self) -> None:
        payload = base_payload()
        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "HOLD", "reason_codes": [ReasonCode.RG_EDGE_TOO_LOW.value]},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_EDGE_TOO_LOW.value])
        self.assertIn(ReasonCode.RG_EDGE_TOO_LOW.value, list(decision.gates.get("market_reason_codes") or []))

    def test_micro_plan_led_promotes_buy_in_hold_mode(self) -> None:
        payload = base_payload()
        self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})["max_spread_bps"] = 3.0
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": True, "sell": True},
            "target_position_pct": 2.5,
            "activation_gate": {
                "decision": "HOLD",
                "decision_effective": "HOLD",
                "hard_plan_block": False,
                "soft_plan_block": False,
                "plan_execution_blocked": False,
            },
        }
        payload["context"]["position"] = {"current_position_pct": 0.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={
                "signal": "HOLD",
                "alpha": 0.90,
                "signal_target_pct": 0.0,
                "reason_codes": [ReasonCode.RG_PASS.value],
            },
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_CAP_PROMOTED.value])
        micro_max = float(
            (((self.rules.raw.get("governance") or {}).get("micro_mode") or {}).get("max_position_pct") or 2.0)
        )
        self.assertAlmostEqual(float(decision.effective_target_pct or 0.0), min(2.5, micro_max), places=6)
        self.assertEqual(str(decision.gates.get("micro_mode_entry_path")), "plan-led")

    def test_micro_blocked_by_market_cooldown_reason(self) -> None:
        payload = base_payload()
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": True, "sell": True},
            "target_position_pct": 2.0,
            "activation_gate": {
                "decision": "HOLD",
                "decision_effective": "HOLD",
                "hard_plan_block": False,
                "soft_plan_block": False,
                "plan_execution_blocked": False,
            },
        }
        # Guard semantics test: explicitly disable plan-led cooldown ignore.
        self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})[
            "ignore_market_cooldown_in_plan_led"
        ] = False

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={
                "signal": "HOLD",
                "alpha": 0.95,
                "signal_target_pct": 0.0,
                "reason_codes": [ReasonCode.RG_COOLDOWN_ACTIVE.value],
            },
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_MICRO_BLOCKED_COOLDOWN.value])

    def test_micro_plan_led_can_ignore_cooldown_when_enabled(self) -> None:
        payload = base_payload()
        self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})["max_spread_bps"] = 3.0
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": True, "sell": True},
            "target_position_pct": 2.0,
            "activation_gate": {
                "decision": "HOLD",
                "decision_effective": "HOLD",
                "hard_plan_block": False,
                "soft_plan_block": False,
                "plan_execution_blocked": False,
            },
        }
        self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})[
            "ignore_market_cooldown_in_plan_led"
        ] = True

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={
                "signal": "HOLD",
                "alpha": 0.95,
                "signal_target_pct": 0.0,
                "reason_codes": [ReasonCode.RG_COOLDOWN_ACTIVE.value],
            },
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_CAP_PROMOTED.value])

    def test_micro_inter_slot_realtime_relaxes_alpha_threshold(self) -> None:
        payload = base_payload()
        self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})["max_spread_bps"] = 3.0
        self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})["min_alpha"] = 0.85
        self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})["realtime_min_alpha_delta"] = -0.08
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": True, "sell": True},
            "target_position_pct": 2.0,
            "activation_gate": {
                "decision": "HOLD",
                "decision_effective": "HOLD",
                "hard_plan_block": False,
                "soft_plan_block": False,
                "plan_execution_blocked": False,
                "inter_slot_realtime_mode": True,
            },
        }

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={
                "signal": "HOLD",
                "alpha": 0.79,
                "signal_target_pct": 0.0,
                "reason_codes": [ReasonCode.RG_PASS.value],
            },
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_CAP_PROMOTED.value])
        self.assertLess(float(decision.gates.get("micro_mode_dynamic_min_alpha") or 0.0), 0.85)

    def test_micro_without_inter_slot_keeps_base_alpha_threshold(self) -> None:
        payload = base_payload()
        self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})["max_spread_bps"] = 3.0
        self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})["min_alpha"] = 0.85
        self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})["realtime_min_alpha_delta"] = -0.08
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": True, "sell": True},
            "target_position_pct": 2.0,
            "activation_gate": {
                "decision": "HOLD",
                "decision_effective": "HOLD",
                "hard_plan_block": False,
                "soft_plan_block": False,
                "plan_execution_blocked": False,
                "inter_slot_realtime_mode": False,
            },
        }

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={
                "signal": "HOLD",
                "alpha": 0.79,
                "signal_target_pct": 0.0,
                "reason_codes": [ReasonCode.RG_PASS.value],
            },
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_MICRO_BLOCKED_POLICY.value])

    def test_live_micro_runtime_hold_entry_can_buy_with_calibrated_after_cost(self) -> None:
        payload = base_payload()
        self.rules.raw.setdefault("universe", {})["mode"] = "live"
        micro_cfg = self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})
        micro_cfg["enabled_live"] = True
        micro_cfg["allow_runtime_hold_entry"] = True
        micro_cfg["require_calibration_live"] = True
        micro_cfg["require_market_long"] = False
        micro_cfg["max_spread_bps"] = 3.0
        micro_cfg["max_position_pct"] = 12.0
        micro_cfg["live_min_predicted_after_cost_bps"] = 0.0
        payload["context"]["runtime_controls"] = {
            "buy_enabled": True,
            "max_position_pct": 20.0,
            "actionable_target_floor_pct": 10.2,
        }
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": False, "sell": True},
            "target_position_pct": 12.0,
            "activation_gate": {
                "decision": "HOLD",
                "decision_effective": "HOLD",
                "hold_mode": "HOLD_CONDITIONAL",
                "hard_plan_block": False,
                "soft_plan_block": False,
                "plan_execution_blocked": False,
                "inter_slot_realtime_mode": True,
                "final_plan_no_trade_declared": False,
            },
        }
        payload["context"]["position"] = {"current_position_pct": 0.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={
                "signal": "HOLD",
                "alpha": 0.91,
                "signal_target_pct": 0.0,
                "expected_net_edge_bps": -0.5,
                "min_edge_required_bps": 4.0,
                "reason_codes": [ReasonCode.RG_EDGE_TOO_LOW.value],
                "reason": {
                    "edge_calibration": {
                        "predicted_after_cost_bps": 0.35,
                        "required_after_cost_bps": 4.0,
                        "uncertainty_bps": 2.5,
                    }
                },
            },
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_CAP_PROMOTED.value])
        self.assertEqual(bool(decision.gates.get("micro_mode_runtime_hold_entry_allowed")), True)
        self.assertEqual(bool(decision.gates.get("micro_mode_live_exploration_edge_ok")), True)
        self.assertGreaterEqual(float(decision.effective_target_pct or 0.0), 10.2)

    def test_live_micro_runtime_hold_entry_can_bypass_raw_alpha_when_calibrated_positive(self) -> None:
        payload = base_payload()
        self.rules.raw.setdefault("universe", {})["mode"] = "live"
        micro_cfg = self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})
        micro_cfg["enabled_live"] = True
        micro_cfg["allow_runtime_hold_entry"] = True
        micro_cfg["require_calibration_live"] = True
        micro_cfg["require_market_long"] = False
        micro_cfg["max_spread_bps"] = 3.0
        micro_cfg["max_position_pct"] = 12.0
        micro_cfg["live_min_predicted_after_cost_bps"] = 0.0
        payload["context"]["runtime_controls"] = {
            "buy_enabled": True,
            "max_position_pct": 20.0,
            "actionable_target_floor_pct": 10.2,
        }
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": False, "sell": True},
            "target_position_pct": 12.0,
            "activation_gate": {
                "decision": "HOLD",
                "decision_effective": "HOLD",
                "hold_mode": "HOLD_CONDITIONAL",
                "hard_plan_block": False,
                "soft_plan_block": False,
                "plan_execution_blocked": False,
                "inter_slot_realtime_mode": True,
                "final_plan_no_trade_declared": False,
            },
        }
        payload["context"]["position"] = {"current_position_pct": 0.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={
                "signal": "HOLD",
                "alpha": 0.55,
                "signal_target_pct": 0.0,
                "expected_net_edge_bps": -0.5,
                "min_edge_required_bps": 4.0,
                "reason_codes": [ReasonCode.RG_EDGE_TOO_LOW.value],
                "predicted_after_cost_bps": 0.35,
                "required_after_cost_bps": 4.0,
                "after_cost_uncertainty_bps": 2.5,
            },
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "BUY")
        self.assertTrue(bool(decision.gates.get("micro_mode_alpha_gate_bypassed_by_calibration")))
        self.assertTrue(bool(decision.gates.get("micro_mode_live_exploration_edge_ok")))
        self.assertGreater(float(decision.gates.get("micro_mode_dynamic_min_alpha") or 0.0), 0.55)

    def test_live_micro_runtime_hold_entry_reads_top_level_calibration_fields(self) -> None:
        payload = base_payload()
        self.rules.raw.setdefault("universe", {})["mode"] = "live"
        micro_cfg = self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})
        micro_cfg["enabled_live"] = True
        micro_cfg["allow_runtime_hold_entry"] = True
        micro_cfg["require_calibration_live"] = True
        micro_cfg["require_market_long"] = False
        micro_cfg["max_spread_bps"] = 3.0
        micro_cfg["max_position_pct"] = 12.0
        micro_cfg["live_min_predicted_after_cost_bps"] = 0.0
        payload["context"]["runtime_controls"] = {
            "buy_enabled": True,
            "max_position_pct": 20.0,
            "actionable_target_floor_pct": 10.2,
        }
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": False, "sell": True},
            "target_position_pct": 12.0,
            "activation_gate": {
                "decision": "HOLD",
                "decision_effective": "HOLD",
                "hold_mode": "HOLD_CONDITIONAL",
                "hard_plan_block": False,
                "soft_plan_block": False,
                "plan_execution_blocked": False,
                "inter_slot_realtime_mode": True,
                "final_plan_no_trade_declared": False,
            },
        }
        payload["context"]["position"] = {"current_position_pct": 0.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={
                "signal": "HOLD",
                "alpha": 0.91,
                "signal_target_pct": 0.0,
                "expected_net_edge_bps": -0.5,
                "min_edge_required_bps": 4.0,
                "reason_codes": [ReasonCode.RG_EDGE_TOO_LOW.value],
                "predicted_after_cost_bps": 0.35,
                "required_after_cost_bps": 4.0,
                "after_cost_uncertainty_bps": 2.5,
            },
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "BUY")
        self.assertTrue(bool(decision.gates.get("micro_mode_live_calibration_ready")))
        self.assertAlmostEqual(float(decision.gates.get("market_predicted_after_cost_bps") or 0.0), 0.35, places=6)

    def test_live_micro_runtime_hold_entry_falls_back_to_expected_net_edge_for_calibration(self) -> None:
        payload = base_payload()
        self.rules.raw.setdefault("universe", {})["mode"] = "live"
        micro_cfg = self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})
        micro_cfg["enabled_live"] = True
        micro_cfg["allow_runtime_hold_entry"] = True
        micro_cfg["require_calibration_live"] = True
        micro_cfg["require_market_long"] = False
        micro_cfg["live_min_predicted_after_cost_bps"] = -1.0
        payload["context"]["runtime_controls"] = {
            "buy_enabled": True,
            "max_position_pct": 20.0,
            "actionable_target_floor_pct": 10.2,
        }
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": False, "sell": True},
            "target_position_pct": 12.0,
            "activation_gate": {
                "decision": "HOLD",
                "decision_effective": "HOLD",
                "hold_mode": "HOLD_CONDITIONAL",
                "hard_plan_block": False,
                "soft_plan_block": False,
                "plan_execution_blocked": False,
                "inter_slot_realtime_mode": True,
                "final_plan_no_trade_declared": False,
            },
        }
        payload["context"]["position"] = {"current_position_pct": 0.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={
                "signal": "HOLD",
                "alpha": 0.91,
                "signal_target_pct": 0.0,
                "expected_net_edge_bps": -0.5,
                "min_edge_required_bps": 4.0,
                "reason_codes": [ReasonCode.RG_EDGE_TOO_LOW.value],
            },
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertTrue(bool(decision.gates.get("micro_mode_live_calibration_ready")))
        self.assertAlmostEqual(float(decision.gates.get("market_predicted_after_cost_bps") or 0.0), -0.5, places=6)

    def test_live_micro_runtime_hold_entry_requires_calibration_when_configured(self) -> None:
        payload = base_payload()
        self.rules.raw.setdefault("universe", {})["mode"] = "live"
        micro_cfg = self.rules.raw.setdefault("governance", {}).setdefault("micro_mode", {})
        micro_cfg["enabled_live"] = True
        micro_cfg["allow_runtime_hold_entry"] = True
        micro_cfg["require_calibration_live"] = True
        micro_cfg["require_market_long"] = False
        payload["context"]["runtime_controls"] = {
            "buy_enabled": True,
            "max_position_pct": 20.0,
            "actionable_target_floor_pct": 10.2,
        }
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": False, "sell": True},
            "target_position_pct": 12.0,
            "activation_gate": {
                "decision": "HOLD",
                "decision_effective": "HOLD",
                "hold_mode": "HOLD_CONDITIONAL",
                "hard_plan_block": False,
                "soft_plan_block": False,
                "plan_execution_blocked": False,
                "inter_slot_realtime_mode": True,
                "final_plan_no_trade_declared": False,
            },
        }

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={
                "signal": "HOLD",
                "alpha": 0.91,
                "signal_target_pct": 0.0,
                "expected_net_edge_bps": -0.5,
                "min_edge_required_bps": 4.0,
                "reason_codes": [ReasonCode.RG_EDGE_TOO_LOW.value],
            },
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_MICRO_BLOCKED_POLICY.value])

    def test_cap_promoted_paper_override_allows_buy_with_effective_target(self) -> None:
        payload = base_payload()
        payload["context"]["trade_plan"] = {
            "allowed_actions": {"buy": True, "sell": True},
            "target_position_pct": 0.0,
            "execution_plan": {"final_numbers": {"target_position_pct": 3.0}},
            "activation_gate": {
                "decision": "HOLD",
                "decision_effective": "PAPER",
                "hold_mode": "HOLD_CONDITIONAL",
                "cap_promoted": True,
                "cap_runtime": {"consecutive_passes": 12, "required_passes": 12},
            },
        }
        payload["context"]["position"] = {"current_position_pct": 0.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "BUY", "confidence": 0.68, "signal_target_pct": 2.0},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "BUY")
        self.assertAlmostEqual(float(decision.effective_target_pct or 0.0), 2.0, places=6)
        self.assertEqual(str(decision.gates.get("trade_plan_hold_mode")), "HOLD_CONDITIONAL")
        self.assertEqual(bool(decision.gates.get("trade_plan_cap_promoted")), True)

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

    def test_runtime_controls_can_block_buy(self) -> None:
        payload = base_payload()
        payload["context"]["runtime_controls"] = {
            "buy_enabled": False,
            "reason_codes": [ReasonCode.RG_NEWS_RISK.value],
        }

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "BUY", "confidence": 0.70, "signal_target_pct": 4.0},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "HOLD")
        self.assertEqual(decision.selected_reasons, [ReasonCode.RG_NEWS_RISK.value])

    def test_runtime_controls_cap_effective_target(self) -> None:
        payload = base_payload()
        payload["context"]["trade_plan"] = {"allowed_actions": {"buy": True, "sell": True}, "target_position_pct": 8.0}
        payload["context"]["runtime_controls"] = {"buy_enabled": True, "max_position_pct": 2.0}

        decision = safe_judge_decide(
            payload,
            rules=self.rules,
            market={"signal": "BUY", "confidence": 0.72, "signal_target_pct": 6.0},
            regime={"trade_allowed": True},
            risk={"veto": False},
            ops={"veto": False},
        )
        self.assertEqual(decision.action, "BUY")
        self.assertAlmostEqual(float(decision.effective_target_pct or 0.0), 2.0, places=6)


if __name__ == "__main__":
    unittest.main()
