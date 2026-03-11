from __future__ import annotations

from typing import Any, Mapping

from ai_invest.domain.reason_codes import ReasonCode


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
            return int(round(value))
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


def _controller_config(rules_raw: Mapping[str, Any]) -> dict[str, Any]:
    cfg = (rules_raw.get("runtime_controller") or {}) if isinstance(rules_raw, Mapping) else {}
    return {
        "enabled": _as_bool(cfg.get("enabled"), default=True),
        "eval_min_samples": max(3, _as_int(cfg.get("eval_min_samples"), default=8)),
        "small_account_equity_krw": max(10_000.0, _as_float(cfg.get("small_account_equity_krw"), default=300_000.0)),
        "min_order_buffer_mult": _clamp(_as_float(cfg.get("min_order_buffer_mult"), default=1.02), 1.0, 1.20),
        "actionable_floor_alpha_margin": _clamp(
            _as_float(cfg.get("actionable_floor_alpha_margin"), default=0.05),
            0.0,
            0.20,
        ),
        "shock_defensive_threshold": _clamp(
            _as_float(cfg.get("shock_defensive_threshold"), default=0.45),
            0.20,
            0.95,
        ),
        "shock_block_threshold": _clamp(
            _as_float(cfg.get("shock_block_threshold"), default=0.78),
            0.30,
            0.99,
        ),
        "allow_reversal_on_elevated_news": _as_bool(
            cfg.get("allow_reversal_on_elevated_news"),
            default=False,
        ),
        "performance_credit_bps": _clamp(
            _as_float(cfg.get("performance_credit_bps"), default=25.0),
            5.0,
            100.0,
        ),
        "performance_penalty_bps": _clamp(
            _as_float(cfg.get("performance_penalty_bps"), default=25.0),
            5.0,
            100.0,
        ),
    }


