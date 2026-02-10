from __future__ import annotations

from dataclasses import dataclass
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
    flat_tolerance_krw: float = 1.0,
) -> OutcomeEvaluation:
    """Outcome Evaluator (v1): deterministic, low-assumption.

    - Always emits outcome_label.
    - Emits error_type only when the pattern is unambiguous.
    """

    outcome_label = classify_outcome_label(realized_pnl_krw=realized_pnl_krw, flat_tolerance_krw=flat_tolerance_krw)
    gross_pnl = (avg_exit_price - avg_entry_price) * qty

    error_type: str | None = None
    root_cause: str | None = None
    fix_hypothesis: str | None = None

    # If gross was positive but net is negative, fees/costs likely dominated.
    if outcome_label == "LOSS" and gross_pnl > 0 and realized_pnl_krw < 0:
        error_type = ReasonCode.OC_COST_UNDERESTIMATED.value
        root_cause = "gross_pnl_positive_but_net_negative"
        fix_hypothesis = "Tighten cost guards (spread/slippage) or require higher edge before entry."

    return OutcomeEvaluation(
        outcome_label=outcome_label,
        error_type=error_type,
        root_cause=root_cause,
        fix_hypothesis=fix_hypothesis,
        meta={"gross_pnl_krw": gross_pnl, "fees_total_krw": fees_total_krw},
    )

