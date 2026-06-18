from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ai_invest.config.rules_loader import RulesConfig
from ai_invest.domain.reason_codes import ReasonCode, parse_reason_code, validate_reason_codes
from ai_invest.strategy.spread_guard import evaluate_spread_guard


class SafeJudgeContractError(ValueError):
    """Raised when the common input contract is violated."""


def _dot_get(payload: Mapping[str, Any], path: str) -> Any:
    node: Any = payload
    for key in path.split("."):
        if not isinstance(node, Mapping) or key not in node:
            raise SafeJudgeContractError(f"Missing required key: {path}")
        node = node[key]
    return node


def _as_bool(payload: Mapping[str, Any], path: str) -> bool:
    value = _dot_get(payload, path)
    if not isinstance(value, bool):
        raise SafeJudgeContractError(f"Expected bool at {path}, got {type(value).__name__}")
    return value


def _as_float(payload: Mapping[str, Any], path: str) -> float:
    value = _dot_get(payload, path)
    if not isinstance(value, (int, float)):
        raise SafeJudgeContractError(f"Expected number at {path}, got {type(value).__name__}")
    return float(value)


def _as_str(payload: Mapping[str, Any], path: str) -> str:
    value = _dot_get(payload, path)
    if not isinstance(value, str) or not value:
        raise SafeJudgeContractError(f"Expected non-empty string at {path}")
    return value


def _opt_float(payload: Mapping[str, Any], path: str) -> float | None:
    try:
        value = _dot_get(payload, path)
    except Exception:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        s = str(value).strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def _opt_bool(payload: Mapping[str, Any], path: str) -> bool | None:
    try:
        value = _dot_get(payload, path)
    except Exception:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "1", "yes", "y", "on"}:
            return True
        if s in {"false", "0", "no", "n", "off"}:
            return False
    return None


def _opt_str(payload: Mapping[str, Any], path: str) -> str | None:
    try:
        value = _dot_get(payload, path)
    except Exception:
        return None
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _extract_reason_codes(payload: Mapping[str, Any] | None) -> list[ReasonCode]:
    if not isinstance(payload, Mapping):
        return []
    raw_items: list[Any] = []
    for key in ("reason_codes", "selected_reasons"):
        value = payload.get(key)
        if isinstance(value, (list, tuple)):
            raw_items.extend(value)
    single = payload.get("reason_code")
    if single is not None:
        raw_items.append(single)
    out: list[ReasonCode] = []
    seen: set[str] = set()
    for raw in raw_items:
        try:
            code = parse_reason_code(raw)
        except Exception:
            continue
        if code.value in seen:
            continue
        seen.add(code.value)
        out.append(code)
    return out


def _non_pass_reasons(codes: list[ReasonCode]) -> list[ReasonCode]:
    return [c for c in codes if c != ReasonCode.RG_PASS]


@dataclass(frozen=True)
class SafeJudgeDecision:
    action: str  # BUY / SELL / HOLD / PAUSE
    score: float | None
    confidence: float | None
    effective_target_pct: float | None
    gates: dict[str, Any]
    selected_reasons: list[str]
    rejected_reasons: list[str]
    expected_cost_bps: float | None
    expected_rr: float | None


