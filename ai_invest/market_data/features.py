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


def _derive_opens_from_closes(closes: Sequence[float]) -> list[float]:
    arr = [float(x) for x in closes]
    if not arr:
        return []
    out: list[float] = [float(arr[0])]
    for i in range(1, len(arr)):
        out.append(float(arr[i - 1]))
    return out


def compute_body_pct(*, open_price: float, close_price: float) -> float:
    den = max(abs(float(close_price)), 1e-12)
    return float(abs(float(close_price) - float(open_price)) / den)


def compute_wick_pct(*, open_price: float, high_price: float, low_price: float, close_price: float) -> float:
    upper_wick = max(0.0, float(high_price) - max(float(open_price), float(close_price)))
    lower_wick = max(0.0, min(float(open_price), float(close_price)) - float(low_price))
    den = max(abs(float(close_price)), 1e-12)
    return float((upper_wick + lower_wick) / den)


def compute_clv(*, high_price: float, low_price: float, close_price: float) -> float:
    den = max(float(high_price) - float(low_price), 1e-12)
    value = (2.0 * float(close_price) - float(high_price) - float(low_price)) / den
    return float(max(-1.0, min(1.0, value)))


def compute_dollar_volume_zscore(
    *,
    closes: Sequence[float],
    volumes: Sequence[float],
    turnovers: Sequence[float] | None = None,
    window: int = 20,
) -> float:
    n = min(len(closes), len(volumes))
    if n <= 0:
        return 0.0
    if turnovers is not None:
        t = [max(0.0, float(x)) for x in list(turnovers)]
        m = min(n, len(t))
        if m > 0:
            t = t[-m:]
            if any(x > 0.0 for x in t):
                return compute_volume_zscore(t, window=window)
    dv = [float(closes[i]) * float(volumes[i]) for i in range(n)]
    return compute_volume_zscore(dv, window=window)


def compute_orderflow_pressure(*, opens: Sequence[float], closes: Sequence[float], volumes: Sequence[float], period: int = 5) -> float:
    n = min(len(opens), len(closes), len(volumes))
    if n <= 0:
        return 0.0
    p = max(1, int(period))
    o = [float(x) for x in opens[-p:]]
    c = [float(x) for x in closes[-p:]]
    v = [float(x) for x in volumes[-p:]]
    signed = []
    for i in range(len(v)):
        direction = 1.0 if c[i] > o[i] else (-1.0 if c[i] < o[i] else 0.0)
        signed.append(float(v[i]) * direction)
    den = max(sum(v), 1e-12)
    out = sum(signed) / den
    return float(max(-1.0, min(1.0, out)))


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
    opens: Sequence[float] | None = None,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    turnover_values: Sequence[float] | None = None,
    ema_fast: int = 20,
    ema_slow: int = 60,
    ret_short_bars: int = 15,
    ret_long_bars: int = 60,
) -> dict[str, float]:
    opens_arr = [float(x) for x in (opens if opens is not None else _derive_opens_from_closes(closes))]
    turnovers_arr = [float(x) for x in turnover_values] if turnover_values is not None else None
    n = min(len(opens_arr), len(highs), len(lows), len(closes), len(volumes))
    if turnovers_arr is not None:
        n = min(n, len(turnovers_arr))
    if n <= 0:
        return {
            "rsi_14": 50.0,
            "rsi_14_prev": 50.0,
            "vol_zscore": 0.0,
            "atr_pct": 0.0,
            "ret_15m": 0.0,
            "ret_60m": 0.0,
            "ema20": 0.0,
            "ema60": 0.0,
            "body_pct": 0.0,
            "wick_pct": 0.0,
            "clv": 0.0,
            "dv_zscore": 0.0,
            "oflow": 0.0,
            "range_pct": 0.0,
        }
    opens_arr = opens_arr[-n:]
    highs_arr = [float(x) for x in highs[-n:]]
    lows_arr = [float(x) for x in lows[-n:]]
    closes_arr = [float(x) for x in closes[-n:]]
    volumes_arr = [float(x) for x in volumes[-n:]]
    turnovers_tail = [float(x) for x in turnovers_arr[-n:]] if turnovers_arr is not None else None

    rsi_14 = compute_rsi(closes_arr, period=14)
    rsi_prev = compute_rsi(closes_arr[:-1], period=14) if len(closes_arr) >= 16 else 50.0
    atr_pct = compute_atr_pct(highs_arr, lows_arr, closes_arr, period=14)
    vol_z = compute_volume_zscore(volumes_arr, window=20)
    ema20 = compute_ema(closes_arr, period=max(1, int(ema_fast)))
    ema60 = compute_ema(closes_arr, period=max(1, int(ema_slow)))
    ret_15m = compute_return(closes_arr, bars=max(1, int(ret_short_bars)))
    ret_60m = compute_return(closes_arr, bars=max(1, int(ret_long_bars)))

    last_open = float(opens_arr[-1])
    last_high = float(highs_arr[-1])
    last_low = float(lows_arr[-1])
    last_close = float(closes_arr[-1])
    body_pct = compute_body_pct(open_price=last_open, close_price=last_close)
    wick_pct = compute_wick_pct(
        open_price=last_open,
        high_price=last_high,
        low_price=last_low,
        close_price=last_close,
    )
    clv = compute_clv(high_price=last_high, low_price=last_low, close_price=last_close)
    dv_z = compute_dollar_volume_zscore(
        closes=closes_arr,
        volumes=volumes_arr,
        turnovers=turnovers_tail,
        window=20,
    )
    oflow = compute_orderflow_pressure(opens=opens_arr, closes=closes_arr, volumes=volumes_arr, period=5)
    range_pct = float(((last_high - last_low) / max(abs(last_close), 1e-12)) * 100.0)

    return {
        "rsi_14": float(rsi_14),
        "rsi_14_prev": float(rsi_prev),
        "vol_zscore": float(vol_z),
        "atr_pct": float(atr_pct),
        "ret_15m": float(ret_15m),
        "ret_60m": float(ret_60m),
        "ema20": float(ema20),
        "ema60": float(ema60),
        "body_pct": float(body_pct),
        "wick_pct": float(wick_pct),
        "clv": float(clv),
        "dv_zscore": float(dv_z),
        "oflow": float(oflow),
        "range_pct": float(range_pct),
    }
