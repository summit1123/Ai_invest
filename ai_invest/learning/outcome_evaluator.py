from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from ai_invest.domain.reason_codes import ReasonCode


@dataclass(frozen=True)
class OutcomeEvaluation:
    outcome_label: str  # WIN / LOSS / FLAT / MISS
    error_type: str | None  # OC_* only
    root_cause: str | None
    fix_hypothesis: str | None
    meta: Mapping[str, Any]


def classify_outcome_label(*, realized_pnl_krw: float, flat_tolerance_krw: float = 1.0) -> str:
    if realized_pnl_krw > flat_tolerance_krw:
        return "WIN"
    if realized_pnl_krw < -flat_tolerance_krw:
        return "LOSS"
    return "FLAT"


def evaluate_closed_trade(
    *,
    qty: float,
    avg_entry_price: float,
    avg_exit_price: float,
    realized_pnl_krw: float,
    fees_total_krw: float,
    ts_open: datetime | None = None,
    ts_close: datetime | None = None,
    pnl_bps: float | None = None,
    exit_reason: str | None = None,
    min_hold_seconds: int | None = None,
    flat_tolerance_krw: float = 1.0,
) -> OutcomeEvaluation:
    """Outcome Evaluator (v1): deterministic, low-assumption.

    - Always emits outcome_label.
    - Emits error_type only when the pattern is unambiguous.
    """

    outcome_label = classify_outcome_label(realized_pnl_krw=realized_pnl_krw, flat_tolerance_krw=flat_tolerance_krw)
    gross_pnl = (avg_exit_price - avg_entry_price) * qty

    fee_bps = (float(fees_total_krw) / float(avg_entry_price * qty) * 10000.0) if (avg_entry_price > 0 and qty > 0) else 0.0
    gross_bps = (float(gross_pnl) / float(avg_entry_price * qty) * 10000.0) if (avg_entry_price > 0 and qty > 0) else 0.0
    hold_seconds = None
    if ts_open is not None and ts_close is not None:
        try:
            hold_seconds = max(0.0, (ts_close - ts_open).total_seconds())
        except Exception:
            hold_seconds = None
    hold_minutes = (float(hold_seconds) / 60.0) if hold_seconds is not None else None
    exit_reason_norm = str(exit_reason or "").strip().upper() or None
    min_hold = max(0, int(min_hold_seconds or 0))

    error_type: str | None = None
    root_cause: str | None = None
    fix_hypothesis: str | None = None

    # If gross was positive but net is negative, fees/costs likely dominated.
    if outcome_label == "LOSS" and gross_pnl > 0 and realized_pnl_krw < 0:
        error_type = ReasonCode.OC_COST_UNDERESTIMATED.value
        root_cause = "gross_pnl_positive_but_net_negative"
        fix_hypothesis = "Tighten cost guards (spread/slippage) or require higher edge before entry."
    elif (
        outcome_label == "LOSS"
        and exit_reason_norm in {"TRAIL", "MOMENTUM_BREAK"}
        and hold_seconds is not None
        and min_hold > 0
        and float(hold_seconds) <= float(min_hold) + 5.0
    ):
        error_type = ReasonCode.OC_EARLY_EXIT.value
        root_cause = f"exit_{str(exit_reason_norm).lower()}_before_min_hold"
        fix_hypothesis = "Honor min-hold on non-protective exits and arm trailing only after net edge is secured."
    elif (
        outcome_label == "LOSS"
        and exit_reason_norm == "STOP"
        and hold_minutes is not None
        and float(hold_minutes) <= 15.0
        and abs(float(gross_bps)) <= max(12.0, float(fee_bps) + 4.0)
    ):
        error_type = ReasonCode.OC_STOP_TOO_TIGHT.value
        root_cause = "stop_triggered_near_noise_band"
        fix_hypothesis = "Widen initial stop or add volatility/microstructure filter before entry."
    elif (
        outcome_label == "LOSS"
        and hold_minutes is not None
        and float(hold_minutes) <= 45.0
        and float(gross_bps) <= -max(16.0, float(fee_bps))
    ):
        error_type = ReasonCode.OC_FALSE_BREAKOUT.value
        root_cause = "entry_followed_by_fast_reversal"
        fix_hypothesis = "Require stronger confirmation (trend/volume) before entry or reduce reversal exposure."

    return OutcomeEvaluation(
        outcome_label=outcome_label,
        error_type=error_type,
        root_cause=root_cause,
        fix_hypothesis=fix_hypothesis,
        meta={
            "gross_pnl_krw": gross_pnl,
            "fees_total_krw": fees_total_krw,
            "gross_bps": gross_bps,
            "net_bps": float(pnl_bps) if pnl_bps is not None else None,
            "fee_bps": fee_bps,
            "hold_seconds": hold_seconds,
            "exit_reason": exit_reason_norm,
        },
    )
