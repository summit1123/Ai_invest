from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ai_invest.config.rules_loader import RulesConfig
from ai_invest.domain.reason_codes import ReasonCode


@dataclass(frozen=True)
class MarketOpinion:
    signal: str  # LONG / SELL / HOLD (v1 long-only, SELL = exit/close only)
    confidence: float
    target_position_pct: float
    reason_codes: list[str]
    reason: dict[str, Any]


def market_agent_opine(
    payload: Mapping[str, Any],
    *,
    rules: RulesConfig,
) -> MarketOpinion:
    """Simple deterministic v1 market agent.

    - long-only
    - cost-aware: spread gate biases to HOLD
    - uses rsi/volume z-score features when available
    """

    features = payload.get("features") or {}
    snapshot = payload.get("snapshot") or {}

    spread_bps = float(snapshot.get("spread_bps") or 0.0)
    if spread_bps > rules.cost_guard.max_spread_bps_entry:
        return MarketOpinion(
            signal="HOLD",
            confidence=0.55,
            target_position_pct=0.0,
            reason_codes=[ReasonCode.RG_SPREAD_TOO_WIDE.value],
            reason={"spread_bps": spread_bps, "max_spread_bps_entry": rules.cost_guard.max_spread_bps_entry},
        )

    rsi_14 = float(features.get("rsi_14") or 50.0)
    vol_z = float(features.get("vol_zscore") or 0.0)
    rsi_min = float(rules.raw.get("signal", {}).get("rsi_min", 50.0))
    vol_min = float(rules.raw.get("signal", {}).get("volume_zscore_min", 1.2))

    # Exit bias: if momentum weakens materially, recommend SELL (exit only; executor is long-only safe).
    if rsi_14 <= max(0.0, rsi_min - 5.0):
        conf = min(0.90, 0.55 + (rsi_min - rsi_14) / 100.0)
        return MarketOpinion(
            signal="SELL",
            confidence=float(conf),
            target_position_pct=0.0,
            reason_codes=[ReasonCode.RG_PASS.value],
            reason={"rsi_14": rsi_14, "rsi_min": rsi_min, "exit": True},
        )

    if rsi_14 >= rsi_min and vol_z >= vol_min:
        conf = min(0.95, 0.50 + (rsi_14 - rsi_min) / 100.0 + (vol_z - vol_min) / 10.0)
        return MarketOpinion(
            signal="LONG",
            confidence=float(conf),
            target_position_pct=min(10.0, rules.risk.max_position_pct_per_symbol),
            reason_codes=[ReasonCode.RG_PASS.value],
            reason={"rsi_14": rsi_14, "vol_zscore": vol_z},
        )

    return MarketOpinion(
        signal="HOLD",
        confidence=0.55,
        target_position_pct=0.0,
        reason_codes=[ReasonCode.RG_EDGE_TOO_LOW.value],
        reason={"rsi_14": rsi_14, "vol_zscore": vol_z},
    )
