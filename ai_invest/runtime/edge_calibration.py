from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import exp, sqrt
from typing import Any, Mapping, Sequence


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


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, bool):
            return int(default)
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return int(value)
        s = str(value).strip()
        return int(float(s)) if s else int(default)
    except Exception:
        return int(default)


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return bool(value)
    if value is None:
        return bool(default)
    s = str(value).strip().lower()
    if not s:
        return bool(default)
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _ensure_utc(value: datetime | None) -> datetime:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_utc(value)
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return _ensure_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except Exception:
        return None


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    q = _clamp(float(q), 0.0, 1.0)
    pos = q * float(len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return float(ordered[lo])
    frac = pos - float(lo)
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * frac)


def _robust_scale(values: Sequence[float], *, fallback: float, floor: float) -> float:
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return max(float(fallback), float(floor))
    q25 = _quantile(ordered, 0.25)
    q75 = _quantile(ordered, 0.75)
    spread = max(0.0, float(q75) - float(q25))
    if spread <= 0.0:
        spread = max(0.0, float(max(ordered)) - float(min(ordered)))
    if spread <= 0.0:
        spread = float(fallback)
    return max(float(floor), float(spread), float(fallback))


def load_edge_calibration_config(*, rules_raw: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = (rules_raw or {}).get("runtime_calibration") if isinstance(rules_raw, Mapping) else {}
    raw_map = raw if isinstance(raw, Mapping) else {}
    return {
        "enabled": _as_bool(raw_map.get("enabled"), default=True),
        "refresh_minutes": max(1, _as_int(raw_map.get("refresh_minutes"), default=10)),
        "max_age_days": max(14, _as_int(raw_map.get("max_age_days"), default=365)),
        "safe_decision_limit": max(200, _as_int(raw_map.get("safe_decision_limit"), default=5000)),
        "trade_limit": max(200, _as_int(raw_map.get("trade_limit"), default=5000)),
        "outcome_limit": max(200, _as_int(raw_map.get("outcome_limit"), default=5000)),
        "min_samples": max(8, _as_int(raw_map.get("min_samples"), default=24)),
        "alpha_entry_percentile": _clamp(_as_float(raw_map.get("alpha_entry_percentile"), default=0.60), 0.20, 0.95),
        "alpha_promotion_percentile": _clamp(_as_float(raw_map.get("alpha_promotion_percentile"), default=0.70), 0.20, 0.98),
        "alpha_floor_min": _clamp(_as_float(raw_map.get("alpha_floor_min"), default=0.16), 0.05, 0.60),
        "neighbor_k": max(12, _as_int(raw_map.get("neighbor_k"), default=96)),
        "shrinkage_samples": _clamp(_as_float(raw_map.get("shrinkage_samples"), default=18.0), 4.0, 100.0),
        "uncertainty_floor_bps": _clamp(_as_float(raw_map.get("uncertainty_floor_bps"), default=4.0), 0.0, 30.0),
        "uncertainty_mult": _clamp(_as_float(raw_map.get("uncertainty_mult"), default=1.10), 0.25, 3.0),
        "min_required_after_cost_bps": _clamp(_as_float(raw_map.get("min_required_after_cost_bps"), default=0.0), 0.0, 30.0),
        "min_effective_samples": _clamp(_as_float(raw_map.get("min_effective_samples"), default=6.0), 1.0, 40.0),
    }


@dataclass(frozen=True)
class EdgeCalibrationSample:
    decision_id: str
    ts_utc: datetime
    alpha_raw: float
    spread_bps: float
    atr_pct: float
    dv_zscore: float
    strategy_tag: str | None
    regime: str | None
    expected_cost_bps: float
    realized_after_cost_bps: float
    outcome_label: str


@dataclass(frozen=True)
class EdgeCalibrationDataset:
    enabled: bool
    symbol: str
    built_at_utc: datetime
    sample_count: int
    samples: tuple[EdgeCalibrationSample, ...]
    global_mean_after_cost_bps: float
    alpha_entry_threshold: float
    alpha_promotion_threshold: float
    alpha_scale: float
    spread_scale: float
    atr_scale: float
    dv_scale: float
    neighbor_k: int
    shrinkage_samples: float
    uncertainty_floor_bps: float
    uncertainty_mult: float
    min_required_after_cost_bps: float
    min_effective_samples: float
    max_age_days: int

    def as_runtime_summary(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "symbol": str(self.symbol),
            "built_at_utc": self.built_at_utc.isoformat(),
            "sample_count": int(self.sample_count),
            "global_mean_after_cost_bps": float(self.global_mean_after_cost_bps),
            "entry_alpha_threshold": float(self.alpha_entry_threshold),
            "promotion_alpha_threshold": float(self.alpha_promotion_threshold),
            "alpha_scale": float(self.alpha_scale),
            "spread_scale": float(self.spread_scale),
            "atr_scale": float(self.atr_scale),
            "dv_scale": float(self.dv_scale),
        }


def _empty_dataset(*, symbol: str, now_utc: datetime, cfg: Mapping[str, Any]) -> EdgeCalibrationDataset:
    return EdgeCalibrationDataset(
        enabled=False,
        symbol=str(symbol or "").strip().upper(),
        built_at_utc=_ensure_utc(now_utc),
        sample_count=0,
        samples=(),
        global_mean_after_cost_bps=0.0,
        alpha_entry_threshold=float(cfg.get("alpha_floor_min", 0.16)),
        alpha_promotion_threshold=float(cfg.get("alpha_floor_min", 0.16)),
        alpha_scale=0.10,
        spread_scale=2.0,
        atr_scale=0.04,
        dv_scale=0.75,
        neighbor_k=max(12, _as_int(cfg.get("neighbor_k"), default=96)),
        shrinkage_samples=float(cfg.get("shrinkage_samples", 18.0)),
        uncertainty_floor_bps=float(cfg.get("uncertainty_floor_bps", 4.0)),
        uncertainty_mult=float(cfg.get("uncertainty_mult", 1.10)),
        min_required_after_cost_bps=float(cfg.get("min_required_after_cost_bps", 0.0)),
        min_effective_samples=float(cfg.get("min_effective_samples", 6.0)),
        max_age_days=max(14, _as_int(cfg.get("max_age_days"), default=365)),
    )


def build_edge_calibration_dataset(
    *,
    events: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    symbol: str,
    now_utc: datetime | None = None,
    rules_raw: Mapping[str, Any] | None = None,
) -> EdgeCalibrationDataset:
    cfg = load_edge_calibration_config(rules_raw=rules_raw)
    now = _ensure_utc(now_utc)
    sym = str(symbol or "").strip().upper()
    if not bool(cfg["enabled"]):
        return _empty_dataset(symbol=sym, now_utc=now, cfg=cfg)

    cutoff = now - timedelta(days=max(14, int(cfg["max_age_days"])))
    trade_by_id: dict[str, Mapping[str, Any]] = {}
    for trade in trades or []:
        if not isinstance(trade, Mapping):
            continue
        if str(trade.get("symbol") or "").strip().upper() != sym:
            continue
        trade_id = str(trade.get("trade_id") or "").strip()
        if not trade_id:
            continue
        trade_by_id[trade_id] = trade

    outcome_by_decision: dict[str, Mapping[str, Any]] = {}
    for outcome in outcomes or []:
        if not isinstance(outcome, Mapping):
            continue
        if str(outcome.get("symbol") or "").strip().upper() != sym:
            continue
        decision_id = str(outcome.get("decision_id") or "").strip()
        trade_id = str(outcome.get("trade_id") or "").strip()
        closed_at = _parse_dt(outcome.get("ts_close"))
        if not decision_id or not trade_id or closed_at is None or closed_at < cutoff:
            continue
        if trade_id not in trade_by_id:
            continue
        prev = outcome_by_decision.get(decision_id)
        if prev is None:
            outcome_by_decision[decision_id] = outcome
            continue
        prev_closed = _parse_dt(prev.get("ts_close"))
        if prev_closed is None or closed_at > prev_closed:
            outcome_by_decision[decision_id] = outcome

    samples: list[EdgeCalibrationSample] = []
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        if str(event.get("event_type") or "").strip().upper() != "SAFE_DECISION":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        event_symbol = str(payload.get("symbol") or event.get("symbol") or "").strip().upper()
        if event_symbol != sym:
            continue
        decision_id = str(payload.get("decision_id") or event.get("entity_id") or "").strip()
        if not decision_id:
            continue
        outcome = outcome_by_decision.get(decision_id)
        if not isinstance(outcome, Mapping):
            continue
        trade = trade_by_id.get(str(outcome.get("trade_id") or "").strip())
        if not isinstance(trade, Mapping):
            continue
        ts_utc = _parse_dt(event.get("ts"))
        if ts_utc is None or ts_utc < cutoff:
            continue
        agent_inputs = payload.get("agent_inputs") if isinstance(payload.get("agent_inputs"), Mapping) else {}
        market = agent_inputs.get("market") if isinstance(agent_inputs.get("market"), Mapping) else {}
        reason = market.get("reason") if isinstance(market.get("reason"), Mapping) else {}
        decision = payload.get("decision") if isinstance(payload.get("decision"), Mapping) else {}
        gates = decision.get("gates") if isinstance(decision.get("gates"), Mapping) else {}
        alpha_raw = _as_float(market.get("alpha_raw"), default=_as_float(market.get("alpha"), default=0.0))
        spread_bps = _as_float(gates.get("spread_bps"), default=_as_float(reason.get("spread_bps"), default=0.0))
        atr_pct = _as_float(reason.get("atr_pct"), default=0.0)
        dv_zscore = _as_float(reason.get("dv_zscore"), default=0.0)
        expected_cost_bps = _as_float(
            market.get("expected_cost_bps"),
            default=_as_float(gates.get("market_expected_cost_bps"), default=0.0),
        )
        strategy_tag = str(market.get("strategy_tag") or "").strip().upper() or None
        regime = str(market.get("regime") or "").strip().upper() or None
        realized_after_cost_bps = _as_float(trade.get("pnl_bps"), default=0.0)
        outcome_label = str(outcome.get("outcome_label") or "").strip().upper() or "UNKNOWN"
        samples.append(
            EdgeCalibrationSample(
                decision_id=decision_id,
                ts_utc=ts_utc,
                alpha_raw=float(alpha_raw),
                spread_bps=float(spread_bps),
                atr_pct=float(atr_pct),
                dv_zscore=float(dv_zscore),
                strategy_tag=strategy_tag,
                regime=regime,
                expected_cost_bps=float(expected_cost_bps),
                realized_after_cost_bps=float(realized_after_cost_bps),
                outcome_label=outcome_label,
            )
        )

    if len(samples) < int(cfg["min_samples"]):
        return _empty_dataset(symbol=sym, now_utc=now, cfg=cfg)

    samples.sort(key=lambda item: item.ts_utc, reverse=True)
    alpha_values = [float(s.alpha_raw) for s in samples]
    spread_values = [float(s.spread_bps) for s in samples]
    atr_values = [float(s.atr_pct) for s in samples]
    dv_values = [float(s.dv_zscore) for s in samples]
    pnl_values = [float(s.realized_after_cost_bps) for s in samples]
    alpha_floor = float(cfg["alpha_floor_min"])
    entry_alpha_threshold = max(alpha_floor, _quantile(alpha_values, float(cfg["alpha_entry_percentile"])))
    promotion_alpha_threshold = max(alpha_floor, _quantile(alpha_values, float(cfg["alpha_promotion_percentile"])))

    return EdgeCalibrationDataset(
        enabled=True,
        symbol=sym,
        built_at_utc=now,
        sample_count=len(samples),
        samples=tuple(samples),
        global_mean_after_cost_bps=sum(pnl_values) / float(len(pnl_values)),
        alpha_entry_threshold=float(entry_alpha_threshold),
        alpha_promotion_threshold=float(promotion_alpha_threshold),
        alpha_scale=_robust_scale(alpha_values, fallback=0.10, floor=0.05),
        spread_scale=_robust_scale(spread_values, fallback=2.0, floor=1.0),
        atr_scale=_robust_scale(atr_values, fallback=0.04, floor=0.02),
        dv_scale=_robust_scale(dv_values, fallback=0.75, floor=0.35),
        neighbor_k=max(12, min(int(cfg["neighbor_k"]), len(samples))),
        shrinkage_samples=float(cfg["shrinkage_samples"]),
        uncertainty_floor_bps=float(cfg["uncertainty_floor_bps"]),
        uncertainty_mult=float(cfg["uncertainty_mult"]),
        min_required_after_cost_bps=float(cfg["min_required_after_cost_bps"]),
        min_effective_samples=float(cfg["min_effective_samples"]),
        max_age_days=max(14, int(cfg["max_age_days"])),
    )


def evaluate_edge_calibration(
    *,
    dataset: EdgeCalibrationDataset | None,
    alpha_raw: float,
    spread_bps: float,
    atr_pct: float,
    dv_zscore: float,
    regime: str | None,
    strategy_tag: str | None,
    current_expected_cost_bps: float,
) -> dict[str, Any]:
    if dataset is None or not bool(getattr(dataset, "enabled", False)) or not getattr(dataset, "samples", None):
        return {
            "enabled": False,
            "sample_count": int(getattr(dataset, "sample_count", 0) if dataset is not None else 0),
            "predicted_after_cost_bps": 0.0,
            "required_after_cost_bps": 0.0,
        }

    scored: list[tuple[float, EdgeCalibrationSample]] = []
    regime_norm = str(regime or "").strip().upper() or None
    tag_norm = str(strategy_tag or "").strip().upper() or None
    for sample in dataset.samples:
        w = 1.0
        w *= exp(-abs(float(alpha_raw) - float(sample.alpha_raw)) / max(float(dataset.alpha_scale), 1e-6))
        w *= exp(-abs(float(spread_bps) - float(sample.spread_bps)) / max(float(dataset.spread_scale), 1e-6))
        w *= exp(-abs(float(atr_pct) - float(sample.atr_pct)) / max(float(dataset.atr_scale), 1e-6))
        w *= exp(-abs(float(dv_zscore) - float(sample.dv_zscore)) / max(float(dataset.dv_scale), 1e-6))
        if tag_norm and sample.strategy_tag:
            w *= 1.12 if tag_norm == str(sample.strategy_tag).upper() else 0.88
        if regime_norm and sample.regime:
            w *= 1.08 if regime_norm == str(sample.regime).upper() else 0.92
        if w > 1e-6:
            scored.append((float(w), sample))

    if not scored:
        return {
            "enabled": False,
            "sample_count": int(dataset.sample_count),
            "predicted_after_cost_bps": 0.0,
            "required_after_cost_bps": 0.0,
        }

    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > int(dataset.neighbor_k):
        scored = scored[: int(dataset.neighbor_k)]

    weights = [float(w) for w, _ in scored]
    sum_w = sum(weights)
    sum_w_sq = sum(float(w) * float(w) for w in weights)
    if sum_w <= 0.0 or sum_w_sq <= 0.0:
        return {
            "enabled": False,
            "sample_count": int(dataset.sample_count),
            "predicted_after_cost_bps": 0.0,
            "required_after_cost_bps": 0.0,
        }

    n_eff = (sum_w * sum_w) / sum_w_sq
    local_mean = sum(float(w) * float(s.realized_after_cost_bps) for w, s in scored) / sum_w
    cost_weight_sum = sum(float(w) for w, s in scored if float(s.expected_cost_bps) > 0.1)
    if cost_weight_sum > max(2.0, sum_w * 0.20):
        hist_cost_mean = (
            sum(float(w) * float(s.expected_cost_bps) for w, s in scored if float(s.expected_cost_bps) > 0.1)
            / cost_weight_sum
        )
        cost_delta_bps = float(current_expected_cost_bps) - float(hist_cost_mean)
        cost_adjust_available = True
    else:
        hist_cost_mean = float(current_expected_cost_bps)
        cost_delta_bps = 0.0
        cost_adjust_available = False
    shrink = float(n_eff) / (float(n_eff) + float(dataset.shrinkage_samples))
    blended_mean = (float(shrink) * float(local_mean)) + ((1.0 - float(shrink)) * float(dataset.global_mean_after_cost_bps))
    predicted_after_cost_bps = float(blended_mean) - float(cost_delta_bps)
    local_var = sum(float(w) * ((float(s.realized_after_cost_bps) - float(local_mean)) ** 2) for w, s in scored) / sum_w
    local_std = sqrt(max(0.0, float(local_var)))
    uncertainty_bps = max(
        float(dataset.uncertainty_floor_bps),
        (float(local_std) / max(sqrt(max(float(n_eff), 1.0)), 1.0)) * float(dataset.uncertainty_mult),
    )
    coverage_penalty_bps = max(0.0, float(dataset.min_effective_samples) - float(n_eff)) * 1.25
    required_after_cost_bps = max(
        float(dataset.min_required_after_cost_bps),
        float(uncertainty_bps) + float(coverage_penalty_bps),
    )
    gross_edge_bps = max(0.0, float(predicted_after_cost_bps) + float(current_expected_cost_bps))
    positive_weight = sum(float(w) for w, s in scored if float(s.realized_after_cost_bps) > 0.0)
    alpha_percentile = sum(1 for s in dataset.samples if float(s.alpha_raw) <= float(alpha_raw)) / float(len(dataset.samples))

    return {
        "enabled": True,
        "symbol": str(dataset.symbol),
        "sample_count": int(dataset.sample_count),
        "neighbor_count": int(len(scored)),
        "effective_sample_size": float(n_eff),
        "global_mean_after_cost_bps": float(dataset.global_mean_after_cost_bps),
        "local_mean_after_cost_bps": float(local_mean),
        "hist_cost_mean_bps": float(hist_cost_mean),
        "cost_delta_bps": float(cost_delta_bps),
        "cost_adjust_available": bool(cost_adjust_available),
        "predicted_after_cost_bps": float(predicted_after_cost_bps),
        "required_after_cost_bps": float(required_after_cost_bps),
        "uncertainty_bps": float(uncertainty_bps),
        "coverage_penalty_bps": float(coverage_penalty_bps),
        "gross_edge_bps": float(gross_edge_bps),
        "weighted_positive_share": float(positive_weight / sum_w) if sum_w > 0 else 0.0,
        "entry_alpha_threshold": float(dataset.alpha_entry_threshold),
        "promotion_alpha_threshold": float(dataset.alpha_promotion_threshold),
        "alpha_percentile": float(alpha_percentile),
        "model": "weighted_after_cost_similarity_v1",
    }


def resolve_effective_cap_min_alpha(*, configured_min_alpha: float, edge_calibration: Mapping[str, Any] | None) -> float:
    base = max(0.0, float(configured_min_alpha))
    if not isinstance(edge_calibration, Mapping) or not bool(edge_calibration.get("enabled", False)):
        return float(base)
    calibrated = _as_float(edge_calibration.get("promotion_alpha_threshold"), default=base)
    if calibrated <= 0.0:
        return float(base)
    return float(min(base, calibrated)) if base > 0.0 else float(calibrated)
