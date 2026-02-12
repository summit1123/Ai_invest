from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def clamp(value: float, lo: float, hi: float) -> float:
    return float(max(float(lo), min(float(hi), float(value))))


def _as_float(value: Any, *, default: float) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s else float(default)
    except Exception:
        return float(default)


def _as_int(value: Any, *, default: int) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, int):
            return int(value)
        s = str(value).strip()
        return int(float(s)) if s else int(default)
    except Exception:
        return int(default)


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return bool(value)
    s = str(value or "").strip().lower()
    if not s:
        return bool(default)
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


@dataclass(frozen=True)
class AlphaScoreConfig:
    enabled: bool
    entry_alpha: float
    strong_alpha: float
    cooldown_override_alpha: float
    cooldown_override_enabled: bool
    base_target_pct: float
    max_target_pct: float
    atr_ref_pct: float
    vol_scale_min: float
    vol_scale_max: float
    ema_fast: int
    ema_slow: int
    ret_short_mins: int
    ret_long_mins: int
    lookback_minutes: int
    rev_rsi_prev_max: float
    rev_rsi_now_min: float
    rev_ret_15m_max: float
    rev_vol_z_min: float
    exit_rsi: float
    stop_atr_mult: float
    trail_atr_mult: float
    time_stop_mom_minutes: int
    time_stop_rev_minutes: int
    cooldown_minutes: int
    atr_block_pct: float
    daily_loss_entry_block_ratio: float
    # Mom score weights (normalized internally).
    mom_weight_rsi: float
    mom_weight_vol: float
    mom_weight_ret: float
    mom_weight_trend: float
    # Reduce REV dominance when higher-timeframe trend is weak.
    rev_penalty_when_trend_weak: float
    # Dynamic entry alpha adjustment coefficients (cost-aware).
    entry_alpha_spread_k: float
    entry_alpha_fee_k: float


@dataclass(frozen=True)
class AlphaScoreResult:
    mom_s: float
    rev_s: float
    alpha: float
    strength: float
    vol_scale: float
    signal_target_pct: float
    strategy_tag_candidate: str


def load_alpha_score_config(*, rules_raw: Mapping[str, Any]) -> AlphaScoreConfig:
    strategy = (rules_raw.get("strategy") or {}) if isinstance(rules_raw, Mapping) else {}
    cfg = (strategy.get("alpha_score") or {}) if isinstance(strategy, Mapping) else {}
    regime = (rules_raw.get("regime") or {}) if isinstance(rules_raw, Mapping) else {}
    risk = (rules_raw.get("risk") or {}) if isinstance(rules_raw, Mapping) else {}
    return AlphaScoreConfig(
        enabled=_as_bool(cfg.get("enabled"), default=True),
        entry_alpha=_as_float(cfg.get("entry_alpha"), default=0.65),
        strong_alpha=_as_float(cfg.get("strong_alpha"), default=0.80),
        cooldown_override_alpha=_as_float(cfg.get("cooldown_override_alpha"), default=0.90),
        cooldown_override_enabled=_as_bool(cfg.get("cooldown_override_enabled"), default=True),
        base_target_pct=_as_float(cfg.get("base_target_pct"), default=3.0),
        max_target_pct=_as_float(cfg.get("max_target_pct"), default=5.0),
        atr_ref_pct=_as_float(cfg.get("atr_ref_pct"), default=1.3),
        vol_scale_min=_as_float(cfg.get("vol_scale_min"), default=0.5),
        vol_scale_max=_as_float(cfg.get("vol_scale_max"), default=1.2),
        ema_fast=_as_int(cfg.get("ema_fast"), default=20),
        ema_slow=_as_int(cfg.get("ema_slow"), default=60),
        ret_short_mins=_as_int(cfg.get("ret_short_mins"), default=15),
        ret_long_mins=_as_int(cfg.get("ret_long_mins"), default=60),
        lookback_minutes=_as_int(cfg.get("lookback_minutes"), default=240),
        rev_rsi_prev_max=_as_float(cfg.get("rev_rsi_prev_max"), default=32.0),
        rev_rsi_now_min=_as_float(cfg.get("rev_rsi_now_min"), default=50.0),
        rev_ret_15m_max=_as_float(cfg.get("rev_ret_15m_max"), default=-0.008),
        rev_vol_z_min=_as_float(cfg.get("rev_vol_z_min"), default=1.3),
        exit_rsi=_as_float(cfg.get("exit_rsi"), default=45.0),
        stop_atr_mult=_as_float(cfg.get("stop_atr_mult"), default=1.1),
        trail_atr_mult=_as_float(cfg.get("trail_atr_mult"), default=0.9),
        time_stop_mom_minutes=_as_int(cfg.get("time_stop_mom_minutes"), default=240),
        time_stop_rev_minutes=_as_int(cfg.get("time_stop_rev_minutes"), default=120),
        cooldown_minutes=_as_int(cfg.get("cooldown_minutes"), default=30),
        atr_block_pct=_as_float(
            cfg.get("atr_block_pct"),
            default=_as_float(regime.get("volatility_block_atr_pct"), default=2.5),
        ),
        daily_loss_entry_block_ratio=_as_float(cfg.get("daily_loss_entry_block_ratio"), default=0.8),
        mom_weight_rsi=_as_float(cfg.get("mom_weight_rsi"), default=0.20),
        mom_weight_vol=_as_float(cfg.get("mom_weight_vol"), default=0.15),
        mom_weight_ret=_as_float(cfg.get("mom_weight_ret"), default=0.40),
        mom_weight_trend=_as_float(cfg.get("mom_weight_trend"), default=0.25),
        rev_penalty_when_trend_weak=_as_float(cfg.get("rev_penalty_when_trend_weak"), default=0.80),
        entry_alpha_spread_k=_as_float(cfg.get("entry_alpha_spread_k"), default=0.03),
        entry_alpha_fee_k=_as_float(cfg.get("entry_alpha_fee_k"), default=0.05),
    )


