from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ai_invest.config.rules_loader import RulesConfig
from ai_invest.domain.reason_codes import ReasonCode, validate_reason_codes


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
    recon_status = _as_str(payload, "ops.reconciliation_status").upper()
    rate_limit_alert = _as_bool(payload, "ops.rate_limit_alert")
    pause_state = _as_bool(payload, "ops.pause_state")
    daily_loss_pct = _as_float(payload, "context.account.daily_loss_pct")

    # Optional context fields (paper/live sizing, trade plan).
    trade_plan_target_pct = _opt_float(payload, "context.trade_plan.target_position_pct")
    trade_plan_execution_target_pct = _opt_float(payload, "context.trade_plan.execution_plan.final_numbers.target_position_pct")
    trade_plan_buy_allowed = _opt_bool(payload, "context.trade_plan.allowed_actions.buy")
    trade_plan_sell_allowed = _opt_bool(payload, "context.trade_plan.allowed_actions.sell")
    trade_plan_activation_decision = _opt_str(payload, "context.trade_plan.activation_gate.decision")
    trade_plan_activation_decision_effective = _opt_str(payload, "context.trade_plan.activation_gate.decision_effective")
    trade_plan_hold_mode = _opt_str(payload, "context.trade_plan.activation_gate.hold_mode")
    trade_plan_cap_promoted = _opt_bool(payload, "context.trade_plan.activation_gate.cap_promoted")
    try:
        trade_plan_cap_runtime = _dot_get(payload, "context.trade_plan.activation_gate.cap_runtime")
    except Exception:
        trade_plan_cap_runtime = None
    current_position_pct = _opt_float(payload, "context.position.current_position_pct")
    cash_krw = _opt_float(payload, "context.account.cash_krw")
    market_signal_target_pct = None
    if market is not None:
        try:
            market_signal_target_pct = float((market or {}).get("signal_target_pct"))
        except Exception:
            market_signal_target_pct = None

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

    gates: dict[str, Any] = {
        "symbol": symbol,
        "pause_state": pause_state,
        "reconciliation_status": recon_status,
        "rate_limit_alert": rate_limit_alert,
        "daily_loss_pct": daily_loss_pct,
        "max_daily_loss_pct": rules.risk.max_daily_loss_pct,
        "spread_bps": spread_bps,
        "max_spread_bps_entry": rules.cost_guard.max_spread_bps_entry,
        "trade_plan_target_pct": trade_plan_target_pct,
        "trade_plan_execution_target_pct": trade_plan_execution_target_pct,
        "trade_plan_buy_allowed": trade_plan_buy_allowed,
        "trade_plan_sell_allowed": trade_plan_sell_allowed,
        "trade_plan_activation_decision": trade_plan_activation_decision,
        "trade_plan_activation_decision_effective": trade_plan_activation_decision_effective,
        "trade_plan_hold_mode": trade_plan_hold_mode,
        "trade_plan_cap_promoted": trade_plan_cap_promoted,
        "trade_plan_cap_runtime": trade_plan_cap_runtime if isinstance(trade_plan_cap_runtime, Mapping) else None,
        "signal_target_pct": market_signal_target_pct,
        "effective_target_pct": effective_target_pct,
        "current_position_pct": current_position_pct,
        "cash_krw": cash_krw,
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
        # Generic fail-closed ops veto until ops agent provides finer codes.
        action = "PAUSE"
        reasons.append(ReasonCode.RG_DATA_BAD)
    elif not regime_allowed:
        action = "HOLD"
        reasons.append(ReasonCode.RG_REGIME_BLOCKED)
    elif risk_veto:
        action = "HOLD"
        reasons.append(ReasonCode.RG_RISK_VETO)
    elif spread_bps > rules.cost_guard.max_spread_bps_entry:
        action = "HOLD"
        reasons.append(ReasonCode.RG_SPREAD_TOO_WIDE)
    else:
        # Soft decision: follow market if present, else HOLD.
        market_signal = str((market or {}).get("signal", "HOLD")).upper()
        if market_signal in {"BUY", "LONG"}:
            action = "BUY"
        elif market_signal in {"SELL", "SHORT"}:
            action = "SELL"
        else:
            action = "HOLD"
        reasons.append(ReasonCode.RG_PASS)

        if action == "BUY" and trade_plan_buy_allowed is False:
            action = "HOLD"
            reasons = [ReasonCode.RG_TRADE_PLAN_FLAT]
        elif action == "SELL" and trade_plan_sell_allowed is False:
            action = "HOLD"
            reasons = [ReasonCode.RG_SIGNAL_CONFLICT]

        plan_decision = str(trade_plan_activation_decision_effective or trade_plan_activation_decision or "").upper()
        if plan_decision == "HOLD" and action in {"BUY", "SELL"}:
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

    selected_reason_codes = [c.value for c in validate_reason_codes(reasons, max_items=3)]

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
        expected_cost_bps = spread_bps + rules.cost_guard.entry_cost_buffer_bps

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
