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
        "snapshot": {"spread_bps": 2.0},
        "features": {"atr_pct": 1.0, "rsi_14": 55.0, "vol_zscore": 2.0, "missing_rate_1m": 0.0},
        "ops": {"rate_limit_alert": False, "reconciliation_status": "OK", "pause_state": False},
        "context": {"account": {"daily_loss_pct": 0.0}},
    }


class AgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = load_rules("rules.yaml")

    def test_market_agent_can_long(self) -> None:
        op = market_agent_opine(base_payload(), rules=self.rules)
        self.assertIn(op.signal, {"LONG", "HOLD", "SELL"})

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
