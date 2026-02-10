from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ai_invest.config.rules_loader import RulesConfig
from ai_invest.domain.reason_codes import ReasonCode


@dataclass(frozen=True)
class RegimeOpinion:
    regime: str  # TREND / HIGH_VOL / DATA_UNSTABLE
    trade_allowed: bool
    reason_codes: list[str]
    reason: dict[str, Any]


def regime_agent_opine(payload: Mapping[str, Any], *, rules: RulesConfig) -> RegimeOpinion:
    features = payload.get("features") or {}

    missing_rate = float(features.get("missing_rate_1m") or 0.0)
    if missing_rate > 0.05:
        return RegimeOpinion(
            regime="DATA_UNSTABLE",
            trade_allowed=False,
            reason_codes=[ReasonCode.RG_DATA_BAD.value],
            reason={"missing_rate_1m": missing_rate},
        )

    atr_pct = float(features.get("atr_pct") or 0.0)
    block_atr = float(rules.raw.get("regime", {}).get("volatility_block_atr_pct", 2.5))
    if atr_pct >= block_atr:
        return RegimeOpinion(
            regime="HIGH_VOL",
            trade_allowed=False,
            reason_codes=[ReasonCode.RG_REGIME_BLOCKED.value],
            reason={"atr_pct": atr_pct, "volatility_block_atr_pct": block_atr},
        )

    allowed = set(rules.raw.get("regime", {}).get("trade_allowed", ["TREND"]))
    trade_allowed = "TREND" in allowed
    return RegimeOpinion(
        regime="TREND",
        trade_allowed=trade_allowed,
        reason_codes=[ReasonCode.RG_PASS.value] if trade_allowed else [ReasonCode.RG_REGIME_BLOCKED.value],
        reason={"atr_pct": atr_pct},
    )

