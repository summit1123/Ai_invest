from __future__ import annotations

from enum import Enum
from typing import Iterable


class ReasonDomain(str, Enum):
    SAFE_JUDGE = "RG"
    EXECUTION = "EX"
    OUTCOME = "OC"
    OPS = "OP"


class ReasonCode(str, Enum):
    # RG_* (Safe Judge / gate)
    RG_RECON_FAIL = "RG_RECON_FAIL"
    RG_DAILY_LOSS_LIMIT_HIT = "RG_DAILY_LOSS_LIMIT_HIT"
    RG_DATA_BAD = "RG_DATA_BAD"
    RG_RATE_LIMIT_STORM = "RG_RATE_LIMIT_STORM"
    RG_WS_UNSTABLE = "RG_WS_UNSTABLE"
    RG_RISK_VETO = "RG_RISK_VETO"
    RG_REGIME_BLOCKED = "RG_REGIME_BLOCKED"
    RG_SPREAD_TOO_WIDE = "RG_SPREAD_TOO_WIDE"
    RG_SLIPPAGE_EST_TOO_HIGH = "RG_SLIPPAGE_EST_TOO_HIGH"
    RG_EDGE_TOO_LOW = "RG_EDGE_TOO_LOW"
    RG_EXPOSURE_LIMIT = "RG_EXPOSURE_LIMIT"
    RG_TRADE_PLAN_FLAT = "RG_TRADE_PLAN_FLAT"
    RG_TRADE_PLAN_TARGET_REACHED = "RG_TRADE_PLAN_TARGET_REACHED"
    RG_MIN_ORDER_NOT_MET = "RG_MIN_ORDER_NOT_MET"
    RG_COOLDOWN_ACTIVE = "RG_COOLDOWN_ACTIVE"
    RG_SIGNAL_CONFLICT = "RG_SIGNAL_CONFLICT"
    RG_MICRO_BLOCKED_COOLDOWN = "RG_MICRO_BLOCKED_COOLDOWN"
    RG_MICRO_BLOCKED_EDGE = "RG_MICRO_BLOCKED_EDGE"
    RG_MICRO_BLOCKED_POLICY = "RG_MICRO_BLOCKED_POLICY"
    RG_CAP_PENDING = "RG_CAP_PENDING"
    RG_CAP_PROMOTED = "RG_CAP_PROMOTED"
    RG_CAP_BLOCKED = "RG_CAP_BLOCKED"
    RG_PASS = "RG_PASS"

    # EX_* (Execution)
    EX_ORDER_SUBMIT_FAIL = "EX_ORDER_SUBMIT_FAIL"
    EX_ORDER_REJECTED = "EX_ORDER_REJECTED"
    EX_ACK_TIMEOUT = "EX_ACK_TIMEOUT"
    EX_PARTIAL_FILL_TIMEOUT = "EX_PARTIAL_FILL_TIMEOUT"
    EX_CANCEL_FAILED = "EX_CANCEL_FAILED"
    EX_REPRICE_LIMIT_REACHED = "EX_REPRICE_LIMIT_REACHED"
    EX_TICK_SIZE_INVALID = "EX_TICK_SIZE_INVALID"
    EX_INSUFFICIENT_BALANCE = "EX_INSUFFICIENT_BALANCE"
    EX_INVALID_STATE_TRANSITION = "EX_INVALID_STATE_TRANSITION"

    # OC_* (Outcome review)
    OC_FALSE_BREAKOUT = "OC_FALSE_BREAKOUT"
    OC_REGIME_MISCLASSIFIED = "OC_REGIME_MISCLASSIFIED"
    OC_COST_UNDERESTIMATED = "OC_COST_UNDERESTIMATED"
    OC_STOP_TOO_TIGHT = "OC_STOP_TOO_TIGHT"
    OC_STOP_TOO_LOOSE = "OC_STOP_TOO_LOOSE"
    OC_LATE_ENTRY = "OC_LATE_ENTRY"
    OC_EARLY_EXIT = "OC_EARLY_EXIT"
    OC_LIQUIDITY_DROPOUT = "OC_LIQUIDITY_DROPOUT"
    OC_NEWS_SHOCK = "OC_NEWS_SHOCK"
    OC_SIGNAL_OVERFIT = "OC_SIGNAL_OVERFIT"
    OC_EXECUTION_LATENCY = "OC_EXECUTION_LATENCY"
    OC_RULE_DRIFT = "OC_RULE_DRIFT"

    # OP_* (Ops lifecycle)
    OP_PAUSE_TRIGGERED = "OP_PAUSE_TRIGGERED"
    OP_RESUME_COMPLETED = "OP_RESUME_COMPLETED"
    OP_RESTART_RECOVERY = "OP_RESTART_RECOVERY"
    OP_MANUAL_REVIEW_REQUIRED = "OP_MANUAL_REVIEW_REQUIRED"


def _normalize_reason_code_value(value: ReasonCode | str) -> str:
    if isinstance(value, ReasonCode):
        return value.value
    return str(value)


def domain_of(code: ReasonCode | str) -> ReasonDomain:
    prefix = _normalize_reason_code_value(code).split("_", 1)[0]
    try:
        return ReasonDomain(prefix)
    except ValueError as exc:
        raise ValueError(f"Unknown reason code domain prefix: {prefix}") from exc


def parse_reason_code(value: ReasonCode | str) -> ReasonCode:
    if isinstance(value, ReasonCode):
        return value
    normalized = _normalize_reason_code_value(value)
    try:
        return ReasonCode(normalized)
    except ValueError as exc:
        raise ValueError(f"Unknown reason code: {normalized}") from exc


def validate_reason_codes(
    values: Iterable[str | ReasonCode],
    *,
    max_items: int = 3,
    allowed_domains: set[ReasonDomain] | None = None,
) -> list[ReasonCode]:
    parsed = [parse_reason_code(v) for v in values]
    if len(parsed) > max_items:
        raise ValueError(f"Too many reason codes: {len(parsed)} > {max_items}")
    if allowed_domains:
        for code in parsed:
            if domain_of(code) not in allowed_domains:
                raise ValueError(f"Domain not allowed for code {code}")
    return parsed
