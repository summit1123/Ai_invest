from __future__ import annotations

import unittest

from ai_invest.config.capital_policy import resolve_capital_policy


class CapitalPolicyTests(unittest.TestCase):
    def test_disabled_policy_returns_base_limits(self) -> None:
        out = resolve_capital_policy(
            rules_raw={"governance": {"capital_policy": {"enabled": False}}},
            equity_krw=1_000_000,
            default_target_position_pct=10.0,
            max_position_pct_per_symbol=20.0,
            cooldown_minutes_after_trigger=180,
        )
        self.assertFalse(out.enabled)
        self.assertEqual(out.tier_name, "default")
        self.assertEqual(out.max_target_position_pct, 10.0)
        self.assertEqual(out.max_position_pct_per_symbol, 20.0)

    def test_enabled_policy_selects_tier_and_clamps(self) -> None:
        rules_raw = {
            "governance": {
                "capital_policy": {
                    "enabled": True,
                    "tiers": [
                        {
                            "name": "small",
                            "min_equity_krw": 0,
                            "max_target_position_pct": 5.0,
                            "max_position_pct_per_symbol": 10.0,
                            "cooldown_minutes_after_trigger": 240,
                        },
                        {
                            "name": "mid",
                            "min_equity_krw": 2_000_000,
                            "max_target_position_pct": 9.0,
                            "max_position_pct_per_symbol": 16.0,
                            "cooldown_minutes_after_trigger": 180,
                        },
                    ],
                }
            }
        }
        out = resolve_capital_policy(
            rules_raw=rules_raw,
            equity_krw=2_500_000,
            default_target_position_pct=12.0,
            max_position_pct_per_symbol=20.0,
            cooldown_minutes_after_trigger=120,
        )
        self.assertTrue(out.enabled)
        self.assertEqual(out.tier_name, "mid")
        self.assertEqual(out.max_target_position_pct, 9.0)
        self.assertEqual(out.max_position_pct_per_symbol, 16.0)
        self.assertEqual(out.cooldown_minutes_after_trigger, 180)


if __name__ == "__main__":
    unittest.main()
