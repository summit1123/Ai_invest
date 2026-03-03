from __future__ import annotations

import copy
import unittest
from pathlib import Path

import yaml

from ai_invest.config.rules_loader import RulesValidationError, load_rules, validate_rules


class RulesLoaderTests(unittest.TestCase):
    def test_load_rules_yaml_success(self) -> None:
        cfg = load_rules(Path("rules.yaml"))
        self.assertEqual(cfg.version, "v1.0")
        self.assertGreaterEqual(len(cfg.universe.symbols), 1)
        self.assertEqual(cfg.universe.symbols[0], "KRW-BTC")
        self.assertIn("KRW-BTC", cfg.universe.symbols)
        self.assertGreater(cfg.cost_guard.min_expected_edge_bps, cfg.cost_guard.max_total_cost_bps)
        self.assertFalse(cfg.stop_policy.include_tax_in_realtime_stop)

    def test_validate_rejects_invalid_risk_hierarchy(self) -> None:
        raw = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))
        broken = copy.deepcopy(raw)
        broken["risk"]["max_risk_per_trade_pct"] = 2.0
        broken["risk"]["max_daily_loss_pct"] = 1.0

        with self.assertRaises(RulesValidationError):
            validate_rules(broken)

    def test_validate_rejects_edge_below_total_cost(self) -> None:
        raw = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))
        broken = copy.deepcopy(raw)
        broken["cost_guard"]["min_expected_edge_bps"] = 10.0
        broken["cost_guard"]["max_total_cost_bps"] = 12.0

        with self.assertRaises(RulesValidationError):
            validate_rules(broken)


if __name__ == "__main__":
    unittest.main()
