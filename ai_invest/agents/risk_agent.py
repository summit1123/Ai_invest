from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ai_invest.config.rules_loader import RulesConfig
from ai_invest.domain.reason_codes import ReasonCode


@dataclass(frozen=True)
class RiskOpinion:
    veto: bool
    max_position_pct: float
    max_loss_per_trade_pct: float
    reason_codes: list[str]
    reason: dict[str, Any]


def risk_agent_opine(payload: Mapping[str, Any], *, rules: RulesConfig) -> RiskOpinion:
    ctx = payload.get("context") or {}
    account = ctx.get("account") or {}
    position = ctx.get("position") if isinstance(ctx.get("position"), Mapping) else {}
    runtime_controls = ctx.get("runtime_controls") if isinstance(ctx.get("runtime_controls"), Mapping) else {}
    daily_loss_pct = float(account.get("daily_loss_pct") or 0.0)
    current_qty = float(position.get("current_qty") or 0.0)

    max_daily = float(rules.risk.max_daily_loss_pct)
    runtime_buy_enabled = bool(runtime_controls.get("buy_enabled", True))
    runtime_max_position_pct = float(runtime_controls.get("max_position_pct") or rules.risk.max_position_pct_per_symbol)
    runtime_reason_codes = [str(x).strip() for x in list(runtime_controls.get("reason_codes") or []) if str(x).strip()]
    if daily_loss_pct >= max_daily:
        return RiskOpinion(
            veto=True,
            max_position_pct=0.0,
            max_loss_per_trade_pct=rules.risk.max_risk_per_trade_pct,
            reason_codes=[ReasonCode.RG_DAILY_LOSS_LIMIT_HIT.value],
            reason={"daily_loss_pct": daily_loss_pct, "max_daily_loss_pct": max_daily},
        )

    if (not runtime_buy_enabled) and current_qty <= 0.0:
        code = runtime_reason_codes[0] if runtime_reason_codes else ReasonCode.RG_EXPOSURE_LIMIT.value
        return RiskOpinion(
            veto=True,
            max_position_pct=0.0,
            max_loss_per_trade_pct=rules.risk.max_risk_per_trade_pct,
            reason_codes=[code],
            reason={
                "daily_loss_pct": daily_loss_pct,
                "runtime_controls": dict(runtime_controls),
            },
        )

    # Soft veto near limit (fail-closed bias).
    if daily_loss_pct >= max_daily * 0.9:
        return RiskOpinion(
            veto=True,
            max_position_pct=0.0,
            max_loss_per_trade_pct=rules.risk.max_risk_per_trade_pct,
            reason_codes=[ReasonCode.RG_EXPOSURE_LIMIT.value],
            reason={"daily_loss_pct": daily_loss_pct, "max_daily_loss_pct": max_daily},
        )

    return RiskOpinion(
        veto=False,
        max_position_pct=min(rules.risk.max_position_pct_per_symbol, max(0.0, runtime_max_position_pct)),
        max_loss_per_trade_pct=rules.risk.max_risk_per_trade_pct,
        reason_codes=[ReasonCode.RG_PASS.value],
        reason={"daily_loss_pct": daily_loss_pct, "runtime_controls": dict(runtime_controls)},
    )