def build_runtime_controls(
    *,
    rules_raw: Mapping[str, Any],
    account: Mapping[str, Any],
    risk_limits: Mapping[str, Any] | None = None,
    learning_feedback: Mapping[str, Any] | None = None,
    research_signal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _controller_config(rules_raw)
    equity_krw = max(0.0, _as_float(account.get("equity_krw"), default=_as_float(account.get("cash_krw"), default=0.0)))
    daily_loss_pct = max(0.0, _as_float(account.get("daily_loss_pct"), default=0.0))
    capital_profile = account.get("capital_profile") if isinstance(account.get("capital_profile"), Mapping) else {}
    risk_limits_map = risk_limits if isinstance(risk_limits, Mapping) else {}
    max_daily_loss_pct = max(0.01, _as_float(risk_limits_map.get("max_daily_loss_pct"), default=1.5))
    base_cap_pct = _as_float(
        capital_profile.get("max_target_position_pct"),
        default=_as_float(((rules_raw.get("governance") or {}).get("default_target_position_pct")), default=20.0),
    )
    base_cap_pct = max(0.0, base_cap_pct)
    max_position_pct_per_symbol = _as_float(
        capital_profile.get("max_position_pct_per_symbol"),
        default=_as_float(((rules_raw.get("risk") or {}).get("max_position_pct_per_symbol")), default=100.0),
    )
    hard_cap_pct = min(float(base_cap_pct or max_position_pct_per_symbol), float(max_position_pct_per_symbol or base_cap_pct))
    if not bool(cfg["enabled"]):
        return {
            "enabled": False,
            "mode": "NORMAL",
            "buy_enabled": True,
            "max_position_pct": float(max(0.0, hard_cap_pct)),
            "target_scale": 1.0,
            "entry_alpha_adj": 0.0,
            "min_edge_bps_adj": 0.0,
            "daily_loss_soft_limit_pct": float(
                max(
                    0.01,
                    _as_float((risk_limits or {}).get("max_daily_loss_pct"), default=1.5),
                )
            ),
            "allow_reversal_entries": True,
            "actionable_target_floor_pct": 0.0,
            "actionable_floor_alpha_margin": float(cfg["actionable_floor_alpha_margin"]),
            "news_shock_score": 0.0,
            "performance_score": 0.5,
            "execution_score": 0.5,
            "loss_pressure": 0.0,
            "sample_total": 0,
            "reason_codes": [ReasonCode.RG_PASS.value],
        }

    feedback = learning_feedback if isinstance(learning_feedback, Mapping) else {}
    symbol_profile = feedback.get("symbol_profile") if isinstance(feedback.get("symbol_profile"), Mapping) else {}
    trade_stats = symbol_profile.get("trade_stats") if isinstance(symbol_profile.get("trade_stats"), Mapping) else {}
    outcome_stats = symbol_profile.get("outcome_stats") if isinstance(symbol_profile.get("outcome_stats"), Mapping) else {}
    sample_total = max(0, _as_int(symbol_profile.get("sample_total"), default=0))
    avg_pnl_bps = _as_float(trade_stats.get("avg_pnl_bps"), default=0.0)
    win_rate = _as_float(trade_stats.get("win_rate_trades"), default=0.50)
    cost_ratio = _clamp(_as_float(outcome_stats.get("oc_cost_underestimated_ratio"), default=0.0), 0.0, 1.0)
    latency_ratio = _clamp(_as_float(outcome_stats.get("oc_execution_latency_ratio"), default=0.0), 0.0, 1.0)

    perf_credit_ref = float(cfg["performance_credit_bps"])
    perf_penalty_ref = float(cfg["performance_penalty_bps"])
    performance_score = 0.55
    if sample_total >= int(cfg["eval_min_samples"]):
        pnl_score = _clamp(
            (float(avg_pnl_bps) + float(perf_penalty_ref)) / (float(perf_penalty_ref) + float(perf_credit_ref)),
            0.0,
            1.0,
        )
        win_score = _clamp((float(win_rate) - 0.40) / 0.25, 0.0, 1.0)
        performance_score = _clamp((0.65 * pnl_score) + (0.35 * win_score), 0.0, 1.0)

    execution_score = _clamp(1.0 - (0.65 * float(cost_ratio)) - (0.35 * float(latency_ratio)), 0.0, 1.0)
    loss_pressure = _clamp(float(daily_loss_pct) / float(max_daily_loss_pct), 0.0, 1.0)

    research = research_signal if isinstance(research_signal, Mapping) else {}
    news_shock_score = _clamp(_as_float(research.get("shock_score"), default=0.0), 0.0, 1.0)
    news_signal_age_minutes = _as_float(research.get("report_age_minutes"), default=0.0)
    if news_signal_age_minutes > 240.0:
        news_shock_score *= 0.5

    target_scale = (
        0.25
        + (0.45 * float(performance_score))
        + (0.20 * float(execution_score))
        - (0.35 * float(loss_pressure))
        - (0.20 * float(news_shock_score))
    )
    target_scale = _clamp(float(target_scale), 0.20, 1.05)

    entry_alpha_adj = (
        (0.10 * (1.0 - float(performance_score)))
        + (0.06 * (1.0 - float(execution_score)))
        + (0.10 * float(loss_pressure))
        + (0.08 * float(news_shock_score))
    )
    entry_alpha_adj = _clamp(float(entry_alpha_adj), -0.03, 0.22)

    min_edge_bps_adj = (
        (10.0 * (1.0 - float(performance_score)))
        + (8.0 * (1.0 - float(execution_score)))
        + (12.0 * float(loss_pressure))
        + (10.0 * float(news_shock_score))
    )
    min_edge_bps_adj = _clamp(float(min_edge_bps_adj), 0.0, 36.0)

    daily_loss_soft_limit_pct = max_daily_loss_pct * (0.92 - 0.20 * float(news_shock_score))
    daily_loss_soft_limit_pct = _clamp(float(daily_loss_soft_limit_pct), max_daily_loss_pct * 0.60, max_daily_loss_pct)

    buy_enabled = bool(daily_loss_pct < daily_loss_soft_limit_pct and news_shock_score < float(cfg["shock_block_threshold"]))
    allow_reversal_entries = bool(
        news_shock_score < float(cfg["shock_defensive_threshold"])
        or bool(cfg["allow_reversal_on_elevated_news"])
    )

    max_position_pct = _clamp(float(hard_cap_pct) * float(target_scale), 0.0, float(max_position_pct_per_symbol))
    min_order_krw = max(0.0, _as_float(((rules_raw.get("execution") or {}).get("min_order_krw")), default=5_000.0))
    actionable_target_floor_pct = 0.0
    if equity_krw > 0 and min_order_krw > 0 and equity_krw <= float(cfg["small_account_equity_krw"]):
        actionable_target_floor_pct = (float(min_order_krw) / float(equity_krw)) * 100.0 * float(cfg["min_order_buffer_mult"])
        actionable_target_floor_pct = _clamp(float(actionable_target_floor_pct), 0.0, float(max_position_pct))

    if not buy_enabled:
        mode = "PAUSED" if float(news_shock_score) >= float(cfg["shock_block_threshold"]) else "DEFENSIVE"
    elif news_shock_score >= float(cfg["shock_defensive_threshold"]) or loss_pressure >= 0.65:
        mode = "DEFENSIVE"
    elif performance_score < 0.45 or execution_score < 0.50:
        mode = "LIMITED"
    else:
        mode = "NORMAL"

    reasons: list[str] = []
    if float(news_shock_score) >= float(cfg["shock_block_threshold"]):
        reasons.append(ReasonCode.RG_NEWS_RISK.value)
    if float(daily_loss_pct) >= float(daily_loss_soft_limit_pct):
        reasons.append(ReasonCode.RG_EXPOSURE_LIMIT.value)
    if not reasons:
        reasons.append(ReasonCode.RG_PASS.value)

    return {
        "enabled": bool(cfg["enabled"]),
        "mode": str(mode),
        "buy_enabled": bool(buy_enabled),
        "max_position_pct": float(max_position_pct),
        "target_scale": float(target_scale),
        "entry_alpha_adj": float(entry_alpha_adj),
        "min_edge_bps_adj": float(min_edge_bps_adj),
        "daily_loss_soft_limit_pct": float(daily_loss_soft_limit_pct),
        "allow_reversal_entries": bool(allow_reversal_entries),
        "actionable_target_floor_pct": float(actionable_target_floor_pct),
        "actionable_floor_alpha_margin": float(cfg["actionable_floor_alpha_margin"]),
        "news_shock_score": float(news_shock_score),
        "performance_score": float(performance_score),
        "execution_score": float(execution_score),
        "loss_pressure": float(loss_pressure),
        "sample_total": int(sample_total),
        "reason_codes": reasons[:3],
    }
