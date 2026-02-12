from __future__ import annotations

import unittest

from ai_invest.agents.market_agent import market_agent_opine
from ai_invest.agents.ops_agent import ops_agent_opine
from ai_invest.agents.regime_agent import regime_agent_opine
from ai_invest.agents.risk_agent import risk_agent_opine
from ai_invest.config.rules_loader import load_rules


def base_payload() -> dict:
    return {
        "symbol": "KRW-BTC",
        "timestamp_utc": "2026-02-12T00:00:00+00:00",
        "snapshot": {"spread_bps": 2.0, "last_price": 100.0, "mid_price": 100.0},
        "features": {
            "atr_pct": 1.0,
            "rsi_14": 55.0,
            "rsi_14_prev": 53.0,
            "vol_zscore": 2.0,
            "ret_15m": -0.001,
            "ret_60m": 0.01,
            "ema20": 101.0,
            "ema60": 99.0,
            "missing_rate_1m": 0.0,
        },
        "ops": {"rate_limit_alert": False, "reconciliation_status": "OK", "pause_state": False},
        "context": {
            "account": {"daily_loss_pct": 0.0},
            "position": {"current_qty": 0.0},
            "position_state": {},
        },
    }


class AgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = load_rules("rules.yaml")

    def test_market_agent_can_long(self) -> None:
        op = market_agent_opine(base_payload(), rules=self.rules)
        self.assertIn(op.signal, {"LONG", "HOLD", "SELL"})
        self.assertIsInstance(op.alpha, float)
        self.assertIsInstance(op.signal_target_pct, float)
        self.assertIsInstance(op.entry_allowed, bool)

    def test_market_agent_stop_exit_on_in_position_loss(self) -> None:
        payload = base_payload()
        payload["context"]["position"]["current_qty"] = 0.01
        payload["context"]["position_state"] = {
            "entry_price": 100.0,
            "entry_ts": "2026-02-11T22:00:00+00:00",
            "hwm_price": 105.0,
            "strategy_tag": "MOM",
        }
        payload["snapshot"]["last_price"] = 96.0
        payload["snapshot"]["mid_price"] = 96.0
        op = market_agent_opine(payload, rules=self.rules)
        self.assertEqual(op.signal, "SELL")
        self.assertIn(op.exit_reason, {"STOP", "TRAIL", "MOMENTUM_BREAK", "TIMESTOP"})

    def test_market_agent_cooldown_blocks_new_entry(self) -> None:
        payload = base_payload()
        payload["context"]["position"]["current_qty"] = 0.0
        payload["context"]["position_state"] = {"cooldown_until": "2099-01-01T00:00:00+00:00"}
        op = market_agent_opine(payload, rules=self.rules)
        self.assertEqual(op.signal, "HOLD")
        self.assertFalse(op.entry_allowed)

    def test_regime_agent_blocks_on_high_atr(self) -> None:
        payload = base_payload()
        payload["features"]["atr_pct"] = 999.0
        op = regime_agent_opine(payload, rules=self.rules)
        self.assertFalse(op.trade_allowed)

    def test_risk_agent_veto_on_daily_loss(self) -> None:
        payload = base_payload()
        payload["context"]["account"]["daily_loss_pct"] = self.rules.risk.max_daily_loss_pct
        op = risk_agent_opine(payload, rules=self.rules)
        self.assertTrue(op.veto)

    def test_ops_agent_veto_on_recon_fail(self) -> None:
        payload = base_payload()
        payload["ops"]["reconciliation_status"] = "FAIL"
        op = ops_agent_opine(payload)
        self.assertTrue(op.veto)


if __name__ == "__main__":
    unittest.main()
