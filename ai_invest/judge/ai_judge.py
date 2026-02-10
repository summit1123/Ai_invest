from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ai_invest.config.rules_loader import RulesConfig
from ai_invest.domain.reason_codes import ReasonCode, validate_reason_codes


@dataclass(frozen=True)
class AiJudgeDecision:
    action: str
    score: float | None
    confidence: float | None
    selected_reasons: list[str]
    rejected_reasons: list[str]
    meta: dict[str, Any]


def ai_judge_shadow_decide(
    payload: Mapping[str, Any],
    *,
    rules: RulesConfig,
    market: Mapping[str, Any] | None = None,
    regime: Mapping[str, Any] | None = None,
    risk: Mapping[str, Any] | None = None,
    ops: Mapping[str, Any] | None = None,
) -> AiJudgeDecision:
    """Shadow AI Judge (v1): deterministic baseline.

    In v1, we don't call external LLMs. We keep a cheap deterministic policy so
    SAFE vs AI storage/compare plumbing can be built without adding variance.
    """

    _ = payload, rules, market, regime, risk, ops

    # Baseline: always HOLD unless market strongly indicates LONG.
    action = "HOLD"
    conf = None
    if market and str(market.get("signal", "")).upper() in {"LONG", "BUY"}:
        action = "BUY"
        try:
            conf = float(market.get("confidence"))
        except (TypeError, ValueError):
            conf = None

    reasons = validate_reason_codes([ReasonCode.RG_PASS], max_items=3)
    return AiJudgeDecision(
        action=action,
        score=conf,
        confidence=conf,
        selected_reasons=[r.value for r in reasons],
        rejected_reasons=[],
        meta={"shadow_policy": "baseline_v1"},
    )

