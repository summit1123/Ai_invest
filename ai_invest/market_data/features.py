from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class FeatureSnapshot:
    atr_pct: float
    rsi_14: float
    vol_zscore: float
    missing_rate_1m: float


def _mean(values: Sequence[float]) -> float:
    return sum(values) / float(len(values)) if values else 0.0


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    var = sum((x - m) ** 2 for x in values) / float(len(values) - 1)
    return math.sqrt(var)


def compute_rsi(closes: Sequence[float], *, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0

    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, period + 1):
        delta = closes[-i] - closes[-i - 1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-delta)

    avg_gain = _mean(gains)
    avg_loss = _mean(losses)
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(max(0.0, min(100.0, rsi)))


def compute_ema(values: Sequence[float], *, period: int) -> float:
    if period <= 0 or not values:
        return 0.0
    arr = [float(x) for x in values if isinstance(x, (int, float))]
    if not arr:
        return 0.0
    alpha = 2.0 / (float(period) + 1.0)
    ema = arr[0]
    for v in arr[1:]:
        ema = (alpha * v) + ((1.0 - alpha) * ema)
    return float(ema)


def compute_return(values: Sequence[float], *, bars: int) -> float:
    if bars <= 0:
        return 0.0
    if len(values) <= bars:
        return 0.0
    now = float(values[-1])
    prev = float(values[-(bars + 1)])
    if prev <= 0:
        return 0.0
    return float((now / prev) - 1.0)


def compute_atr_pct(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    period: int = 14,
) -> float:
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return 0.0

    trs: list[float] = []
    for i in range(n - period, n):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    atr = _mean(trs)
    last_close = closes[-1]
    if last_close <= 0:
        return 0.0
    return float(atr / last_close * 100.0)


def compute_volume_zscore(volumes: Sequence[float], *, window: int = 20) -> float:
    if len(volumes) < window:
        return 0.0
    sample = list(volumes[-window:])
    m = _mean(sample)
    s = _std(sample)
    if s <= 0:
        return 0.0
    return float((sample[-1] - m) / s)


def build_feature_snapshot_from_candles(
    *,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    missing_rate_1m: float = 0.0,
) -> FeatureSnapshot:
    return FeatureSnapshot(
        atr_pct=compute_atr_pct(highs, lows, closes, period=14),
        rsi_14=compute_rsi(closes, period=14),
        vol_zscore=compute_volume_zscore(volumes, window=20),
        missing_rate_1m=float(missing_rate_1m),
    )


def build_alpha_features_from_1m_candles(
    *,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    ema_fast: int = 20,
    ema_slow: int = 60,
    ret_short_bars: int = 15,
    ret_long_bars: int = 60,
) -> dict[str, float]:
    rsi_14 = compute_rsi(closes, period=14)
    rsi_prev = compute_rsi(closes[:-1], period=14) if len(closes) >= 16 else 50.0
    atr_pct = compute_atr_pct(highs, lows, closes, period=14)
    vol_z = compute_volume_zscore(volumes, window=20)
    ema20 = compute_ema(closes, period=max(1, int(ema_fast)))
    ema60 = compute_ema(closes, period=max(1, int(ema_slow)))
    ret_15m = compute_return(closes, bars=max(1, int(ret_short_bars)))
    ret_60m = compute_return(closes, bars=max(1, int(ret_long_bars)))
    return {
        "rsi_14": float(rsi_14),
        "rsi_14_prev": float(rsi_prev),
        "vol_zscore": float(vol_z),
        "atr_pct": float(atr_pct),
        "ret_15m": float(ret_15m),
        "ret_60m": float(ret_60m),
        "ema20": float(ema20),
        "ema60": float(ema60),
    }
