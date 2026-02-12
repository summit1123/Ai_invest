from __future__ import annotations

import unittest

from ai_invest.strategy.alpha_score import compute_alpha_score, load_alpha_score_config


class AlphaScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_alpha_score_config(rules_raw={})

    def test_entry_boundary_alpha_near_threshold(self) -> None:
        features = {
            "rsi_14": 60.0,  # s_rsi=1
            "rsi_14_prev": 58.0,
            "vol_zscore": 1.457142857,  # s_vol~=0.5714 -> mom ~= 0.65
            "ret_15m": 0.0,
            "ret_60m": 0.0,
            "ema20": 101.0,
            "ema60": 100.0,
            "atr_pct": 1.3,
        }
        out = compute_alpha_score(features=features, cfg=self.cfg)
        self.assertAlmostEqual(out.alpha, 0.65, places=2)
        self.assertGreaterEqual(out.signal_target_pct, self.cfg.base_target_pct * 0.95)

    def test_strong_alpha_increases_target(self) -> None:
        features = {
            "rsi_14": 62.0,
            "rsi_14_prev": 57.0,
            "vol_zscore": 2.2,
            "ret_15m": 0.002,
            "ret_60m": 0.02,
            "ema20": 102.0,
            "ema60": 100.0,
            "atr_pct": 1.1,
        }
        out = compute_alpha_score(features=features, cfg=self.cfg)
        self.assertGreaterEqual(out.alpha, self.cfg.strong_alpha)
        self.assertGreaterEqual(out.signal_target_pct, self.cfg.base_target_pct)
        self.assertLessEqual(out.signal_target_pct, self.cfg.max_target_pct)

    def test_rev_trigger_sets_rev_strategy(self) -> None:
        features = {
            "rsi_14_prev": 30.0,
            "rsi_14": 51.0,
            "vol_zscore": 1.5,
            "ret_15m": -0.01,
            "ret_60m": -0.005,
            "ema20": 99.0,
            "ema60": 100.0,
            "atr_pct": 1.3,
        }
        out = compute_alpha_score(features=features, cfg=self.cfg)
        self.assertEqual(out.rev_s, 1.0)
        self.assertEqual(out.strategy_tag_candidate, "REV")

    def test_vol_scale_clamps_on_extreme_atr(self) -> None:
        low_atr_features = {
            "rsi_14": 55.0,
            "rsi_14_prev": 50.0,
            "vol_zscore": 1.5,
            "ret_15m": 0.0,
            "ret_60m": 0.01,
            "ema20": 101.0,
            "ema60": 99.0,
            "atr_pct": 0.1,
        }
        high_atr_features = {**low_atr_features, "atr_pct": 9.9}
        low = compute_alpha_score(features=low_atr_features, cfg=self.cfg)
        high = compute_alpha_score(features=high_atr_features, cfg=self.cfg)
        self.assertAlmostEqual(low.vol_scale, self.cfg.vol_scale_max, places=6)
        self.assertAlmostEqual(high.vol_scale, self.cfg.vol_scale_min, places=6)


if __name__ == "__main__":
    unittest.main()

