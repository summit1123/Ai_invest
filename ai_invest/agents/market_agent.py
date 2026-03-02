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


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, bool):
            return int(default)
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return int(value)
        s = str(value).strip()
        return int(float(s)) if s else int(default)
    except Exception:
        return int(default)


def _round_trip_fee_bps(rules: RulesConfig) -> float:
    fees_cfg = (rules.raw.get("fees") or {}) if isinstance(rules.raw, Mapping) else {}
    bid_bps = _as_float(fees_cfg.get("fallback_bid_fee_bps"), default=5.0)
    ask_bps = _as_float(fees_cfg.get("fallback_ask_fee_bps"), default=5.0)
    return max(0.0, float(bid_bps)) + max(0.0, float(ask_bps))


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
    trade_plan = context.get("trade_plan") if isinstance(context.get("trade_plan"), Mapping) else {}
    plan_exec = trade_plan.get("execution_plan") if isinstance(trade_plan.get("execution_plan"), Mapping) else {}
    plan_final_numbers = (
        plan_exec.get("final_numbers") if isinstance(plan_exec.get("final_numbers"), Mapping) else {}
    )
    plan_time_horizon = str(trade_plan.get("time_horizon") or "").strip().lower()
    learning_feedback = context.get("learning_feedback") if isinstance(context.get("learning_feedback"), Mapping) else {}
    learning_enabled = bool(learning_feedback.get("enabled", False))
    learning_profile = (
        learning_feedback.get("symbol_profile") if isinstance(learning_feedback.get("symbol_profile"), Mapping) else {}
    )
    feedback_sample_total = _as_int(learning_profile.get("sample_total"), default=0)
    feedback_trade_stats = (
        learning_profile.get("trade_stats") if isinstance(learning_profile.get("trade_stats"), Mapping) else {}
    )
    feedback_outcome_stats = (
        learning_profile.get("outcome_stats") if isinstance(learning_profile.get("outcome_stats"), Mapping) else {}
    )
    feedback_cost_ratio = _as_float(feedback_outcome_stats.get("oc_cost_underestimated_ratio"), default=0.0)
    feedback_latency_ratio = _as_float(feedback_outcome_stats.get("oc_execution_latency_ratio"), default=0.0)
    feedback_win_rate = _as_float(feedback_trade_stats.get("win_rate_trades"), default=0.0)
    feedback_avg_pnl_bps = _as_float(feedback_trade_stats.get("avg_pnl_bps"), default=0.0)
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
    fee_total_bps = _round_trip_fee_bps(rules)
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
        hold_seconds = (
            max(0.0, (now - state.entry_ts).total_seconds())
            if state.entry_ts is not None
            else None
        )
        min_hold_seconds = max(
            0,
            _as_int((plan_final_numbers or {}).get("min_hold_seconds"), default=int(rules.risk.min_hold_seconds)),
        )
        min_hold_active = bool(hold_seconds is not None and hold_seconds < float(min_hold_seconds))
        hwm_gain_bps = ((hwm_price - entry_price) / entry_price * 10000.0) if entry_price > 0 and hwm_price > 0 else 0.0
        # Trail exit is armed only after enough favorable move to clear round-trip cost.
        trail_arm_floor_bps = max(float(fee_total_bps) + 2.0, 12.0)
        trail_armed = bool(hwm_gain_bps >= float(trail_arm_floor_bps))
        exit_reason: str | None = None
        if entry_price > 0 and stop_pct > 0 and last_price > 0 and last_price <= entry_price * (1.0 - stop_pct):
            exit_reason = "STOP"
        elif (
            (not min_hold_active)
            and trail_armed
            and hwm_price > 0
            and trail_pct > 0
            and last_price > 0
            and last_price <= hwm_price * (1.0 - trail_pct)
        ):
            exit_reason = "TRAIL"
        elif (not min_hold_active) and (rsi_14 <= float(cfg.exit_rsi) or (ema20 > 0 and ema60 > 0 and ema20 < ema60)):
            exit_reason = "MOMENTUM_BREAK"
        elif state.entry_ts is not None:
            hold_minutes = max(0.0, (now - state.entry_ts).total_seconds() / 60.0)
            max_hold = float(cfg.time_stop_rev_minutes) if str(strategy_tag).upper() == "REV" else float(cfg.time_stop_mom_minutes)
            plan_max_hold = _as_int((plan_final_numbers or {}).get("max_hold_minutes"), default=0)
            if plan_max_hold > 0:
                max_hold = float(plan_max_hold)
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
                    "hold_seconds": hold_seconds,
                    "min_hold_seconds": min_hold_seconds,
                    "min_hold_active": min_hold_active,
                    "plan_max_hold_minutes": _as_int((plan_final_numbers or {}).get("max_hold_minutes"), default=0),
                    "plan_time_horizon": str(plan_time_horizon or ""),
                    "hwm_gain_bps": hwm_gain_bps,
                    "trail_armed": trail_armed,
                    "trail_arm_floor_bps": float(trail_arm_floor_bps),
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

    entry_alpha_dynamic = float(cfg.entry_alpha) + (
        float(cfg.entry_alpha_spread_k) * max(0.0, float(spread_bps)) / 10.0
    ) + (
        float(cfg.entry_alpha_fee_k) * max(0.0, float(fee_total_bps)) / 10.0
    )
    entry_alpha_feedback_adj = 0.0
    if bool(learning_enabled) and int(feedback_sample_total) >= 8:
        if float(feedback_cost_ratio) >= 0.20:
            entry_alpha_feedback_adj += min(0.06, 0.01 + (float(feedback_cost_ratio) - 0.20) * 0.16)
        if float(feedback_latency_ratio) >= 0.15:
            entry_alpha_feedback_adj += min(0.03, 0.005 + (float(feedback_latency_ratio) - 0.15) * 0.08)
        if (
            float(feedback_avg_pnl_bps) >= 25.0
            and float(feedback_win_rate) >= 0.58
            and float(feedback_cost_ratio) <= 0.15
        ):
            entry_alpha_feedback_adj -= 0.02

    entry_alpha_floor = max(0.10, float(cfg.entry_alpha) - 0.04)
    entry_alpha_effective = float(entry_alpha_dynamic) + float(entry_alpha_feedback_adj)
    entry_alpha_effective = min(0.95, max(float(entry_alpha_floor), float(entry_alpha_effective)))

    if alpha.alpha >= float(entry_alpha_effective):
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
                "entry_alpha": float(cfg.entry_alpha),
                "entry_alpha_dynamic": float(entry_alpha_dynamic),
                "entry_alpha_feedback_adj": float(entry_alpha_feedback_adj),
                "entry_alpha_floor": float(entry_alpha_floor),
                "entry_alpha_effective": float(entry_alpha_effective),
                "spread_bps": float(spread_bps),
                "fee_total_bps": float(fee_total_bps),
                "learning_feedback_enabled": bool(learning_enabled),
                "learning_feedback_sample_total": int(feedback_sample_total),
                "learning_feedback_cost_ratio": float(feedback_cost_ratio),
                "learning_feedback_latency_ratio": float(feedback_latency_ratio),
                "learning_feedback_win_rate": float(feedback_win_rate),
                "learning_feedback_avg_pnl_bps": float(feedback_avg_pnl_bps),
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
        reason={
            "alpha": alpha.alpha,
            "entry_alpha": float(cfg.entry_alpha),
            "entry_alpha_dynamic": float(entry_alpha_dynamic),
            "entry_alpha_feedback_adj": float(entry_alpha_feedback_adj),
            "entry_alpha_floor": float(entry_alpha_floor),
            "entry_alpha_effective": float(entry_alpha_effective),
            "spread_bps": float(spread_bps),
            "fee_total_bps": float(fee_total_bps),
            "learning_feedback_enabled": bool(learning_enabled),
            "learning_feedback_sample_total": int(feedback_sample_total),
            "learning_feedback_cost_ratio": float(feedback_cost_ratio),
            "learning_feedback_latency_ratio": float(feedback_latency_ratio),
            "learning_feedback_win_rate": float(feedback_win_rate),
            "learning_feedback_avg_pnl_bps": float(feedback_avg_pnl_bps),
        },
    )
