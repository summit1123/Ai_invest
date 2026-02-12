from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Mapping

from ai_invest.config.rules_loader import RulesConfig
from ai_invest.domain.reason_codes import ReasonCode
from ai_invest.runtime.position_state import parse_position_state
from ai_invest.strategy.alpha_score import compute_alpha_score, load_alpha_score_config


@dataclass(frozen=True)
class MarketOpinion:
    signal: str  # LONG / SELL / HOLD (v1 long-only, SELL = exit/close only)
    confidence: float
    target_position_pct: float
    signal_target_pct: float
    alpha: float
    mom_s: float
    rev_s: float
    strength: float
    vol_scale: float
    strategy_tag: str | None
    entry_allowed: bool
    exit_reason: str | None
    reason_codes: list[str]
    reason: dict[str, Any]


def _now_utc(payload: Mapping[str, Any]) -> datetime:
    s = str(payload.get("timestamp_utc") or "").strip()
    if not s:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s else float(default)
    except Exception:
        return float(default)


def market_agent_opine(
    payload: Mapping[str, Any],
    *,
    rules: RulesConfig,
) -> MarketOpinion:
    """AlphaScore-based deterministic market agent.

    - long-only
    - pre-filters disallow new entries under hard risk/ops/cost conditions
    - SELL means full exit only
    """

    features = payload.get("features") or {}
    snapshot = payload.get("snapshot") or {}
    ops = payload.get("ops") or {}
    context = payload.get("context") or {}
    account = context.get("account") or {}
    pos_ctx = context.get("position") or {}
    pos_state_map = context.get("position_state") if isinstance(context.get("position_state"), Mapping) else {}
    now = _now_utc(payload)

    cfg = load_alpha_score_config(rules_raw=rules.raw)
    alpha = compute_alpha_score(features=features, cfg=cfg)

    spread_bps = _as_float(snapshot.get("spread_bps"), default=0.0)
    atr_pct = _as_float(features.get("atr_pct"), default=0.0)
    rsi_14 = _as_float(features.get("rsi_14"), default=50.0)
    ema20 = _as_float(features.get("ema20"), default=0.0)
    ema60 = _as_float(features.get("ema60"), default=0.0)
    last_price = _as_float(snapshot.get("last_price"), default=_as_float(snapshot.get("mid_price"), default=0.0))
    daily_loss_pct = _as_float(account.get("daily_loss_pct"), default=0.0)
    max_daily_loss = float(rules.risk.max_daily_loss_pct)
    current_qty = _as_float(pos_ctx.get("current_qty"), default=0.0)
    has_position = current_qty > 0.0
    state = parse_position_state(pos_state_map)
    cooldown_active = bool(state.cooldown_until is not None and now < state.cooldown_until)

    pre_block_reason: str | None = None
    pre_block_code: str | None = None
    if bool(ops.get("pause_state")):
        pre_block_reason = "PAUSE"
        pre_block_code = ReasonCode.OP_PAUSE_TRIGGERED.value
    elif str(ops.get("reconciliation_status") or "OK").upper() == "FAIL":
        pre_block_reason = "RECON_FAIL"
        pre_block_code = ReasonCode.RG_RECON_FAIL.value
    elif bool(ops.get("rate_limit_alert")):
        pre_block_reason = "RATE_LIMIT"
        pre_block_code = ReasonCode.RG_RATE_LIMIT_STORM.value
    elif atr_pct >= float(cfg.atr_block_pct):
        pre_block_reason = "HIGH_VOL"
        pre_block_code = ReasonCode.RG_REGIME_BLOCKED.value
    if spread_bps > rules.cost_guard.max_spread_bps_entry:
        pre_block_reason = "SPREAD_WIDE"
        pre_block_code = ReasonCode.RG_SPREAD_TOO_WIDE.value

    entry_loss_block = daily_loss_pct >= (max_daily_loss * float(cfg.daily_loss_entry_block_ratio))

    if pre_block_reason and not has_position:
        return MarketOpinion(
            signal="HOLD",
            confidence=0.55,
            target_position_pct=0.0,
            signal_target_pct=0.0,
            alpha=alpha.alpha,
            mom_s=alpha.mom_s,
            rev_s=alpha.rev_s,
            strength=alpha.strength,
            vol_scale=alpha.vol_scale,
            strategy_tag=state.strategy_tag,
            entry_allowed=False,
            exit_reason=None,
            reason_codes=[pre_block_code or ReasonCode.RG_DATA_BAD.value],
            reason={
                "block": pre_block_reason,
                "spread_bps": spread_bps,
                "max_spread_bps_entry": rules.cost_guard.max_spread_bps_entry,
                "atr_pct": atr_pct,
                "atr_block_pct": cfg.atr_block_pct,
            },
        )

    # Exit checks (full liquidation) when in-position.
    if has_position:
        strategy_tag = state.strategy_tag or alpha.strategy_tag_candidate
        entry_price = _as_float(state.entry_price, default=0.0)
        hwm_price = _as_float(state.hwm_price, default=max(last_price, entry_price))
        stop_pct = float(cfg.stop_atr_mult) * (atr_pct / 100.0)
        trail_pct = float(cfg.trail_atr_mult) * (atr_pct / 100.0)
        exit_reason: str | None = None
        if entry_price > 0 and stop_pct > 0 and last_price > 0 and last_price <= entry_price * (1.0 - stop_pct):
            exit_reason = "STOP"
        elif hwm_price > 0 and trail_pct > 0 and last_price > 0 and last_price <= hwm_price * (1.0 - trail_pct):
            exit_reason = "TRAIL"
        elif rsi_14 <= float(cfg.exit_rsi) or (ema20 > 0 and ema60 > 0 and ema20 < ema60):
            exit_reason = "MOMENTUM_BREAK"
        elif state.entry_ts is not None:
            hold_minutes = max(0.0, (now - state.entry_ts).total_seconds() / 60.0)
            max_hold = float(cfg.time_stop_rev_minutes) if str(strategy_tag).upper() == "REV" else float(cfg.time_stop_mom_minutes)
            if hold_minutes >= max_hold:
                exit_reason = "TIMESTOP"

        if exit_reason:
            conf = min(0.95, 0.70 + max(0.0, (float(cfg.entry_alpha) - float(alpha.alpha)) * 0.5))
            return MarketOpinion(
                signal="SELL",
                confidence=float(conf),
                target_position_pct=0.0,
                signal_target_pct=0.0,
                alpha=alpha.alpha,
                mom_s=alpha.mom_s,
                rev_s=alpha.rev_s,
                strength=alpha.strength,
                vol_scale=alpha.vol_scale,
                strategy_tag=strategy_tag,
                entry_allowed=False,
                exit_reason=exit_reason,
                reason_codes=[ReasonCode.RG_PASS.value],
                reason={
                    "exit_reason": exit_reason,
                    "entry_price": entry_price,
                    "hwm_price": hwm_price,
                    "last_price": last_price,
                    "rsi_14": rsi_14,
                    "ema20": ema20,
                    "ema60": ema60,
                    "atr_pct": atr_pct,
                },
            )

    if pre_block_reason:
        return MarketOpinion(
            signal="HOLD",
            confidence=0.55,
            target_position_pct=0.0,
            signal_target_pct=0.0,
            alpha=alpha.alpha,
            mom_s=alpha.mom_s,
            rev_s=alpha.rev_s,
            strength=alpha.strength,
            vol_scale=alpha.vol_scale,
            strategy_tag=state.strategy_tag,
            entry_allowed=False,
            exit_reason=None,
            reason_codes=[pre_block_code or ReasonCode.RG_DATA_BAD.value],
            reason={"block": pre_block_reason},
        )

    if entry_loss_block:
        return MarketOpinion(
            signal="HOLD",
            confidence=0.55,
            target_position_pct=0.0,
            signal_target_pct=0.0,
            alpha=alpha.alpha,
            mom_s=alpha.mom_s,
            rev_s=alpha.rev_s,
            strength=alpha.strength,
            vol_scale=alpha.vol_scale,
            strategy_tag=state.strategy_tag,
            entry_allowed=False,
            exit_reason=None,
            reason_codes=[ReasonCode.RG_EXPOSURE_LIMIT.value],
            reason={
                "daily_loss_pct": daily_loss_pct,
                "max_daily_loss_pct": max_daily_loss,
                "entry_block_ratio": cfg.daily_loss_entry_block_ratio,
            },
        )

    if cooldown_active and not (bool(cfg.cooldown_override_enabled) and alpha.alpha >= float(cfg.cooldown_override_alpha)):
        return MarketOpinion(
            signal="HOLD",
            confidence=0.55,
            target_position_pct=0.0,
            signal_target_pct=0.0,
            alpha=alpha.alpha,
            mom_s=alpha.mom_s,
            rev_s=alpha.rev_s,
            strength=alpha.strength,
            vol_scale=alpha.vol_scale,
            strategy_tag=state.strategy_tag,
            entry_allowed=False,
            exit_reason=None,
            reason_codes=[ReasonCode.RG_COOLDOWN_ACTIVE.value],
            reason={"cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until else None},
        )

    if alpha.alpha >= float(cfg.entry_alpha):
        conf = min(0.95, 0.50 + alpha.alpha * 0.45)
        return MarketOpinion(
            signal="LONG",
            confidence=float(conf),
            target_position_pct=float(alpha.signal_target_pct),
            signal_target_pct=float(alpha.signal_target_pct),
            alpha=alpha.alpha,
            mom_s=alpha.mom_s,
            rev_s=alpha.rev_s,
            strength=alpha.strength,
            vol_scale=alpha.vol_scale,
            strategy_tag=alpha.strategy_tag_candidate,
            entry_allowed=True,
            exit_reason=None,
            reason_codes=[ReasonCode.RG_PASS.value],
            reason={
                "alpha": alpha.alpha,
                "mom_s": alpha.mom_s,
                "rev_s": alpha.rev_s,
                "signal_target_pct": alpha.signal_target_pct,
                "strategy_tag": alpha.strategy_tag_candidate,
            },
        )

    return MarketOpinion(
        signal="HOLD",
        confidence=0.55,
        target_position_pct=0.0,
        signal_target_pct=0.0,
        alpha=alpha.alpha,
        mom_s=alpha.mom_s,
        rev_s=alpha.rev_s,
        strength=alpha.strength,
        vol_scale=alpha.vol_scale,
        strategy_tag=state.strategy_tag,
        entry_allowed=False,
        exit_reason=None,
        reason_codes=[ReasonCode.RG_EDGE_TOO_LOW.value],
        reason={"alpha": alpha.alpha, "entry_alpha": cfg.entry_alpha},
    )