def compute_alpha_score(*, features: Mapping[str, Any], cfg: AlphaScoreConfig) -> AlphaScoreResult:
    rsi = _as_float(features.get("rsi_14"), default=50.0)
    rsi_prev = _as_float(features.get("rsi_14_prev"), default=rsi)
    vol_z = _as_float(features.get("vol_zscore"), default=0.0)
    ret_15m = _as_float(features.get("ret_15m"), default=0.0)
    ret_60m = _as_float(features.get("ret_60m"), default=0.0)
    ema20 = _as_float(features.get("ema20"), default=0.0)
    ema60 = _as_float(features.get("ema60"), default=0.0)
    atr_pct = _as_float(features.get("atr_pct"), default=0.0)

    s_rsi = clamp((rsi - 50.0) / 10.0, 0.0, 1.0)
    s_vol = clamp((vol_z - 1.0) / 0.8, 0.0, 1.0)
    s_ret = clamp(ret_60m / 0.015, 0.0, 1.0)
    s_trend = 1.0 if ema20 > ema60 else 0.0
    # Momentum score: prioritize directional components (ret/trend), keep RSI/vol as support.
    w_rsi = max(0.0, float(cfg.mom_weight_rsi))
    w_vol = max(0.0, float(cfg.mom_weight_vol))
    w_ret = max(0.0, float(cfg.mom_weight_ret))
    w_trend = max(0.0, float(cfg.mom_weight_trend))
    w_sum = max(1e-9, w_rsi + w_vol + w_ret + w_trend)
    mom_s = clamp(((w_rsi * s_rsi) + (w_vol * s_vol) + (w_ret * s_ret) + (w_trend * s_trend)) / w_sum, 0.0, 1.0)

    # Reversal score: 0/1 -> 0~1 (0.25-step) to avoid cliff-like jumps.
    c_prev = 1.0 if (rsi_prev <= float(cfg.rev_rsi_prev_max)) else 0.0
    c_now = 1.0 if (rsi >= float(cfg.rev_rsi_now_min)) else 0.0
    c_ret = 1.0 if (ret_15m <= float(cfg.rev_ret_15m_max)) else 0.0
    c_vol = 1.0 if (vol_z >= float(cfg.rev_vol_z_min)) else 0.0
    rev_s = 0.25 * (c_prev + c_now + c_ret + c_vol)
    trend_weak = bool((ema20 <= ema60) or (ret_60m < 0.0))
    rev_adj = rev_s * (float(cfg.rev_penalty_when_trend_weak) if trend_weak else 1.0)
    alpha = max(mom_s, rev_adj)
    den = max(1e-9, 1.0 - float(cfg.entry_alpha))
    strength = clamp((alpha - float(cfg.entry_alpha)) / den, 0.0, 1.0)
    if atr_pct <= 0:
        vol_scale = float(cfg.vol_scale_max)
    else:
        vol_scale = clamp(float(cfg.atr_ref_pct) / float(atr_pct), float(cfg.vol_scale_min), float(cfg.vol_scale_max))
    raw_target = float(cfg.base_target_pct) + (float(cfg.max_target_pct) - float(cfg.base_target_pct)) * float(strength)
    signal_target = clamp(raw_target * vol_scale, 0.0, float(cfg.max_target_pct))
    tag = "REV" if rev_adj >= mom_s and rev_adj > 0 else "MOM"
    return AlphaScoreResult(
        mom_s=float(mom_s),
        rev_s=float(rev_s),
        alpha=float(alpha),
        strength=float(strength),
        vol_scale=float(vol_scale),
        signal_target_pct=float(signal_target),
        strategy_tag_candidate=str(tag),
    )
