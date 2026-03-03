from __future__ import annotations

from dataclasses import dataclass
from math import exp
from typing import Any, Mapping

from ai_invest.config.rules_loader import RulesConfig


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, bool):
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s else float(default)
    except Exception:
        return float(default)


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return bool(value)
    s = str(value or "").strip().lower()
    if not s:
        return bool(default)
    return s in {"1", "true", "yes", "y", "on"}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _sigmoid(x: float) -> float:
    x_c = _clamp(float(x), -20.0, 20.0)
    return 1.0 / (1.0 + exp(-x_c))


@dataclass(frozen=True)
class SpreadGuardResult:
    enabled: bool
    base_limit_bps: float
    effective_limit_bps: float
    atr_component_bps: float
    liq_component_bps: float
    liq_score: float
    spread_pct: float
    atr_pct: float
    dv_zscore: float
    min_limit_bps: float
    max_limit_bps: float


def evaluate_spread_guard(
    *,
    rules: RulesConfig,
    spread_bps: float,
    mid_price: float,
    atr_pct: float,
    dv_zscore: float,
    alpha_cfg_raw: Mapping[str, Any] | None = None,
) -> SpreadGuardResult:
    cfg = alpha_cfg_raw if isinstance(alpha_cfg_raw, Mapping) else {}
    base = max(0.1, float(rules.cost_guard.max_spread_bps_entry))
    enabled = _as_bool(cfg.get("spread_dynamic_enabled"), default=True)

    atr_k = max(0.0, _as_float(cfg.get("spread_dynamic_atr_k"), default=0.35))
    liq_k = max(0.0, _as_float(cfg.get("spread_dynamic_liq_k"), default=2.0))
    liq_ref = max(0.1, _as_float(cfg.get("spread_dynamic_liq_ref"), default=_as_float(cfg.get("liq_dv_z_ref"), default=1.0)))
    liq_scale = max(0.2, _as_float(cfg.get("spread_dynamic_liq_scale"), default=0.8))
    min_mult = max(0.2, _as_float(cfg.get("spread_dynamic_min_mult"), default=0.8))
    max_mult = max(min_mult, _as_float(cfg.get("spread_dynamic_max_mult"), default=1.8))

    atr_v = max(0.0, float(atr_pct))
    dv_v = float(dv_zscore)
    spread_v = max(0.0, float(spread_bps))
    mid_v = max(0.0, float(mid_price))

    liq_score = _sigmoid((dv_v - liq_ref) / liq_scale)
    atr_component = atr_k * atr_v
    liq_component = liq_k * (1.0 - liq_score)
    raw_limit = base + atr_component + liq_component
    limit = raw_limit if enabled else base

    min_limit = base * min_mult
    max_limit = base * max_mult
    effective_limit = _clamp(limit, min_limit, max_limit)

    spread_pct = spread_v / 10000.0

    return SpreadGuardResult(
        enabled=bool(enabled),
        base_limit_bps=float(base),
        effective_limit_bps=float(effective_limit),
        atr_component_bps=float(atr_component),
        liq_component_bps=float(liq_component),
        liq_score=float(liq_score),
        spread_pct=float(max(0.0, spread_pct)),
        atr_pct=float(atr_v),
        dv_zscore=float(dv_v),
        min_limit_bps=float(min_limit),
        max_limit_bps=float(max_limit),
    )
