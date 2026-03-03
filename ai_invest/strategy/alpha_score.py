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
    # Regime switch thresholds.
    regime_trend_threshold: float
    regime_shock_threshold: float
    trend_ema_gap_ref: float
    trend_ret_ref: float
    shock_vol_z_ref: float
    shock_atr_ref: float
    # Candle/flow feature thresholds.
    trend_confirm_min_dv_z: float
    trend_wick_max: float
    range_ret_damp_ref: float
    # Regime-specific alpha mix weights.
    trend_weight_clv: float
    trend_weight_flow: float
    trend_weight_body: float
    trend_weight_wick_pen: float
    range_weight_rsi: float
    range_weight_clv: float
    range_weight_dv: float


@dataclass(frozen=True)
class AlphaScoreResult:
    mom_s: float
    rev_s: float
    alpha_raw: float
    alpha: float
    strength: float
    vol_scale: float
    signal_target_pct: float
    strategy_tag_candidate: str
    regime: str
    trend_strength: float
    shock_strength: float


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
        regime_trend_threshold=_as_float(cfg.get("regime_trend_threshold"), default=0.58),
        regime_shock_threshold=_as_float(cfg.get("regime_shock_threshold"), default=0.72),
        trend_ema_gap_ref=_as_float(cfg.get("trend_ema_gap_ref"), default=0.003),
        trend_ret_ref=_as_float(cfg.get("trend_ret_ref"), default=0.010),
        shock_vol_z_ref=_as_float(cfg.get("shock_vol_z_ref"), default=2.5),
        shock_atr_ref=_as_float(cfg.get("shock_atr_ref"), default=2.2),
        trend_confirm_min_dv_z=_as_float(cfg.get("trend_confirm_min_dv_z"), default=0.2),
        trend_wick_max=_as_float(cfg.get("trend_wick_max"), default=0.006),
        range_ret_damp_ref=_as_float(cfg.get("range_ret_damp_ref"), default=0.012),
        trend_weight_clv=_as_float(cfg.get("trend_weight_clv"), default=0.10),
        trend_weight_flow=_as_float(cfg.get("trend_weight_flow"), default=0.08),
        trend_weight_body=_as_float(cfg.get("trend_weight_body"), default=0.06),
        trend_weight_wick_pen=_as_float(cfg.get("trend_weight_wick_pen"), default=0.12),
        range_weight_rsi=_as_float(cfg.get("range_weight_rsi"), default=0.55),
        range_weight_clv=_as_float(cfg.get("range_weight_clv"), default=0.25),
        range_weight_dv=_as_float(cfg.get("range_weight_dv"), default=0.20),
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
    body_pct = _as_float(features.get("body_pct"), default=0.0)
    wick_pct = _as_float(features.get("wick_pct"), default=0.0)
    clv = _as_float(features.get("clv"), default=0.0)
    dv_z = _as_float(features.get("dv_zscore"), default=0.0)
    oflow = _as_float(features.get("oflow"), default=0.0)

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
    ema_gap = ((ema20 - ema60) / ema60) if abs(ema60) > 1e-9 else 0.0
    trend_strength = clamp(
        (max(0.0, ema_gap) / max(float(cfg.trend_ema_gap_ref), 1e-9))
        + (max(0.0, ret_60m) / max(float(cfg.trend_ret_ref), 1e-9)),
        0.0,
        1.0,
    )
    shock_vol = clamp(abs(vol_z) / max(float(cfg.shock_vol_z_ref), 1e-9), 0.0, 1.0)
    shock_atr = clamp(max(0.0, atr_pct) / max(float(cfg.shock_atr_ref), 1e-9), 0.0, 1.0)
    shock_wick = clamp(
        max(0.0, wick_pct - float(cfg.trend_wick_max)) / max(float(cfg.trend_wick_max), 1e-9),
        0.0,
        1.0,
    )
    shock_strength = clamp((0.55 * shock_vol) + (0.30 * shock_atr) + (0.15 * shock_wick), 0.0, 1.0)

    regime = "RANGE"
    if shock_strength >= float(cfg.regime_shock_threshold):
        regime = "SHOCK"
    elif trend_strength >= float(cfg.regime_trend_threshold):
        regime = "TREND"

    clv_long = clamp((clv + 1.0) * 0.5, 0.0, 1.0)
    clv_reversal = clamp((-clv + 1.0) * 0.5, 0.0, 1.0)
    flow_long = clamp((oflow + 1.0) * 0.5, 0.0, 1.0)
    body_score = clamp(body_pct / 0.004, 0.0, 1.0)
    wick_penalty = clamp(wick_pct / max(float(cfg.trend_wick_max), 1e-9), 0.0, 1.0)
    dv_score = clamp((dv_z - float(cfg.trend_confirm_min_dv_z)) / 2.0, 0.0, 1.0)
    dv_range = clamp(dv_z / 3.0, 0.0, 1.0)
    ret_damp = 1.0 - clamp(abs(ret_15m) / max(float(cfg.range_ret_damp_ref), 1e-9), 0.0, 1.0)

    trend_alpha = clamp(
        mom_s
        + (float(cfg.trend_weight_clv) * (clv_long - 0.5))
        + (float(cfg.trend_weight_flow) * (flow_long - 0.5))
        + (float(cfg.trend_weight_body) * (body_score - 0.4))
        - (float(cfg.trend_weight_wick_pen) * wick_penalty),
        0.0,
        1.0,
    )
    if dv_z < float(cfg.trend_confirm_min_dv_z):
        trend_alpha *= 0.75
    if wick_pct > float(cfg.trend_wick_max):
        trend_alpha *= 0.65
    trend_alpha = clamp(trend_alpha * (0.85 + 0.15 * dv_score), 0.0, 1.0)

    range_w_sum = max(
        1e-9,
        float(cfg.range_weight_rsi) + float(cfg.range_weight_clv) + float(cfg.range_weight_dv),
    )
    range_alpha = (
        (float(cfg.range_weight_rsi) * rev_adj)
        + (float(cfg.range_weight_clv) * clv_reversal)
        + (float(cfg.range_weight_dv) * dv_range)
    ) / range_w_sum
    range_alpha = clamp(range_alpha * ret_damp, 0.0, 1.0)

    if regime == "TREND":
        alpha_raw = trend_alpha
        tag = "MOM"
    elif regime == "RANGE":
        alpha_raw = range_alpha
        tag = "REV" if range_alpha >= trend_alpha else "MOM"
    else:
        alpha_raw = 0.0
        tag = "MOM"

    alpha = clamp(alpha_raw, 0.0, 1.0)
    den = max(1e-9, 1.0 - float(cfg.entry_alpha))
    strength = clamp((alpha - float(cfg.entry_alpha)) / den, 0.0, 1.0)
    if atr_pct <= 0:
        vol_scale = float(cfg.vol_scale_max)
    else:
        vol_scale = clamp(float(cfg.atr_ref_pct) / float(atr_pct), float(cfg.vol_scale_min), float(cfg.vol_scale_max))
    raw_target = float(cfg.base_target_pct) + (float(cfg.max_target_pct) - float(cfg.base_target_pct)) * float(strength)
    signal_target = clamp(raw_target * vol_scale, 0.0, float(cfg.max_target_pct))
    return AlphaScoreResult(
        mom_s=float(mom_s),
        rev_s=float(rev_s),
        alpha_raw=float(alpha_raw),
        alpha=float(alpha),
        strength=float(strength),
        vol_scale=float(vol_scale),
        signal_target_pct=float(signal_target),
        strategy_tag_candidate=str(tag),
        regime=str(regime),
        trend_strength=float(trend_strength),
        shock_strength=float(shock_strength),
    )