def safe_judge_decide(
    payload: Mapping[str, Any],
    *,
    rules: RulesConfig,
    market: Mapping[str, Any] | None = None,
    regime: Mapping[str, Any] | None = None,
    risk: Mapping[str, Any] | None = None,
    ops: Mapping[str, Any] | None = None,
) -> SafeJudgeDecision:
    """Safe Judge: hard gates first, then follow market signal if allowed.

    Contract inputs are based on `agents.md` common input contract + agent opinion outputs.
    This function is deterministic and does not execute orders.
    """

    symbol = _as_str(payload, "symbol")
    spread_bps = _as_float(payload, "snapshot.spread_bps")
    mid_price = _opt_float(payload, "snapshot.mid_price") or 0.0
    atr_pct = _opt_float(payload, "features.atr_pct") or 0.0
    dv_zscore = _opt_float(payload, "features.dv_zscore") or 0.0
    runtime_rules_hash = _opt_str(payload, "context.runtime.rules_hash")
    runtime_universe_id = _opt_str(payload, "context.runtime.universe_id")
    runtime_universe_mode = _opt_str(payload, "context.runtime.universe_mode")
    recon_status = _as_str(payload, "ops.reconciliation_status").upper()
    rate_limit_alert = _as_bool(payload, "ops.rate_limit_alert")
    pause_state = _as_bool(payload, "ops.pause_state")
    daily_loss_pct = _as_float(payload, "context.account.daily_loss_pct")
    daily_trades_count = _opt_float(payload, "context.account.daily_trades_count")

    # Optional context fields (paper/live sizing, trade plan).
    trade_plan_target_pct = _opt_float(payload, "context.trade_plan.target_position_pct")
    trade_plan_execution_target_pct = _opt_float(payload, "context.trade_plan.execution_plan.final_numbers.target_position_pct")
    trade_plan_buy_allowed = _opt_bool(payload, "context.trade_plan.allowed_actions.buy")
    trade_plan_sell_allowed = _opt_bool(payload, "context.trade_plan.allowed_actions.sell")
    trade_plan_activation_decision = _opt_str(payload, "context.trade_plan.activation_gate.decision")
    trade_plan_activation_decision_effective = _opt_str(payload, "context.trade_plan.activation_gate.decision_effective")
    trade_plan_hold_mode = _opt_str(payload, "context.trade_plan.activation_gate.hold_mode")
    trade_plan_cap_promoted = _opt_bool(payload, "context.trade_plan.activation_gate.cap_promoted")
    trade_plan_inter_slot_realtime_mode = _opt_bool(payload, "context.trade_plan.activation_gate.inter_slot_realtime_mode")
    trade_plan_final_no_trade = _opt_bool(payload, "context.trade_plan.activation_gate.final_plan_no_trade_declared")
    trade_plan_runtime_entry_allowed = _opt_bool(payload, "context.trade_plan.runtime_entry_policy.runtime_entry_allowed")
    trade_plan_runtime_promotion_enabled = _opt_bool(
        payload,
        "context.trade_plan.runtime_entry_policy.runtime_promotion_enabled",
    )
    trade_plan_runtime_execution_authority = _opt_str(
        payload,
        "context.trade_plan.runtime_entry_policy.execution_authority",
    )
    trade_plan_runtime_entry_objective = _opt_str(
        payload,
        "context.trade_plan.runtime_entry_policy.entry_objective",
    )
    trade_plan_runtime_exploration_enabled = _opt_bool(
        payload,
        "context.trade_plan.runtime_entry_policy.exploration_enabled",
    )
    trade_plan_runtime_profit_floor_bps = _opt_float(
        payload,
        "context.trade_plan.runtime_entry_policy.profit_floor_bps",
    )
    trade_plan_runtime_profit_required_margin_bps = _opt_float(
        payload,
        "context.trade_plan.runtime_entry_policy.profit_required_margin_bps",
    )
    trade_plan_runtime_promotion_active = _opt_bool(payload, "context.trade_plan.runtime_entry_policy.promotion_active")
    trade_plan_runtime_learning_mode = _opt_bool(payload, "context.trade_plan.runtime_entry_policy.learning_mode")
    trade_plan_runtime_min_predicted_after_cost_bps = _opt_float(
        payload,
        "context.trade_plan.runtime_entry_policy.min_predicted_after_cost_bps",
    )
    trade_plan_runtime_alpha_bypass_on_exploration = _opt_bool(
        payload,
        "context.trade_plan.runtime_entry_policy.alpha_bypass_on_exploration",
    )
    try:
        trade_plan_cap_runtime = _dot_get(payload, "context.trade_plan.activation_gate.cap_runtime")
    except Exception:
        trade_plan_cap_runtime = None
    context_map = payload.get("context") if isinstance(payload.get("context"), Mapping) else {}
    runtime_controls = context_map.get("runtime_controls") if isinstance(context_map.get("runtime_controls"), Mapping) else {}
    current_position_pct = _opt_float(payload, "context.position.current_position_pct")
    cash_krw = _opt_float(payload, "context.account.cash_krw")
    runtime_buy_enabled = bool((runtime_controls or {}).get("buy_enabled", True))
    runtime_max_position_pct = _opt_float(payload, "context.runtime_controls.max_position_pct")
    runtime_actionable_floor_pct = _opt_float(payload, "context.runtime_controls.actionable_target_floor_pct")
    runtime_reason_codes = _extract_reason_codes(runtime_controls if isinstance(runtime_controls, Mapping) else None)
    market_signal_target_pct = None
    market_expected_edge_bps = None
    market_expected_cost_bps = None
    market_expected_net_edge_bps = None
    market_min_edge_required_bps = None
    market_regime = None
    market_reason_map = (market or {}).get("reason") if isinstance((market or {}).get("reason"), Mapping) else {}
    market_edge_calibration = (
        market_reason_map.get("edge_calibration") if isinstance(market_reason_map.get("edge_calibration"), Mapping) else {}
    )
    if not market_edge_calibration and isinstance((market or {}).get("edge_calibration"), Mapping):
        market_edge_calibration = dict((market or {}).get("edge_calibration") or {})
    market_predicted_after_cost_bps = None
    market_after_cost_required_bps = None
    market_after_cost_uncertainty_bps = None
    if market is not None:
        try:
            market_signal_target_pct = float((market or {}).get("signal_target_pct"))
        except Exception:
            market_signal_target_pct = None
        try:
            market_expected_edge_bps = float((market or {}).get("expected_edge_bps"))
        except Exception:
            market_expected_edge_bps = None
        try:
            market_expected_cost_bps = float((market or {}).get("expected_cost_bps"))
        except Exception:
            market_expected_cost_bps = None
        try:
            market_expected_net_edge_bps = float((market or {}).get("expected_net_edge_bps"))
        except Exception:
            market_expected_net_edge_bps = None
        try:
            market_min_edge_required_bps = float((market or {}).get("min_edge_required_bps"))
        except Exception:
            market_min_edge_required_bps = None
        try:
            market_regime = str((market or {}).get("regime") or "").strip().upper() or None
        except Exception:
            market_regime = None
        try:
            market_predicted_after_cost_bps = float((market_edge_calibration or {}).get("predicted_after_cost_bps"))
        except Exception:
            try:
                market_predicted_after_cost_bps = float((market or {}).get("predicted_after_cost_bps"))
            except Exception:
                market_predicted_after_cost_bps = None
        try:
            market_after_cost_required_bps = float((market_edge_calibration or {}).get("required_after_cost_bps"))
        except Exception:
            try:
                market_after_cost_required_bps = float((market or {}).get("required_after_cost_bps"))
            except Exception:
                market_after_cost_required_bps = None
        try:
            market_after_cost_uncertainty_bps = float((market_edge_calibration or {}).get("uncertainty_bps"))
        except Exception:
            try:
                market_after_cost_uncertainty_bps = float((market or {}).get("after_cost_uncertainty_bps"))
            except Exception:
                market_after_cost_uncertainty_bps = None
    if market_predicted_after_cost_bps is None and market_expected_net_edge_bps is not None:
        market_predicted_after_cost_bps = float(market_expected_net_edge_bps)
    if market_after_cost_required_bps is None and market_min_edge_required_bps is not None:
        market_after_cost_required_bps = float(market_min_edge_required_bps)
    market_reason_codes = _extract_reason_codes(market)
    regime_reason_codes = _extract_reason_codes(regime)
    risk_reason_codes = _extract_reason_codes(risk)
    ops_reason_codes = _extract_reason_codes(ops)
    market_signal = str((market or {}).get("signal", "HOLD")).upper()
    exit_signal = market_signal in {"SELL", "SHORT"}
    if (
        market_expected_net_edge_bps is None
        and market_expected_edge_bps is not None
        and market_expected_cost_bps is not None
    ):
        market_expected_net_edge_bps = float(market_expected_edge_bps) - float(market_expected_cost_bps)
    alpha_cfg_raw = (
        ((rules.raw.get("strategy") or {}).get("alpha_score") or {})
        if isinstance(rules.raw, Mapping)
        else {}
    )
    spread_guard = evaluate_spread_guard(
        rules=rules,
        spread_bps=spread_bps,
        mid_price=mid_price,
        atr_pct=atr_pct,
        dv_zscore=dv_zscore,
        alpha_cfg_raw=alpha_cfg_raw,
    )
    spread_limit_bps = float(spread_guard.effective_limit_bps)

    effective_target_pct: float | None
    plan_target_for_execution = (
        trade_plan_execution_target_pct if trade_plan_execution_target_pct is not None else trade_plan_target_pct
    )
    if plan_target_for_execution is None:
        effective_target_pct = market_signal_target_pct
    elif market_signal_target_pct is None:
        effective_target_pct = plan_target_for_execution
    else:
        effective_target_pct = min(float(plan_target_for_execution), float(market_signal_target_pct))
    if effective_target_pct is not None and runtime_max_position_pct is not None:
        effective_target_pct = min(float(effective_target_pct), float(runtime_max_position_pct))

    gates: dict[str, Any] = {
        "symbol": symbol,
        "pause_state": pause_state,
        "reconciliation_status": recon_status,
        "rate_limit_alert": rate_limit_alert,
        "daily_loss_pct": daily_loss_pct,
        "daily_trades_count": int(daily_trades_count) if daily_trades_count is not None else None,
        "max_daily_loss_pct": rules.risk.max_daily_loss_pct,
        "spread_bps": spread_bps,
        "max_spread_bps_entry": rules.cost_guard.max_spread_bps_entry,
        "spread_limit_bps_effective": spread_limit_bps,
        "spread_guard": {
            "enabled": bool(spread_guard.enabled),
            "liq_score": float(spread_guard.liq_score),
            "atr_component_bps": float(spread_guard.atr_component_bps),
            "liq_component_bps": float(spread_guard.liq_component_bps),
            "min_limit_bps": float(spread_guard.min_limit_bps),
            "max_limit_bps": float(spread_guard.max_limit_bps),
            "spread_pct": float(spread_guard.spread_pct),
            "atr_pct": float(spread_guard.atr_pct),
            "dv_zscore": float(spread_guard.dv_zscore),
        },
        "rules_hash": runtime_rules_hash,
        "universe_id": runtime_universe_id,
        "universe_mode": runtime_universe_mode,
        "trade_plan_target_pct": trade_plan_target_pct,
        "trade_plan_execution_target_pct": trade_plan_execution_target_pct,
        "trade_plan_buy_allowed": trade_plan_buy_allowed,
        "trade_plan_sell_allowed": trade_plan_sell_allowed,
        "trade_plan_activation_decision": trade_plan_activation_decision,
        "trade_plan_activation_decision_effective": trade_plan_activation_decision_effective,
        "trade_plan_hold_mode": trade_plan_hold_mode,
        "trade_plan_cap_promoted": trade_plan_cap_promoted,
        "trade_plan_inter_slot_realtime_mode": bool(trade_plan_inter_slot_realtime_mode),
        "trade_plan_final_no_trade": bool(trade_plan_final_no_trade),
        "trade_plan_runtime_entry_allowed": trade_plan_runtime_entry_allowed,
        "trade_plan_runtime_promotion_enabled": trade_plan_runtime_promotion_enabled,
        "trade_plan_runtime_execution_authority": trade_plan_runtime_execution_authority,
        "trade_plan_runtime_entry_objective": trade_plan_runtime_entry_objective,
        "trade_plan_runtime_exploration_enabled": trade_plan_runtime_exploration_enabled,
        "trade_plan_runtime_profit_floor_bps": trade_plan_runtime_profit_floor_bps,
        "trade_plan_runtime_profit_required_margin_bps": trade_plan_runtime_profit_required_margin_bps,
        "trade_plan_runtime_promotion_active": trade_plan_runtime_promotion_active,
        "trade_plan_runtime_learning_mode": trade_plan_runtime_learning_mode,
        "trade_plan_runtime_min_predicted_after_cost_bps": trade_plan_runtime_min_predicted_after_cost_bps,
        "trade_plan_runtime_alpha_bypass_on_exploration": trade_plan_runtime_alpha_bypass_on_exploration,
        "trade_plan_cap_runtime": trade_plan_cap_runtime if isinstance(trade_plan_cap_runtime, Mapping) else None,
        "signal_target_pct": market_signal_target_pct,
        "market_expected_edge_bps": market_expected_edge_bps,
        "market_expected_cost_bps": market_expected_cost_bps,
        "market_expected_net_edge_bps": market_expected_net_edge_bps,
        "market_min_edge_required_bps": market_min_edge_required_bps,
        "market_predicted_after_cost_bps": market_predicted_after_cost_bps,
        "market_after_cost_required_bps": market_after_cost_required_bps,
        "market_after_cost_uncertainty_bps": market_after_cost_uncertainty_bps,
        "market_regime": market_regime,
        "effective_target_pct": effective_target_pct,
        "current_position_pct": current_position_pct,
        "cash_krw": cash_krw,
        "runtime_buy_enabled": bool(runtime_buy_enabled),
        "runtime_max_position_pct": runtime_max_position_pct,
        "runtime_actionable_floor_pct": runtime_actionable_floor_pct,
        "runtime_reason_codes": [c.value for c in runtime_reason_codes],
        "market_reason_codes": [c.value for c in market_reason_codes],
        "regime_reason_codes": [c.value for c in regime_reason_codes],
        "risk_reason_codes": [c.value for c in risk_reason_codes],
        "ops_reason_codes": [c.value for c in ops_reason_codes],
        "exit_signal": bool(exit_signal),
    }

    # External agent opinions (optional but supported).
    regime_allowed = True
    if regime is not None and "trade_allowed" in regime:
        regime_allowed = bool(regime.get("trade_allowed"))
    gates["regime_trade_allowed"] = regime_allowed

    risk_veto = False
    if risk is not None and "veto" in risk:
        risk_veto = bool(risk.get("veto"))
    gates["risk_veto"] = risk_veto

    ops_veto = False
    if ops is not None and "veto" in ops:
        ops_veto = bool(ops.get("veto"))
    gates["ops_veto"] = ops_veto

    # Hard gates (priority order).
    reasons: list[ReasonCode] = []
    action: str

    if pause_state:
        action = "PAUSE"
        reasons.append(ReasonCode.OP_PAUSE_TRIGGERED)
    elif recon_status == "FAIL":
        action = "PAUSE"
        reasons.append(ReasonCode.RG_RECON_FAIL)
    elif rate_limit_alert:
        action = "PAUSE"
        reasons.append(ReasonCode.RG_RATE_LIMIT_STORM)
    elif daily_loss_pct >= rules.risk.max_daily_loss_pct:
        action = "PAUSE"
        reasons.append(ReasonCode.RG_DAILY_LOSS_LIMIT_HIT)
    elif ops_veto:
        action = "PAUSE"
        reasons.extend(_non_pass_reasons(ops_reason_codes) or [ReasonCode.RG_DATA_BAD])
    elif not regime_allowed and not exit_signal:
        action = "HOLD"
        reasons.extend(_non_pass_reasons(regime_reason_codes) or [ReasonCode.RG_REGIME_BLOCKED])
    elif risk_veto and not exit_signal:
        action = "HOLD"
        reasons.extend(_non_pass_reasons(risk_reason_codes) or [ReasonCode.RG_RISK_VETO])
    elif spread_bps > float(spread_limit_bps) and not exit_signal:
        action = "HOLD"
        reasons.append(ReasonCode.RG_SPREAD_TOO_WIDE)
    else:
        # Soft decision: follow market if present, else HOLD.
        market_alpha = None
        market_edge_gate_blocked = False
        market_cost_gate_blocked = False
        try:
            market_alpha = float((market or {}).get("alpha"))
        except Exception:
            market_alpha = None
        if market_signal in {"BUY", "LONG"}:
            action = "BUY"
        elif market_signal in {"SELL", "SHORT"}:
            action = "SELL"
        else:
            action = "HOLD"
        if action == "HOLD":
            reasons = _non_pass_reasons(market_reason_codes) or [ReasonCode.RG_PASS]
        else:
            reasons = [ReasonCode.RG_PASS]

        if action == "BUY" and trade_plan_buy_allowed is False:
            action = "HOLD"
            reasons = [ReasonCode.RG_TRADE_PLAN_FLAT]
        elif action == "BUY" and not bool(runtime_buy_enabled):
            action = "HOLD"
            reasons = _non_pass_reasons(runtime_reason_codes) or [ReasonCode.RG_EXPOSURE_LIMIT]

        plan_decision = str(trade_plan_activation_decision_effective or trade_plan_activation_decision or "").upper()
        if plan_decision == "HOLD" and action == "BUY":
            action = "HOLD"
            reasons = [ReasonCode.RG_TRADE_PLAN_FLAT]

        # Trade Plan gating (position sizing guard): if plan says "flat" or already at target, do not buy.
        if action == "BUY" and effective_target_pct is not None:
            if float(effective_target_pct) <= 0:
                action = "HOLD"
                reasons = [ReasonCode.RG_TRADE_PLAN_FLAT]
            elif current_position_pct is not None and float(current_position_pct) >= float(effective_target_pct) - 0.25:
                action = "HOLD"
                reasons = [ReasonCode.RG_TRADE_PLAN_TARGET_REACHED]

        # Paper/live cash guard: if we cannot satisfy min order notional, hold.
        if action == "BUY" and cash_krw is not None and float(cash_krw) < float(rules.execution.min_order_krw):
            action = "HOLD"
            reasons = [ReasonCode.RG_MIN_ORDER_NOT_MET]

        if (
            action == "BUY"
            and market_expected_cost_bps is not None
            and float(market_expected_cost_bps) > float(rules.cost_guard.max_total_cost_bps)
        ):
            action = "HOLD"
            reasons = [ReasonCode.RG_SLIPPAGE_EST_TOO_HIGH]
            market_cost_gate_blocked = True
            market_reason_codes.append(ReasonCode.RG_SLIPPAGE_EST_TOO_HIGH)

        net_edge_required = (
            float(market_min_edge_required_bps)
            if (market_min_edge_required_bps is not None and float(market_min_edge_required_bps) > 0.0)
            else float(rules.cost_guard.min_expected_edge_bps)
        )

        if (
            action == "BUY"
            and market_expected_net_edge_bps is not None
            and float(market_expected_net_edge_bps) < float(net_edge_required)
        ):
            action = "HOLD"
            reasons = [ReasonCode.RG_EDGE_TOO_LOW]
            market_edge_gate_blocked = True
            market_reason_codes.append(ReasonCode.RG_EDGE_TOO_LOW)

        # Always-On Micro Mode:
        # - When governance plan is HOLD, allow a tiny pilot BUY only under strict conditions.
        # - Purpose: keep market feedback loop alive without meaningful risk.
        micro_cfg = (rules.raw.get("governance") or {}) if isinstance(rules.raw, Mapping) else {}
        micro_cfg = (micro_cfg.get("micro_mode") or {}) if isinstance(micro_cfg, Mapping) else {}
        is_live_mode = str(((rules.raw.get("universe") or {}).get("mode") or "paper")).strip().lower() == "live"
        micro_enabled = bool(micro_cfg.get("enabled", False))
        if is_live_mode and not bool(micro_cfg.get("enabled_live", False)):
            micro_enabled = False
        micro_entry_mode = str(micro_cfg.get("entry_mode") or "adaptive").strip().lower()
        if micro_entry_mode not in {"adaptive", "market-led", "plan-led"}:
            micro_entry_mode = "adaptive"
        micro_max_position_pct = float(micro_cfg.get("max_position_pct") or 3.0)
        micro_max_spread_bps = float(micro_cfg.get("max_spread_bps") or 1.0)
        micro_min_alpha = float(micro_cfg.get("min_alpha") or 0.7)
        micro_realtime_min_alpha_delta = float(micro_cfg.get("realtime_min_alpha_delta") or -0.08)
        micro_realtime_max_spread_mult = float(micro_cfg.get("realtime_max_spread_mult") or 1.15)
        micro_max_daily_loss_pct = float(micro_cfg.get("max_daily_loss_pct") or 0.5)
        micro_max_trades_per_day = int(micro_cfg.get("max_trades_per_day") or 5)
        micro_plan_hold_only = bool(micro_cfg.get("plan_hold_only", True))
        micro_allow_plan_led_entry = bool(micro_cfg.get("allow_plan_led_entry", False))
        micro_allow_runtime_hold_entry = bool(micro_cfg.get("allow_runtime_hold_entry", True))
        micro_require_calibration_live = bool(micro_cfg.get("require_calibration_live", True))
        micro_require_market_long = bool(micro_cfg.get("require_market_long", True))
        micro_ignore_cooldown_in_plan_led = bool(micro_cfg.get("ignore_market_cooldown_in_plan_led", False))
        micro_ignore_edge_in_plan_led = bool(micro_cfg.get("ignore_market_edge_in_plan_led", True))
        micro_live_min_predicted_after_cost_bps = float(
            micro_cfg.get("live_min_predicted_after_cost_bps")
            if micro_cfg.get("live_min_predicted_after_cost_bps") is not None
            else 0.0
        )
        micro_live_max_uncertainty_bps = float(
            micro_cfg.get("live_max_uncertainty_bps")
            if micro_cfg.get("live_max_uncertainty_bps") is not None
            else 8.0
        )
        micro_live_profit_floor_bps = float(
            micro_cfg.get("live_profit_floor_bps")
            if micro_cfg.get("live_profit_floor_bps") is not None
            else 1.0
        )
        micro_live_profit_required_margin_bps = float(
            micro_cfg.get("live_profit_required_margin_bps")
            if micro_cfg.get("live_profit_required_margin_bps") is not None
            else 0.5
        )
        micro_allow_live_exploration = bool(micro_cfg.get("allow_live_exploration", False))
        micro_alpha_margin = float(
            micro_cfg.get("alpha_margin")
            if micro_cfg.get("alpha_margin") is not None
            else (0.10 if is_live_mode else 0.0)
        )
        micro_edge_margin_bps = float(
            micro_cfg.get("edge_margin_bps")
            if micro_cfg.get("edge_margin_bps") is not None
            else (10.0 if is_live_mode else 0.0)
        )
        plan_gate_hard_block = _opt_bool(payload, "context.trade_plan.activation_gate.hard_plan_block")
        plan_gate_soft_block = _opt_bool(payload, "context.trade_plan.activation_gate.soft_plan_block")
        plan_gate_exec_block = _opt_bool(payload, "context.trade_plan.activation_gate.plan_execution_blocked")
        inter_slot_realtime_mode = bool(
            _opt_bool(payload, "context.trade_plan.activation_gate.inter_slot_realtime_mode")
        )
        plan_gate_passed = not bool(plan_gate_hard_block or plan_gate_soft_block or plan_gate_exec_block)

        gates["micro_mode_enabled"] = bool(micro_enabled)
        gates["micro_mode"] = {
            "entry_mode": str(micro_entry_mode),
            "max_position_pct": float(micro_max_position_pct),
            "max_spread_bps": float(micro_max_spread_bps),
            "min_alpha": float(micro_min_alpha),
            "max_daily_loss_pct": float(micro_max_daily_loss_pct),
            "max_trades_per_day": int(micro_max_trades_per_day),
            "plan_hold_only": bool(micro_plan_hold_only),
            "allow_plan_led_entry": bool(micro_allow_plan_led_entry),
            "allow_runtime_hold_entry": bool(micro_allow_runtime_hold_entry),
            "require_calibration_live": bool(micro_require_calibration_live),
            "require_market_long": bool(micro_require_market_long),
            "ignore_market_cooldown_in_plan_led": bool(micro_ignore_cooldown_in_plan_led),
            "ignore_market_edge_in_plan_led": bool(micro_ignore_edge_in_plan_led),
            "live_min_predicted_after_cost_bps": float(micro_live_min_predicted_after_cost_bps),
            "live_max_uncertainty_bps": float(micro_live_max_uncertainty_bps),
            "live_profit_floor_bps": float(micro_live_profit_floor_bps),
            "live_profit_required_margin_bps": float(micro_live_profit_required_margin_bps),
            "allow_live_exploration": bool(micro_allow_live_exploration),
            "alpha_margin": float(micro_alpha_margin),
            "edge_margin_bps": float(micro_edge_margin_bps),
            "realtime_min_alpha_delta": float(micro_realtime_min_alpha_delta),
            "realtime_max_spread_mult": float(micro_realtime_max_spread_mult),
            "inter_slot_realtime_mode": bool(inter_slot_realtime_mode),
        }

        plan_decision_effective = str(trade_plan_activation_decision_effective or "").upper()
        plan_decision_base = str(trade_plan_activation_decision or "").upper()
        plan_hold_mode_value = str(trade_plan_hold_mode or "").upper()
        # HOLD plan can appear either as explicit HOLD decision or as HOLD_* mode with PAPER decision_effective.
        plan_is_hold = bool(
            plan_decision_effective == "HOLD"
            or plan_decision_base == "HOLD"
            or plan_hold_mode_value.startswith("HOLD")
        )
        plan_allows_buy = bool(trade_plan_buy_allowed is not False)
        runtime_entry_allowed_flag = (
            bool(trade_plan_runtime_entry_allowed)
            if trade_plan_runtime_entry_allowed is not None
            else bool(inter_slot_realtime_mode)
        )
        runtime_promotion_enabled = (
            bool(trade_plan_runtime_promotion_enabled)
            if trade_plan_runtime_promotion_enabled is not None
            else bool(runtime_entry_allowed_flag and not plan_allows_buy)
        )
        runtime_entry_objective = str(
            trade_plan_runtime_entry_objective
            or ("profit-first" if is_live_mode else "feedback-loop")
        ).strip().lower() or ("profit-first" if is_live_mode else "feedback-loop")
        runtime_exploration_enabled = (
            bool(trade_plan_runtime_exploration_enabled)
            if trade_plan_runtime_exploration_enabled is not None
            else (bool(micro_allow_live_exploration) if is_live_mode else True)
        )
        runtime_profit_floor_bps = float(
            trade_plan_runtime_profit_floor_bps
            if trade_plan_runtime_profit_floor_bps is not None
            else micro_live_profit_floor_bps
        )
        runtime_profit_required_margin_bps = float(
            trade_plan_runtime_profit_required_margin_bps
            if trade_plan_runtime_profit_required_margin_bps is not None
            else micro_live_profit_required_margin_bps
        )
        runtime_learning_mode = bool(trade_plan_runtime_learning_mode)
        runtime_min_predicted_after_cost_bps = float(
            trade_plan_runtime_min_predicted_after_cost_bps
            if trade_plan_runtime_min_predicted_after_cost_bps is not None
            else micro_live_min_predicted_after_cost_bps
        )
        runtime_alpha_bypass_on_exploration = bool(trade_plan_runtime_alpha_bypass_on_exploration)
        runtime_hold_entry_allowed = bool(
            is_live_mode
            and bool(micro_allow_runtime_hold_entry)
            and bool(runtime_entry_allowed_flag)
            and bool(plan_is_hold)
            and not bool(trade_plan_final_no_trade)
            and bool(plan_gate_passed)
            and bool(runtime_promotion_enabled)
        )
        plan_allows_micro_entry = bool(plan_allows_buy or runtime_hold_entry_allowed)
        market_long_ok = bool(market_signal in {"BUY", "LONG"})
        signal_target_ok = bool(market_signal_target_pct is not None and float(market_signal_target_pct) > 0.0)
        market_led_ok = bool(market_long_ok and signal_target_ok)
        plan_led_ok = bool(
            micro_allow_plan_led_entry
            and market_signal not in {"SELL", "SHORT"}
            and plan_target_for_execution is not None
            and float(plan_target_for_execution) > 0.0
            and plan_gate_passed
        )
        if micro_entry_mode == "market-led":
            trigger_ok = bool(market_led_ok)
            micro_entry_path = "market-led" if market_led_ok else "blocked"
        elif micro_entry_mode == "plan-led":
            trigger_ok = bool(plan_led_ok)
            micro_entry_path = "plan-led" if plan_led_ok else "blocked"
        else:
            trigger_ok = bool(market_led_ok or plan_led_ok)
            micro_entry_path = "market-led" if market_led_ok else ("plan-led" if plan_led_ok else "blocked")
        if micro_require_market_long:
            trigger_ok = bool(market_led_ok)
            micro_entry_path = "market-led" if market_led_ok else "blocked"
        live_calibration_ready = bool(market_predicted_after_cost_bps is not None)
        live_calibration_uncertainty_ok = bool(
            market_after_cost_uncertainty_bps is None
            or float(market_after_cost_uncertainty_bps) <= float(micro_live_max_uncertainty_bps)
        )
        live_runtime_edge_bps = (
            float(market_predicted_after_cost_bps)
            if market_predicted_after_cost_bps is not None
            else (
                float(market_expected_net_edge_bps)
                if market_expected_net_edge_bps is not None
                else None
            )
        )
        live_profit_threshold_source = "runtime_profit_floor"
        if micro_entry_path == "plan-led":
            live_profit_threshold_bps = float(runtime_profit_floor_bps) + float(
                runtime_profit_required_margin_bps
            )
        else:
            live_profit_threshold_source = "market_required_plus_margin"
            live_profit_threshold_bps = max(
                float(runtime_profit_floor_bps),
                float(
                    (
                        market_after_cost_required_bps
                        if market_after_cost_required_bps is not None
                        else net_edge_required
                    )
                ) + float(runtime_profit_required_margin_bps),
            )
        live_profit_edge_ok = bool(
            runtime_hold_entry_allowed
            and live_runtime_edge_bps is not None
            and float(live_runtime_edge_bps) >= float(live_profit_threshold_bps)
            and bool(live_calibration_uncertainty_ok)
        )
        live_exploration_edge_ok = bool(
            runtime_hold_entry_allowed
            and micro_entry_path == "plan-led"
            and (
                (
                    bool(live_calibration_ready)
                    and float(market_predicted_after_cost_bps or 0.0) >= float(runtime_min_predicted_after_cost_bps)
                    and bool(live_calibration_uncertainty_ok)
                )
                if bool(micro_require_calibration_live)
                else (
                    (
                        market_predicted_after_cost_bps is not None
                        and float(market_predicted_after_cost_bps) >= float(runtime_min_predicted_after_cost_bps)
                        and bool(live_calibration_uncertainty_ok)
                    )
                    or (
                        market_expected_net_edge_bps is not None
                        and float(market_expected_net_edge_bps) >= float(net_edge_required + micro_edge_margin_bps)
                    )
                )
            )
        )
        live_runtime_entry_edge_ok = bool(
            live_profit_edge_ok
            or (bool(runtime_exploration_enabled) and bool(live_exploration_edge_ok))
        )
        market_has_cooldown_block = any(c == ReasonCode.RG_COOLDOWN_ACTIVE for c in market_reason_codes)
        market_has_edge_block = any(c == ReasonCode.RG_EDGE_TOO_LOW for c in market_reason_codes)
        # In plan-led micro entry, optionally ignore market cooldown/edge blocks and rely on
        # alpha + spread + daily-loss + size gates for controlled paper exploration.
        cooldown_block_applied = bool(
            market_has_cooldown_block
            and not (micro_entry_path == "plan-led" and micro_ignore_cooldown_in_plan_led)
        )
        edge_block_applied = bool(
            market_has_edge_block
            and not (micro_entry_path == "plan-led" and micro_ignore_edge_in_plan_led)
        )
        market_reason_blocked = bool(
            cooldown_block_applied
            or edge_block_applied
            or (market_edge_gate_blocked and not bool(live_runtime_entry_edge_ok))
            or market_cost_gate_blocked
        )

        micro_candidate_context = (
            bool(micro_enabled)
            and action == "HOLD"
            and market_signal not in {"SELL", "SHORT"}
            and bool(plan_allows_micro_entry)
            and (plan_is_hold if micro_plan_hold_only else True)
        )
        micro_allowed_context = bool(micro_candidate_context and bool(trigger_ok))
        micro_block_reason: ReasonCode | None = None
        if micro_candidate_context and (not bool(trigger_ok)):
            micro_block_reason = ReasonCode.RG_MICRO_BLOCKED_POLICY
        if (
            micro_allowed_context
            and bool(is_live_mode)
            and bool(runtime_hold_entry_allowed)
            and not bool(live_runtime_entry_edge_ok)
        ):
            micro_block_reason = ReasonCode.RG_MICRO_BLOCKED_EDGE
        if micro_allowed_context and market_reason_blocked:
            if cooldown_block_applied:
                micro_block_reason = ReasonCode.RG_MICRO_BLOCKED_COOLDOWN
            elif edge_block_applied:
                micro_block_reason = ReasonCode.RG_MICRO_BLOCKED_EDGE
            elif bool(is_live_mode and runtime_hold_entry_allowed and not live_runtime_entry_edge_ok):
                micro_block_reason = ReasonCode.RG_MICRO_BLOCKED_EDGE

        # Dynamic micro thresholds for real-time responsiveness:
        # - inter-slot mode relaxes alpha/spread slightly (paper only exploration)
        # - tight spread relaxes alpha, near-limit spread tightens alpha
        # - near daily loss limit tightens alpha
        dynamic_min_alpha = float(micro_min_alpha)
        dynamic_max_spread_bps = float(micro_max_spread_bps)
        if inter_slot_realtime_mode:
            dynamic_min_alpha = float(dynamic_min_alpha + micro_realtime_min_alpha_delta)
            dynamic_max_spread_bps = float(dynamic_max_spread_bps * micro_realtime_max_spread_mult)
        if float(spread_bps) <= float(micro_max_spread_bps) * 0.5:
            dynamic_min_alpha -= 0.02
        if float(spread_bps) >= float(dynamic_max_spread_bps) * 0.9:
            dynamic_min_alpha += 0.03
        if float(daily_loss_pct) >= float(micro_max_daily_loss_pct) * 0.8:
            dynamic_min_alpha += 0.05
        dynamic_min_alpha = _clamp(dynamic_min_alpha, 0.55, 0.98)
        dynamic_max_spread_bps = _clamp(
            dynamic_max_spread_bps,
            min(1.0, float(rules.cost_guard.max_spread_bps_entry)),
            float(rules.cost_guard.max_spread_bps_entry),
        )
        micro_alpha_gate_bypassed_by_calibration = bool(
            is_live_mode
            and micro_entry_path == "plan-led"
            and (
                bool(live_profit_edge_ok)
                or (
                    bool(runtime_learning_mode)
                    and bool(runtime_alpha_bypass_on_exploration)
                    and bool(live_exploration_edge_ok)
                )
            )
        )
        micro_base_alpha_pass = bool(
            micro_alpha_gate_bypassed_by_calibration
            or (market_alpha is not None and float(market_alpha) >= float(dynamic_min_alpha))
        )
        micro_live_alpha_pass = bool(
            (not bool(is_live_mode))
            or micro_alpha_gate_bypassed_by_calibration
            or (market_alpha is not None and float(market_alpha) >= float(dynamic_min_alpha + micro_alpha_margin))
        )

        micro_pass = (
            micro_allowed_context
            and not bool(market_reason_blocked)
            and bool(micro_base_alpha_pass)
            and bool(micro_live_alpha_pass)
            and float(spread_bps) <= float(dynamic_max_spread_bps)
            and float(daily_loss_pct) <= float(micro_max_daily_loss_pct)
            and (
                (not bool(is_live_mode))
                or (
                    bool(live_runtime_entry_edge_ok)
                    or (
                        micro_entry_path == "market-led"
                        and market_expected_net_edge_bps is not None
                        and float(market_expected_net_edge_bps) >= float(net_edge_required + micro_edge_margin_bps)
                    )
                )
            )
            and (
                daily_trades_count is None
                or int(float(daily_trades_count)) < int(micro_max_trades_per_day)
            )
            and (current_position_pct is None or float(current_position_pct) < float(micro_max_position_pct) - 0.1)
        )
        gates["micro_mode_passed"] = bool(micro_pass)
        gates["micro_mode_entry_mode"] = str(micro_entry_mode)
        gates["micro_mode_entry_path"] = str(micro_entry_path)
        gates["micro_mode_market_reason_blocked"] = bool(market_reason_blocked)
        gates["micro_mode_edge_block_applied"] = bool(edge_block_applied)
        gates["market_edge_gate_blocked"] = bool(market_edge_gate_blocked)
        gates["market_cost_gate_blocked"] = bool(market_cost_gate_blocked)
        gates["micro_mode_plan_gate_passed"] = bool(plan_gate_passed)
        gates["micro_mode_plan_allows_buy"] = bool(plan_allows_buy)
        gates["micro_mode_dynamic_min_alpha"] = float(dynamic_min_alpha)
        gates["micro_mode_dynamic_max_spread_bps"] = float(dynamic_max_spread_bps)
        gates["micro_mode_alpha_gate_passed"] = bool(micro_base_alpha_pass)
        gates["micro_mode_live_alpha_gate_passed"] = bool(micro_live_alpha_pass)
        gates["micro_mode_alpha_gate_bypassed_by_calibration"] = bool(
            micro_alpha_gate_bypassed_by_calibration
        )
        gates["micro_mode_runtime_hold_entry_allowed"] = bool(runtime_hold_entry_allowed)
        gates["micro_mode_runtime_entry_allowed_flag"] = bool(runtime_entry_allowed_flag)
        gates["micro_mode_runtime_promotion_enabled"] = bool(runtime_promotion_enabled)
        gates["micro_mode_runtime_execution_authority"] = str(trade_plan_runtime_execution_authority or "")
        gates["micro_mode_runtime_entry_objective"] = str(runtime_entry_objective)
        gates["micro_mode_runtime_exploration_enabled"] = bool(runtime_exploration_enabled)
        gates["micro_mode_runtime_learning_mode"] = bool(runtime_learning_mode)
        gates["micro_mode_runtime_min_predicted_after_cost_bps"] = float(runtime_min_predicted_after_cost_bps)
        gates["micro_mode_runtime_alpha_bypass_on_exploration"] = bool(runtime_alpha_bypass_on_exploration)
        gates["micro_mode_runtime_profit_floor_bps"] = float(runtime_profit_floor_bps)
        gates["micro_mode_runtime_profit_required_margin_bps"] = float(runtime_profit_required_margin_bps)
        gates["micro_mode_runtime_promotion_active"] = bool(trade_plan_runtime_promotion_active)
        gates["micro_mode_live_runtime_edge_bps"] = live_runtime_edge_bps
        gates["micro_mode_live_profit_threshold_source"] = str(live_profit_threshold_source)
        gates["micro_mode_live_profit_threshold_bps"] = float(live_profit_threshold_bps)
        gates["micro_mode_live_profit_edge_ok"] = bool(live_profit_edge_ok)
        gates["micro_mode_live_exploration_edge_ok"] = bool(live_exploration_edge_ok)
        gates["micro_mode_live_runtime_entry_edge_ok"] = bool(live_runtime_entry_edge_ok)
        gates["micro_mode_live_calibration_ready"] = bool(live_calibration_ready)

        if micro_pass:
            action = "BUY"
            base_target = float(market_signal_target_pct or 0.0)
            if plan_led_ok and plan_target_for_execution is not None:
                base_target = max(float(base_target), float(plan_target_for_execution))
            micro_target = min(float(micro_max_position_pct), float(base_target))
            if bool(is_live_mode) and runtime_actionable_floor_pct is not None and float(runtime_actionable_floor_pct) > 0.0:
                micro_target = max(float(micro_target), float(runtime_actionable_floor_pct))
            if runtime_max_position_pct is not None:
                micro_target = min(float(micro_target), float(runtime_max_position_pct))
            effective_target_pct = float(max(0.0, micro_target))
            gates["effective_target_pct"] = float(effective_target_pct)
            reasons = [ReasonCode.RG_CAP_PROMOTED]
        elif micro_candidate_context:
            if micro_block_reason is None:
                micro_block_reason = ReasonCode.RG_MICRO_BLOCKED_POLICY
            gates["micro_mode_block_reason_code"] = str(micro_block_reason.value)
            reasons = [micro_block_reason]

    selected_reason_codes = [c.value for c in validate_reason_codes(reasons[:3], max_items=3)]

    score: float | None = None
    confidence: float | None = None
    expected_cost_bps: float | None = None
    expected_rr: float | None = None

    if action in {"BUY", "SELL"}:
        try:
            confidence = float((market or {}).get("confidence")) if market else None
        except (TypeError, ValueError):
            confidence = None
        score = confidence
        if market_expected_cost_bps is not None:
            expected_cost_bps = float(market_expected_cost_bps)
        else:
            expected_cost_bps = spread_bps + rules.cost_guard.entry_cost_buffer_bps
        if market_expected_net_edge_bps is not None and expected_cost_bps and expected_cost_bps > 0:
            expected_rr = float(max(-9.99, min(9.99, float(market_expected_net_edge_bps) / float(expected_cost_bps))))

    return SafeJudgeDecision(
        action=action,
        score=score,
        confidence=confidence,
        effective_target_pct=effective_target_pct,
        gates=gates,
        selected_reasons=selected_reason_codes,
        rejected_reasons=[],
        expected_cost_bps=expected_cost_bps,
        expected_rr=expected_rr,
    )
