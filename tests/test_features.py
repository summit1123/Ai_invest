from __future__ import annotations

import unittest

from ai_invest.market_data.features import (
    build_alpha_features_from_1m_candles,
    compute_atr_pct,
    compute_ema,
    compute_return,
    compute_rsi,
    compute_volume_zscore,
)


class FeatureTests(unittest.TestCase):
    def test_rsi_bounds(self) -> None:
        closes = list(range(1, 40))
        rsi = compute_rsi(closes, period=14)
        self.assertGreaterEqual(rsi, 0.0)
        self.assertLessEqual(rsi, 100.0)

    def test_atr_pct_non_negative(self) -> None:
        highs = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25]
        lows = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]
        closes = [9.5] * len(highs)
        atr_pct = compute_atr_pct(highs, lows, closes, period=14)
        self.assertGreaterEqual(atr_pct, 0.0)

    def test_volume_zscore_window_guard(self) -> None:
        self.assertEqual(compute_volume_zscore([1, 2, 3], window=20), 0.0)

    def test_compute_ema_and_return(self) -> None:
        closes = [100.0 + i for i in range(80)]
        ema = compute_ema(closes, period=20)
        ret_15 = compute_return(closes, bars=15)
        ret_60 = compute_return(closes, bars=60)
        self.assertGreater(ema, 0.0)
        self.assertGreater(ret_15, 0.0)
        self.assertGreater(ret_60, 0.0)

    def test_build_alpha_features_contains_expected_keys(self) -> None:
        highs = [101.0 + (i * 0.1) for i in range(120)]
        lows = [99.0 + (i * 0.1) for i in range(120)]
        closes = [100.0 + (i * 0.1) for i in range(120)]
        volumes = [1000.0 + (i * 2.0) for i in range(120)]
        out = build_alpha_features_from_1m_candles(
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            ema_fast=20,
            ema_slow=60,
            ret_short_bars=15,
            ret_long_bars=60,
        )
        self.assertIn("rsi_14", out)
        self.assertIn("rsi_14_prev", out)
        self.assertIn("vol_zscore", out)
        self.assertIn("ret_15m", out)
        self.assertIn("ret_60m", out)
        self.assertIn("ema20", out)
        self.assertIn("ema60", out)


if __name__ == "__main__":
    unittest.main()
