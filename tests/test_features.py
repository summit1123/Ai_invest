from __future__ import annotations

import unittest

from ai_invest.market_data.features import compute_atr_pct, compute_rsi, compute_volume_zscore


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


if __name__ == "__main__":
    unittest.main()

