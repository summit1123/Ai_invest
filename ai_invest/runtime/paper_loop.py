from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from typing import Any, Mapping

import yaml
from zoneinfo import ZoneInfo

from ai_invest.agents.market_agent import market_agent_opine
from ai_invest.agents.ops_agent import ops_agent_opine
from ai_invest.agents.regime_agent import regime_agent_opine
from ai_invest.agents.risk_agent import risk_agent_opine
from ai_invest.config.capital_policy import resolve_capital_policy
from ai_invest.config.rules_loader import RulesConfig, load_rules
from ai_invest.domain.reason_codes import ReasonCode
from ai_invest.execution.live_execution import LiveExecutor
from ai_invest.execution.live_sync import sync_symbol_account_state
from ai_invest.execution.paper_execution import PaperExecutor
from ai_invest.execution.upbit_private import UpbitPrivateApiError, UpbitPrivateClient
from ai_invest.judge.ai_judge import ai_judge_shadow_decide
from ai_invest.judge.safe_judge import safe_judge_decide
from ai_invest.learning.outcome_evaluator import evaluate_closed_trade
from ai_invest.market_data.features import build_alpha_features_from_1m_candles, build_feature_snapshot_from_candles
from ai_invest.market_data.upbit_public import MarketSnapshot, UpbitPublicApiError, fetch_candles_minutes, fetch_market_snapshot
from ai_invest.market_data.upbit_ws import UpbitPublicStreamError, UpbitPublicWsSnapshotHub
from ai_invest.notifications.service import NotificationService
from ai_invest.ops.reconciliation import record_reconciliation_check
from ai_invest.research.news_signal import build_news_signal
from ai_invest.runtime.edge_calibration import (
    build_edge_calibration_dataset,
    load_edge_calibration_config,
    resolve_effective_cap_min_alpha,
)
from ai_invest.runtime.position_state import parse_position_state, with_hwm_update
from ai_invest.runtime.runtime_controls import build_runtime_controls
from ai_invest.storage.postgres import (
    DbAgentOpinion,
    DbDecision,
    DbDecisionOutcome,
    DbEvent,
    DbPauseLog,
    DbPosition,
    DbRuleVersion,
    DbRun,
    PostgresRepo,
)
from ai_invest.strategy.alpha_score import load_alpha_score_config

KST = ZoneInfo("Asia/Seoul")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _quote_currency(symbol: str) -> str:
    if "-" not in symbol:
        return "KRW"
    return symbol.split("-", 1)[0].strip().upper() or "KRW"


def _parse_dt(value: str) -> datetime | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, bool):
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


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return bool(value)
    s = str(value).strip().lower()
    if not s:
        return bool(default)
    return s in {"1", "true", "yes", "y", "on"}


def _env_bool(name: str, *, default: bool = False) -> bool:
    return _as_bool(os.environ.get(name), default=default)


def _latest_symbol_learning_feedback(*, repo: PostgresRepo, symbol: str) -> dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    row = repo.fetch_latest_agent_daily_report(agent_name="quant_strategist")
    if not isinstance(row, Mapping):
        return {"enabled": False, "symbol": sym}
    findings = row.get("findings")
    findings_map = findings if isinstance(findings, Mapping) else {}
    learning_raw = findings_map.get("learning_feedback")
    learning_map = learning_raw if isinstance(learning_raw, Mapping) else {}
    by_symbol = learning_map.get("by_symbol")
    by_symbol_map = by_symbol if isinstance(by_symbol, Mapping) else {}
    symbol_profile = by_symbol_map.get(sym)
    profile_map = symbol_profile if isinstance(symbol_profile, Mapping) else {}

    created_at = row.get("created_at")
    age_minutes = None
    if isinstance(created_at, datetime):
        ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        age_minutes = max(0.0, (_utcnow() - ts.astimezone(timezone.utc)).total_seconds() / 60.0)

    return {
        "enabled": bool(learning_map.get("enabled", False)),
        "symbol": sym,
        "report_id": str(row.get("report_id") or ""),
        "report_age_minutes": float(age_minutes) if age_minutes is not None else None,
        "summary": dict(learning_map.get("summary") or {}) if isinstance(learning_map.get("summary"), Mapping) else {},
        "top_symbol": str(learning_map.get("top_symbol") or ""),
        "symbol_profile": dict(profile_map),
    }


def _latest_research_signal(*, repo: PostgresRepo, symbol: str) -> dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    row = repo.fetch_latest_agent_daily_report(agent_name="research_agent")
    if not isinstance(row, Mapping):
        return {"enabled": False, "symbol": sym}

    findings = row.get("findings") if isinstance(row.get("findings"), Mapping) else {}
    report_symbol = str(findings.get("symbol") or "").strip().upper()
    if report_symbol and report_symbol != sym:
        return {"enabled": False, "symbol": sym}

    created_at = row.get("created_at")
    age_minutes = None
    if isinstance(created_at, datetime):
        ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        age_minutes = max(0.0, (_utcnow() - ts.astimezone(timezone.utc)).total_seconds() / 60.0)

    signal = findings.get("news_signal") if isinstance(findings.get("news_signal"), Mapping) else {}
    if not signal:
        headlines = findings.get("headlines") if isinstance(findings.get("headlines"), list) else []
        risks_map = row.get("risks") if isinstance(row.get("risks"), Mapping) else {}
        watchlist = risks_map.get("watchlist") if isinstance(risks_map.get("watchlist"), list) else []
        signal = build_news_signal(headlines=headlines, risk_watchlist=watchlist)

    out = dict(signal)
    out["enabled"] = bool(out.get("enabled", False))
    out["symbol"] = sym
    out["report_id"] = str(row.get("report_id") or "")
    out["report_age_minutes"] = float(age_minutes) if age_minutes is not None else None
    return out


def _resolve_cap_config(activation_gate: Mapping[str, Any]) -> dict[str, Any]:
    cap = (activation_gate.get("conditional_activation") or {}) if isinstance(activation_gate, Mapping) else {}
    conditions = (cap.get("conditions") or {}) if isinstance(cap, Mapping) else {}
    promotion = (cap.get("promotion") or {}) if isinstance(cap, Mapping) else {}
    return {
        "enabled": bool(cap.get("enabled", False)),
        "auto_promote_to": "PAPER",
        "conditions": {
            "min_alpha": _as_float(conditions.get("min_alpha"), default=0.75),
            "max_spread_bps": _as_float(conditions.get("max_spread_bps"), default=1.5),
            "min_vol_z": _as_float(conditions.get("min_vol_z"), default=0.0),
            "min_atr_pct": _as_float(conditions.get("min_atr_pct"), default=0.08),
            "sustain_seconds": max(15, _as_int(conditions.get("sustain_seconds"), default=180)),
            "min_pass_conditions": max(1, min(4, _as_int(conditions.get("min_pass_conditions"), default=3))),
            "require_alpha_confirm": bool(conditions.get("require_alpha_confirm", True)),
        },
        "promotion": {
            "target_position_pct_cap": max(0.0, _as_float(promotion.get("target_position_pct_cap"), default=3.0)),
            "cooldown_after_promotion_minutes": max(
                0,
                _as_int(promotion.get("cooldown_after_promotion_minutes"), default=60),
            ),
            "promotion_ttl_minutes": max(1, _as_int(promotion.get("promotion_ttl_minutes"), default=120)),
        },
    }


def _resolve_live_data_collection_config(raw_rules: Mapping[str, Any]) -> dict[str, Any]:
    governance_cfg = (raw_rules.get("governance") or {}) if isinstance(raw_rules, Mapping) else {}
    activation_gate_cfg = (governance_cfg.get("activation_gate") or {}) if isinstance(governance_cfg, Mapping) else {}
    live_cfg = (activation_gate_cfg.get("live_data_collection") or {}) if isinstance(activation_gate_cfg, Mapping) else {}
    return {
        "enabled": bool(live_cfg.get("enabled", False)),
        "bootstrap_min_backtest_trades": max(
            1,
            _as_int(live_cfg.get("bootstrap_min_backtest_trades"), default=8),
        ),
        "target_position_pct": max(
            0.0,
            min(100.0, _as_float(live_cfg.get("target_position_pct"), default=12.0)),
        ),
        "exploration_enabled": bool(live_cfg.get("exploration_enabled", True)),
        "profit_floor_bps": float(_as_float(live_cfg.get("profit_floor_bps"), default=0.0)),
        "profit_required_margin_bps": float(
            _as_float(live_cfg.get("profit_required_margin_bps"), default=0.0)
        ),
        "min_predicted_after_cost_bps": float(
            _as_float(live_cfg.get("min_predicted_after_cost_bps"), default=-0.25)
        ),
        "alpha_bypass_on_exploration": bool(live_cfg.get("alpha_bypass_on_exploration", True)),
    }


def _apply_live_learning_runtime_policy_compat(
    *,
    raw_rules: Mapping[str, Any],
    universe_mode: str,
    runtime_activation_gate: Mapping[str, Any],
    runtime_entry_policy: Mapping[str, Any],
    runtime_target_pct: float | None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    gate = dict(runtime_activation_gate or {})
    policy = dict(runtime_entry_policy or {})
    if str(universe_mode or "").strip().lower() != "live":
        return gate, policy, False

    live_cfg = _resolve_live_data_collection_config(raw_rules)
    if not bool(live_cfg.get("enabled")):
        return gate, policy, False

    selected_backtest = gate.get("selected_backtest") if isinstance(gate.get("selected_backtest"), Mapping) else {}
    trades_actual = max(0, _as_int(selected_backtest.get("trades"), default=0))
    bootstrap_min = max(1, _as_int(live_cfg.get("bootstrap_min_backtest_trades"), default=8))
    reason_code = str(gate.get("reason_code") or "").strip().upper()
    decision = str(gate.get("decision") or "").strip().upper()
    hold_mode = str(gate.get("hold_mode") or "").strip().upper()
    decision_effective = str(gate.get("decision_effective") or "").strip().upper()
    live_candidate = bool(
        gate.get("live_data_collection_mode")
        or gate.get("live_data_collection_applied")
        or (not gate and runtime_target_pct is None)
        or (
            bool(gate.get("inter_slot_realtime_mode"))
            and decision == "PAPER"
            and trades_actual < bootstrap_min
            and (decision_effective == "HOLD" or hold_mode.startswith("HOLD"))
            and reason_code in {"POLICY_GATE_BLOCKED", "POLICY_GATE_INSUFFICIENT_DATA"}
        )
    )
    if not live_candidate:
        return gate, policy, False

    target_pct = float(_as_float(runtime_target_pct, default=float(live_cfg.get("target_position_pct") or 0.0)))
    if target_pct <= 0.0:
        target_pct = float(live_cfg.get("target_position_pct") or 0.0)

    gate["live_data_collection_mode"] = True
    gate["live_data_collection_applied"] = True
    gate["live_data_collection_compat_applied"] = True
    gate["live_data_collection_min_trades_effective"] = int(bootstrap_min)
    gate["live_data_collection_target_pct"] = float(target_pct)
    gate.setdefault("decision", "HOLD")
    gate.setdefault("decision_effective", "HOLD")
    gate.setdefault("hold_mode", "HOLD_CONDITIONAL")
    gate.setdefault("inter_slot_realtime_mode", True)
    gate.setdefault("hard_plan_block", False)
    gate.setdefault("soft_plan_block", False)
    gate.setdefault("plan_execution_blocked", False)
    gate.setdefault("final_plan_no_trade_declared", False)
    gate["reason_code"] = "POLICY_GATE_INSUFFICIENT_DATA"

    policy.update(
        {
            "mode": "LIVE_DATA_COLLECTION",
            "entry_objective": "learning-loop",
            "runtime_entry_allowed": True,
            "runtime_promotion_enabled": True,
            "execution_authority": "realtime_loop",
            "entry_timing_owner": "realtime_loop",
            "exploration_enabled": bool(live_cfg.get("exploration_enabled", True)),
            "profit_floor_bps": float(live_cfg.get("profit_floor_bps", 0.0)),
            "profit_required_margin_bps": float(live_cfg.get("profit_required_margin_bps", 0.0)),
            "learning_mode": True,
            "min_predicted_after_cost_bps": float(live_cfg.get("min_predicted_after_cost_bps", -0.25)),
            "alpha_bypass_on_exploration": bool(live_cfg.get("alpha_bypass_on_exploration", True)),
            "meeting_buy_flag": False,
            "meeting_sell_flag": True,
            "policy_cap_target_pct": float(target_pct),
        }
    )
    return gate, policy, True


def _cap_required_passes(*, sustain_seconds: int, loop_interval_seconds: int) -> int:
    sec = max(1, int(loop_interval_seconds))
    sustain = max(1, int(sustain_seconds))
    return max(1, int(ceil(float(sustain) / float(sec))))


def _cap_progress_pass_count(
    *,
    cond_results: Mapping[str, Any],
    min_pass: int,
    require_alpha_confirm: bool = True,
) -> tuple[int, bool]:
    raw_pass_count = sum(1 for value in dict(cond_results or {}).values() if bool(value))
    mandatory_conditions_passed = bool((not require_alpha_confirm) or bool(cond_results.get("alpha")))
    if mandatory_conditions_passed:
        return int(raw_pass_count), True
    return int(min(int(raw_pass_count), max(0, int(min_pass) - 1))), False


def _trade_plan_is_active(plan: dict[str, Any]) -> bool:
    now = _utcnow()
    vf = _parse_dt(str(plan.get("valid_from_kst") or plan.get("valid_from") or ""))
    vt = _parse_dt(str(plan.get("valid_to_kst") or plan.get("valid_to") or ""))
    if vf and now < vf:
        return False
    if vt and now >= vt:
        return False
    return True


def _plan_is_hold_activation(plan: Mapping[str, Any] | None) -> bool:
    if not isinstance(plan, Mapping):
        return False
    gate = plan.get("activation_gate") if isinstance(plan.get("activation_gate"), Mapping) else {}
    decision_effective = str((gate or {}).get("decision_effective") or "").strip().upper()
    decision = str((gate or {}).get("decision") or "").strip().upper()
    hold_mode = str((gate or {}).get("hold_mode") or "").strip().upper()
    return bool(decision_effective == "HOLD" or decision == "HOLD" or hold_mode.startswith("HOLD"))


def _slot_dt_from_key(slot_key: str | None) -> datetime | None:
    value = str(slot_key or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:16], "%Y-%m-%d %H:%M").replace(tzinfo=KST).astimezone(timezone.utc)
    except Exception:
        return None


def _resolve_runtime_trade_plan(*, repo: PostgresRepo, rules_raw: Mapping[str, Any]) -> dict[str, Any] | None:
    latest_plan = repo.fetch_latest_trade_plan(prefer_active=True)
    if not isinstance(latest_plan, Mapping):
        return None
    plan = dict(latest_plan)
    if _trade_plan_is_active(plan):
        return plan

    gov_cfg = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    continuity_cfg = (gov_cfg.get("plan_continuity") or {}) if isinstance(gov_cfg, Mapping) else {}
    window_min = max(3, _as_int(gov_cfg.get("meeting_window_min"), default=5))
    handoff_grace_minutes = max(
        int(window_min),
        _as_int(continuity_cfg.get("handoff_grace_minutes"), default=max(20, window_min * 2)),
    )
    now_utc = _utcnow()
    plan_valid_to = _parse_dt(str(plan.get("valid_to_kst") or plan.get("valid_to") or ""))
    if plan_valid_to is None:
        return None
    if (now_utc - plan_valid_to).total_seconds() > float(handoff_grace_minutes) * 60.0:
        return None

    latest_slot_dt = _slot_dt_from_key(str(plan.get("slot_key") or ""))
    sessions = repo.fetch_meeting_sessions(limit=10)
    for row in sessions:
        if str(row.get("meeting_type") or "").upper() != "DAILY_STRATEGY":
            continue
        if str(row.get("status") or "").upper() != "OPEN":
            continue
        agenda = row.get("agenda") if isinstance(row.get("agenda"), Mapping) else {}
        open_slot_key = str(agenda.get("slot_key") or "").strip()
        open_slot_dt = _slot_dt_from_key(open_slot_key)
        if latest_slot_dt is not None and open_slot_dt is not None and open_slot_dt <= latest_slot_dt:
            continue
        started_at = row.get("started_at")
        if isinstance(started_at, datetime):
            started_utc = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
            bridge_expire_at = min(
                now_utc + timedelta(minutes=handoff_grace_minutes),
                started_utc.astimezone(timezone.utc) + timedelta(minutes=handoff_grace_minutes),
            )
        else:
            bridge_expire_at = now_utc + timedelta(minutes=handoff_grace_minutes)
        if bridge_expire_at <= now_utc:
            continue
        bridge = dict(plan)
        activation_gate = dict(bridge.get("activation_gate") or {})
        activation_gate["handoff_pending"] = True
        activation_gate["handoff_reason"] = "AWAITING_NEXT_MEETING_CLOSE"
        activation_gate["handoff_from_slot_key"] = str(plan.get("slot_key") or "")
        activation_gate["handoff_open_slot_key"] = open_slot_key or None
        activation_gate["handoff_open_meeting_id"] = str(row.get("meeting_id") or "") or None
        activation_gate["handoff_expire_at_kst"] = bridge_expire_at.astimezone(KST).isoformat()
        bridge["activation_gate"] = activation_gate
        bridge["valid_to_kst"] = bridge_expire_at.astimezone(KST).isoformat()
        bridge["handoff_bridge"] = True
        return bridge
    return None


def _market_input_for_safe_judge(market: MarketOpinion) -> dict[str, Any]:
    payload = asdict(market)
    reason = payload.get("reason")
    if not isinstance(reason, Mapping):
        reason = {}
        payload["reason"] = reason
    edge_calibration = reason.get("edge_calibration") if isinstance(reason.get("edge_calibration"), Mapping) else {}
    if edge_calibration:
        payload["edge_calibration"] = dict(edge_calibration)
        if payload.get("predicted_after_cost_bps") is None:
            payload["predicted_after_cost_bps"] = edge_calibration.get("predicted_after_cost_bps")
        if payload.get("required_after_cost_bps") is None:
            payload["required_after_cost_bps"] = edge_calibration.get("required_after_cost_bps")
        if payload.get("after_cost_uncertainty_bps") is None:
            payload["after_cost_uncertainty_bps"] = edge_calibration.get("uncertainty_bps")
    return payload


def _latest_quant_candidate_symbol(
    *,
    repo: PostgresRepo,
    max_age_minutes: int = 180,
    allowed_symbols: set[str] | None = None,
) -> str | None:
    row = repo.fetch_latest_agent_daily_report(agent_name="quant_strategist")
    if not isinstance(row, Mapping):
        return None

    created_at = row.get("created_at")
    if isinstance(created_at, datetime):
        ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        age_min = max(0.0, (_utcnow() - ts.astimezone(timezone.utc)).total_seconds() / 60.0)
        if age_min > float(max(1, int(max_age_minutes))):
            return None

    findings = row.get("findings") if isinstance(row.get("findings"), Mapping) else {}
    candidates = findings.get("candidates") if isinstance(findings, Mapping) and isinstance(findings.get("candidates"), list) else []

    best_symbol: str | None = None
    best_score = -10_000.0
    for cand in candidates:
        if not isinstance(cand, Mapping):
            continue
        sym = str(cand.get("symbol") or "").strip().upper()
        if not sym:
            continue
        if allowed_symbols is not None and sym not in allowed_symbols:
            continue
        score = _as_float(cand.get("score"), default=-9999.0)
        if best_symbol is None or float(score) > float(best_score):
            best_symbol = sym
            best_score = float(score)

    if best_symbol:
        return best_symbol

    suggested = findings.get("suggested_plan") if isinstance(findings, Mapping) and isinstance(findings.get("suggested_plan"), Mapping) else {}
    sym = str((suggested or {}).get("symbol") or "").strip().upper()
    if sym and (allowed_symbols is None or sym in allowed_symbols):
        return sym
    return None


def _select_runtime_candidate_symbol(
    *,
    repo: PostgresRepo,
    default_symbol: str,
    plan_symbol: str | None,
    plan_is_hold: bool,
    plan_symbol_allowed: bool,
    realtime_between_meetings: bool,
    inter_slot_symbol_policy: str,
    realtime_symbol_max_age_min: int,
    allowed_symbols: set[str] | None,
) -> tuple[str, str, bool]:
    candidate_symbol = str(default_symbol)
    candidate_source = "default"
    inter_slot_realtime_mode = False

    runtime_symbol_rotation = bool(
        plan_is_hold and realtime_between_meetings and str(inter_slot_symbol_policy or "").strip().lower() == "allow_quant"
    )
    if plan_symbol_allowed and not runtime_symbol_rotation:
        return str(plan_symbol), "plan_symbol", False

    if runtime_symbol_rotation:
        runtime_symbol = _latest_quant_candidate_symbol(
            repo=repo,
            max_age_minutes=int(realtime_symbol_max_age_min),
            allowed_symbols=allowed_symbols,
        )
        if runtime_symbol:
            return str(runtime_symbol), "quant_inter_slot", True
        if plan_symbol_allowed:
            return str(plan_symbol), "plan_symbol_hold_fallback", False

    if plan_symbol_allowed:
        candidate_symbol = str(plan_symbol)
        candidate_source = "plan_symbol_hold_policy" if plan_is_hold else "plan_symbol"
    return candidate_symbol, candidate_source, inter_slot_realtime_mode


def _fetch_open_position_symbols(*, repo: PostgresRepo) -> list[str]:
    """Return currently open symbols (qty != 0) in a stable order."""
    try:
        overview = repo.fetch_portfolio_overview(quote_currency="KRW")
        positions = overview.get("positions") if isinstance(overview, Mapping) else []
        out: list[str] = []
        for row in positions if isinstance(positions, list) else []:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            qty = _as_float(row.get("qty"), default=0.0)
            if symbol and qty > 0:
                out.append(symbol)
        return sorted(set(out))
    except Exception:
        return []


def _timeframe_to_minutes(tf: str) -> int:
    tf = tf.strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    raise ValueError(f"Unsupported timeframe: {tf}")


def build_common_payload(
    *,
    run_id: uuid.UUID,
    rule_version_id: uuid.UUID,
    decision_id: uuid.UUID,
    snapshot: MarketSnapshot,
    features: dict[str, Any],
    ops: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": str(run_id),
        "rule_version_id": str(rule_version_id),
        "decision_id": str(decision_id),
        "timestamp_utc": _utcnow().isoformat(),
        "symbol": snapshot.symbol,
        "snapshot": {
            "last_price": snapshot.last_price,
            "best_bid": snapshot.best_bid,
            "best_ask": snapshot.best_ask,
            "mid_price": snapshot.mid_price,
            "spread_bps": snapshot.spread_bps,
        },
        "features": features,
        "ops": ops,
        "context": context,
    }


def default_context(*, daily_loss_pct: float = 0.0) -> dict[str, Any]:
    return {
        "account": {"daily_loss_pct": float(daily_loss_pct)},
        "risk_limits": {},
    }


def _rules_hash(*, raw_rules: Mapping[str, Any]) -> str:
    try:
        payload = json.dumps(raw_rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        payload = str(raw_rules)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_universe_id(*, raw_rules: Mapping[str, Any], rules: RulesConfig) -> str:
    uv = (raw_rules.get("universe") or {}) if isinstance(raw_rules, Mapping) else {}
    dyn = (uv.get("dynamic") or {}) if isinstance(uv, Mapping) else {}
    static_symbols = [str(s).strip().upper() for s in list(rules.universe.symbols) if str(s).strip()]
    payload = {
        "market": str(rules.universe.market).strip().upper(),
        "mode": str(rules.universe.mode).strip().lower(),
        "symbols": sorted(set(static_symbols)),
        "max_open_positions": int(rules.universe.max_open_positions),
        "dynamic_enabled": bool(dyn.get("enabled", False)),
        "dynamic_top_n": _as_int(dyn.get("top_n_by_24h_turnover"), default=0),
        "dynamic_max_candidates": _as_int(dyn.get("max_candidates"), default=0),
        "dynamic_enforce_static_allowlist": bool(dyn.get("enforce_static_allowlist", False)),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f"uv-{digest[:16]}"


def _load_runtime_rules(*, rules_path: Path) -> tuple[RulesConfig, dict[str, Any], int, str, str]:
    rules = load_rules(rules_path)
    raw = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    raw_rules = dict(raw) if isinstance(raw, Mapping) else dict(rules.raw or {})
    mtime_ns = int(rules_path.stat().st_mtime_ns)
    rules_hash = _rules_hash(raw_rules=raw_rules)
    universe_id = _build_universe_id(raw_rules=raw_rules, rules=rules)
    return rules, raw_rules, mtime_ns, rules_hash, universe_id


def run_paper_loop(*, cycles: int = 1, sleep_sec: float | None = None) -> None:
    rules_path = Path("rules.yaml")
    rules, raw_rules, rules_mtime_ns, rules_hash, universe_id = _load_runtime_rules(rules_path=rules_path)
    universe_cfg = (raw_rules.get("universe") or {}) if isinstance(raw_rules, Mapping) else {}
    universe_mode = str((universe_cfg.get("mode") or "paper")).strip().lower()
    is_live_mode = bool(universe_mode == "live")
    governance_cfg = (raw_rules.get("governance") or {}) if isinstance(raw_rules, Mapping) else {}
    activation_gate_cfg = (governance_cfg.get("activation_gate") or {}) if isinstance(governance_cfg, Mapping) else {}

    live_execution_enabled = bool(activation_gate_cfg.get("live_execution_enabled", False))
    live_env_enabled = _env_bool("ENABLE_LIVE_TRADING", default=False)
    if is_live_mode:
        if not live_execution_enabled:
            raise RuntimeError("Live mode blocked: governance.activation_gate.live_execution_enabled=false")
        if not live_env_enabled:
            raise RuntimeError("Live mode blocked: ENABLE_LIVE_TRADING is not true")

    repo = PostgresRepo()
    live_client: UpbitPrivateClient | None = None
    if is_live_mode:
        live_client = UpbitPrivateClient.from_env()
        executor = LiveExecutor(repo, live_client)
    else:
        executor = PaperExecutor(repo)
    notifier = NotificationService(repo)
    market_data_cfg = (raw_rules.get("market_data") or {}) if isinstance(raw_rules, Mapping) else {}
    public_ws_enabled = _as_bool(
        market_data_cfg.get("public_ws_enabled"),
        default=_env_bool("UPBIT_PUBLIC_WS_ENABLED", default=True),
    )
    snapshot_hub: UpbitPublicWsSnapshotHub | None = UpbitPublicWsSnapshotHub() if public_ws_enabled else None

    run_id = uuid.uuid4()
    rule_version_id = uuid.uuid4()
    now0 = _utcnow()
    repo.insert_run(
        DbRun(
            run_id=run_id,
            run_type=("LIVE" if is_live_mode else "PAPER"),
            started_at=now0,
            ended_at=None,
            description="runtime loop (dev)",
            config={
                "rules_version": rules.version,
                "mode": universe_mode,
                "rules_hash": rules_hash,
                "universe_id": universe_id,
            },
            git_commit=None,
        )
    )
    repo.insert_rule_version(
        DbRuleVersion(
            rule_version_id=rule_version_id,
            created_by="system",
            parent_version=None,
            status="ACTIVE",
            summary=f"bootstrap from rules.yaml ({universe_mode} loop, hash={rules_hash[:12]})",
            rules_dsl=raw_rules,
            diff={},
            backtest_report={},
        )
    )

    default_symbol = rules.universe.symbols[0]
    dynamic_cfg = (universe_cfg.get("dynamic") or {}) if isinstance(universe_cfg, Mapping) else {}
    enforce_static_allowlist = bool(dynamic_cfg.get("enforce_static_allowlist", False))
    # Seed paper cash once so position sizing can use target_position_pct realistically.
    paper_cfg = raw_rules.get("paper", {}) if isinstance(raw_rules, dict) else {}
    seed_cash = float((paper_cfg or {}).get("initial_cash_krw") or 0.0)
    if (not is_live_mode) and seed_cash > 0:
        repo.ensure_paper_seed_cash(currency=_quote_currency(default_symbol), amount=seed_cash)

    scheduling_cfg = (raw_rules.get("scheduling") or {}) if isinstance(raw_rules, Mapping) else {}
    hot_reload_enabled = bool(scheduling_cfg.get("rules_hot_reload_enabled", True))
    cap_runtime_state: dict[str, dict[str, Any]] = {}
    entry_confirm_state: dict[str, int] = {}
    edge_calibration_state: dict[str, dict[str, Any]] = {}
    symbol_rr_cursor = 0

    for _i in range(cycles):
        if bool(hot_reload_enabled):
            try:
                latest_mtime_ns = int(rules_path.stat().st_mtime_ns)
            except Exception:
                latest_mtime_ns = rules_mtime_ns
            if latest_mtime_ns != rules_mtime_ns:
                try:
                    prev_rule_version_id = rule_version_id
                    prev_rules_hash = str(rules_hash)
                    prev_universe_id = str(universe_id)
                    new_rules, new_raw, new_mtime_ns, new_rules_hash, new_universe_id = _load_runtime_rules(
                        rules_path=rules_path
                    )
                    reloaded_universe_cfg = (
                        (new_raw.get("universe") or {}) if isinstance(new_raw, Mapping) else {}
                    )
                    reloaded_mode = str((reloaded_universe_cfg.get("mode") or "paper")).strip().lower()
                    if reloaded_mode != universe_mode:
                        repo.insert_event(
                            DbEvent(
                                event_id=uuid.uuid4(),
                                ts=_utcnow(),
                                event_type="RULES_RELOAD_SKIPPED",
                                entity_type="rule_versions",
                                entity_id=str(prev_rule_version_id),
                                run_id=run_id,
                                rule_version_id=prev_rule_version_id,
                                payload={
                                    "reason": "mode_mismatch",
                                    "runtime_mode": str(universe_mode),
                                    "requested_mode": str(reloaded_mode),
                                    "rules_hash_prev": prev_rules_hash,
                                    "rules_hash_new": new_rules_hash,
                                },
                            )
                        )
                        rules_mtime_ns = new_mtime_ns
                    else:
                        rules = new_rules
                        raw_rules = new_raw
                        rules_mtime_ns = new_mtime_ns
                        rules_hash = new_rules_hash
                        universe_id = new_universe_id
                        universe_cfg = (
                            (raw_rules.get("universe") or {}) if isinstance(raw_rules, Mapping) else {}
                        )
                        governance_cfg = (
                            (raw_rules.get("governance") or {}) if isinstance(raw_rules, Mapping) else {}
                        )
                        activation_gate_cfg = (
                            (governance_cfg.get("activation_gate") or {})
                            if isinstance(governance_cfg, Mapping)
                            else {}
                        )
                        dynamic_cfg = (
                            (universe_cfg.get("dynamic") or {}) if isinstance(universe_cfg, Mapping) else {}
                        )
                        enforce_static_allowlist = bool(dynamic_cfg.get("enforce_static_allowlist", False))
                        default_symbol = rules.universe.symbols[0]
                        rule_version_id = uuid.uuid4()
                        repo.insert_rule_version(
                            DbRuleVersion(
                                rule_version_id=rule_version_id,
                                created_by="runtime_hot_reload",
                                parent_version=prev_rule_version_id,
                                status="ACTIVE",
                                summary=f"hot reload from rules.yaml (hash={rules_hash[:12]})",
                                rules_dsl=raw_rules,
                                diff={
                                    "rules_hash_prev": prev_rules_hash,
                                    "rules_hash_new": rules_hash,
                                    "universe_id_prev": prev_universe_id,
                                    "universe_id_new": universe_id,
                                },
                                backtest_report={},
                            )
                        )
                        repo.insert_event(
                            DbEvent(
                                event_id=uuid.uuid4(),
                                ts=_utcnow(),
                                event_type="RULES_RELOADED",
                                entity_type="rule_versions",
                                entity_id=str(rule_version_id),
                                run_id=run_id,
                                rule_version_id=rule_version_id,
                                payload={
                                    "parent_rule_version_id": str(prev_rule_version_id),
                                    "rules_hash_prev": prev_rules_hash,
                                    "rules_hash_new": rules_hash,
                                    "universe_id_prev": prev_universe_id,
                                    "universe_id_new": universe_id,
                                },
                            )
                        )
                except Exception as exc:
                    repo.insert_event(
                        DbEvent(
                            event_id=uuid.uuid4(),
                            ts=_utcnow(),
                            event_type="RULES_RELOAD_FAILED",
                            entity_type="rule_versions",
                            entity_id=str(rule_version_id),
                            run_id=run_id,
                            rule_version_id=rule_version_id,
                            payload={"error": str(exc)[:300]},
                        )
                    )

        universe_cfg = (raw_rules.get("universe") or {}) if isinstance(raw_rules, Mapping) else {}
        governance_cfg = (raw_rules.get("governance") or {}) if isinstance(raw_rules, Mapping) else {}
        dynamic_cfg = (universe_cfg.get("dynamic") or {}) if isinstance(universe_cfg, Mapping) else {}
        enforce_static_allowlist = bool(dynamic_cfg.get("enforce_static_allowlist", False))
        default_symbol = rules.universe.symbols[0]
        timeframe_entry = str((raw_rules.get("signal") or {}).get("timeframe_entry", "15m"))
        tf_min = _timeframe_to_minutes(timeframe_entry)
        alpha_cfg = load_alpha_score_config(rules_raw=raw_rules)
        alpha_cfg_raw = (
            ((raw_rules.get("strategy") or {}).get("alpha_score") or {})
            if isinstance(raw_rules, Mapping)
            else {}
        )
        alpha_lookback = max(120, int(alpha_cfg.lookback_minutes))
        entry_confirm_bars = max(1, _as_int(alpha_cfg_raw.get("entry_confirm_bars"), default=1))
        decision_interval_sec = max(
            1,
            int(_as_float((raw_rules.get("scheduling", {}) or {}).get("decision_interval_sec"), default=15.0)),
        )
        micro_cfg = (governance_cfg.get("micro_mode") or {}) if isinstance(governance_cfg, Mapping) else {}
        realtime_between_meetings = bool(micro_cfg.get("realtime_between_meetings", True))
        realtime_symbol_max_age_min = max(15, _as_int(micro_cfg.get("realtime_symbol_max_age_min"), default=180))
        inter_slot_symbol_policy = str(micro_cfg.get("inter_slot_symbol_policy") or "plan_only").strip().lower()
        if inter_slot_symbol_policy not in {"plan_only", "allow_quant"}:
            inter_slot_symbol_policy = "plan_only"

        decision_id = uuid.uuid4()
        symbol = default_symbol
        symbol_source = "default"
        plan_symbol: str | None = None
        plan_is_hold = False
        manage_open_position_only = False
        inter_slot_realtime_mode = False
        plan = _resolve_runtime_trade_plan(repo=repo, rules_raw=raw_rules)
        if plan and _trade_plan_is_active(plan):
            p_sym = str(plan.get("symbol") or "").strip().upper()
            if p_sym:
                plan_symbol = p_sym
            plan_is_hold = _plan_is_hold_activation(plan)

        allowed_symbols = set(rules.universe.symbols) if enforce_static_allowlist else None
        plan_symbol_allowed = bool(
            plan_symbol and (allowed_symbols is None or str(plan_symbol) in allowed_symbols)
        )
        max_open_positions = max(1, int(rules.universe.max_open_positions))
        candidate_symbol, candidate_source, inter_slot_realtime_mode = _select_runtime_candidate_symbol(
            repo=repo,
            default_symbol=str(default_symbol),
            plan_symbol=plan_symbol,
            plan_is_hold=bool(plan_is_hold),
            plan_symbol_allowed=bool(plan_symbol_allowed),
            realtime_between_meetings=bool(realtime_between_meetings),
            inter_slot_symbol_policy=str(inter_slot_symbol_policy),
            realtime_symbol_max_age_min=int(realtime_symbol_max_age_min),
            allowed_symbols=allowed_symbols,
        )

        open_symbols = _fetch_open_position_symbols(repo=repo)
        open_set = set(open_symbols)
        if open_symbols:
            symbol_pool = sorted(set(str(s).strip().upper() for s in open_symbols if str(s or "").strip()))
            can_add_new_symbol = len(open_set) < int(max_open_positions)
            if can_add_new_symbol and candidate_symbol and candidate_symbol not in open_set:
                symbol_pool.append(str(candidate_symbol))
            if plan_symbol and plan_symbol in open_set:
                symbol_pool.append(str(plan_symbol))
            symbol_pool = sorted(set(symbol_pool))
            if not symbol_pool:
                symbol_pool = [str(default_symbol)]
            symbol = str(symbol_pool[int(symbol_rr_cursor % len(symbol_pool))])
            symbol_rr_cursor += 1
            if symbol in open_set and plan_symbol and symbol == str(plan_symbol):
                symbol_source = "open_position_plan_symbol"
            elif symbol in open_set:
                symbol_source = "open_position_round_robin"
                if plan_symbol and plan_symbol != symbol:
                    manage_open_position_only = True
            else:
                symbol_source = f"{candidate_source}_under_cap"
        else:
            symbol = str(candidate_symbol)
            symbol_source = str(candidate_source)

        if is_live_mode and live_client is not None:
            try:
                sync_symbol_account_state(
                    repo=repo,
                    client=live_client,
                    symbol=symbol,
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                )
            except (UpbitPrivateApiError, Exception) as exc:
                now_fail = _utcnow()
                err_msg = f"live account sync failed for {symbol}: {exc}"
                print(f"[경고] {err_msg}")
                pause_event_id = uuid.uuid4()
                repo.insert_event(
                    DbEvent(
                        event_id=pause_event_id,
                        ts=now_fail,
                        event_type="PAUSE",
                        entity_type="pause_log",
                        entity_id="AUTO",
                        run_id=run_id,
                        rule_version_id=rule_version_id,
                        payload={"symbol": symbol, "reason_type": "LIVE_SYNC_FAIL", "error": str(exc)},
                    )
                )
                repo.insert_pause_log(
                    DbPauseLog(
                        pause_id=uuid.uuid4(),
                        ts_pause=now_fail,
                        ts_resume=None,
                        reason_type="LIVE_SYNC_FAIL",
                        severity="HIGH",
                        auto_resumable=False,
                        resume_policy={},
                        notes=err_msg,
                        run_id=run_id,
                    )
                )
                notifier.notify_pause(
                    event_id=pause_event_id,
                    symbol=symbol,
                    reason_type="LIVE_SYNC_FAIL",
                    run_id=run_id,
                )
                break

        try:
            if snapshot_hub is not None:
                ws_symbols = [str(symbol)]
                if plan_symbol:
                    ws_symbols.append(str(plan_symbol))
                ws_symbols.extend([str(s) for s in list(open_symbols or []) if str(s or "").strip()])
                snapshot_hub.set_symbols(ws_symbols)
                snapshot = snapshot_hub.get_snapshot(
                    symbol,
                    wait_timeout_sec=min(2.0, max(0.5, float(decision_interval_sec) / 10.0)),
                    allow_rest_fallback=True,
                )
            else:
                snapshot = fetch_market_snapshot(symbol)
        except (UpbitPublicApiError, UpbitPublicStreamError) as exc:
            # 거래소 공용 API 일시 제한(429) 등은 루프를 죽이지 않고 다음 사이클에서 재시도한다.
            print(f"[경고] market snapshot failed for {symbol}: {exc}")
            if sleep_sec is not None:
                time.sleep(float(sleep_sec))
            continue
        quote_ccy = _quote_currency(symbol)
        cash = repo.fetch_cash_balance(currency=quote_ccy)
        pos = repo.fetch_position(symbol)
        current_qty = float(pos.qty) if pos else 0.0
        position_state = parse_position_state((pos.meta if pos else {}) or {})
        if pos and current_qty > 0:
            updated_state = with_hwm_update(state=position_state, last_price=float(snapshot.last_price))
            if updated_state.hwm_price != position_state.hwm_price:
                new_meta = dict(pos.meta or {})
                new_meta.update(updated_state.as_meta_patch())
                repo.upsert_position(
                    DbPosition(
                        symbol=pos.symbol,
                        ts_updated=_utcnow(),
                        qty=pos.qty,
                        avg_entry_price=pos.avg_entry_price,
                        unrealized_pnl=pos.unrealized_pnl,
                        stop_price=pos.stop_price,
                        take_profit=pos.take_profit,
                        meta=new_meta,
                    )
                )
                pos = repo.fetch_position(symbol)
                position_state = parse_position_state((pos.meta if pos else {}) or {})
        pos_value = float(current_qty) * float(snapshot.mid_price)
        equity = float(cash) + float(pos_value)
        current_pct = (pos_value / equity * 100.0) if equity > 0 else 0.0
        capital_profile = resolve_capital_policy(
            rules_raw=raw_rules,
            equity_krw=float(equity),
            default_target_position_pct=float((raw_rules.get("governance") or {}).get("default_target_position_pct") or 10.0)
            if isinstance(raw_rules, dict)
            else 10.0,
            max_position_pct_per_symbol=float(rules.risk.max_position_pct_per_symbol),
            cooldown_minutes_after_trigger=int(rules.risk.cooldown_minutes_after_trigger),
        )
        effective_target_cap = min(
            float(capital_profile.max_target_position_pct),
            float(capital_profile.max_position_pct_per_symbol),
            float(rules.risk.max_position_pct_per_symbol),
        )
        daily_loss_pct = 0.0
        daily_trades_count = 0
        try:
            today_kst = _utcnow().astimezone(KST).date().isoformat()
            latest_daily = (repo.fetch_pnl_daily(limit=1) or [None])[0]
            if isinstance(latest_daily, dict) and str(latest_daily.get("day") or "") == today_kst:
                realized = float(latest_daily.get("realized_pnl") or 0.0)
                daily_trades_count = int(float(latest_daily.get("trades_count") or 0.0))
                if realized < 0 and float(equity) > 0:
                    daily_loss_pct = abs(float(realized)) / float(equity) * 100.0
        except Exception:
            daily_loss_pct = 0.0
            daily_trades_count = 0
        plan_target_pct = None
        raw_plan_target_pct = None
        plan_activation_gate: dict[str, Any] = {}
        plan_allowed_actions: dict[str, Any] = {}
        plan_runtime_entry_policy: dict[str, Any] = {}
        plan_activation_decision: str | None = None
        plan_activation_decision_effective: str | None = None
        plan_execution_plan: dict[str, Any] = {}
        try:
            if plan and _trade_plan_is_active(plan):
                if isinstance(plan.get("activation_gate"), dict):
                    plan_activation_gate = dict(plan.get("activation_gate") or {})
                plan_activation_decision = str(plan_activation_gate.get("decision") or "").strip().upper() or None
                plan_activation_decision_effective = (
                    str(plan_activation_gate.get("decision_effective") or "").strip().upper() or None
                )
                plan_inter_slot_realtime = bool(plan_activation_gate.get("inter_slot_realtime_mode")) or bool(
                    inter_slot_realtime_mode
                )
                if isinstance(plan.get("execution_plan"), dict):
                    plan_execution_plan = dict(plan.get("execution_plan") or {})
                if isinstance(plan.get("allowed_actions"), dict):
                    plan_allowed_actions = dict(plan.get("allowed_actions") or {})
                if isinstance(plan.get("runtime_entry_policy"), dict):
                    plan_runtime_entry_policy = dict(plan.get("runtime_entry_policy") or {})
                execution_target = None
                if isinstance(plan_execution_plan.get("final_numbers"), dict):
                    execution_target = (plan_execution_plan.get("final_numbers") or {}).get("target_position_pct")
                raw_plan_target_pct = (
                    float(execution_target)
                    if execution_target is not None
                    else float(plan.get("target_position_pct"))
                )
                plan_target_pct = float(raw_plan_target_pct)
                if str(plan_activation_decision_effective or plan_activation_decision or "").upper() == "HOLD":
                    conditional_hold_target_pct = _as_float(
                        plan_activation_gate.get("conditional_hold_target_pct"),
                        default=float(raw_plan_target_pct or 0.0),
                    )
                    plan_target_pct = 0.0
                    raw_plan_target_pct = 0.0
                    if plan_inter_slot_realtime:
                        # Conditional HOLD keeps the policy cap alive between meetings.
                        # Live keeps buy=False and lets runtime promotion / exploration decide.
                        # Paper may open buy=True for data collection.
                        plan_target_pct = float(max(0.0, conditional_hold_target_pct))
                        raw_plan_target_pct = float(max(0.0, conditional_hold_target_pct))
                        if universe_mode == "paper" and realtime_between_meetings:
                            plan_allowed_actions["buy"] = True
                        if "sell" not in plan_allowed_actions:
                            plan_allowed_actions["sell"] = True
                        plan_activation_gate["inter_slot_realtime_mode"] = True
                        if plan_runtime_entry_policy:
                            plan_runtime_entry_policy["runtime_entry_allowed"] = True
                            plan_runtime_entry_policy["runtime_promotion_enabled"] = not bool(
                                plan_allowed_actions.get("buy")
                            )
        except Exception:
            plan_target_pct = None
            raw_plan_target_pct = None
            plan_allowed_actions = {}
            plan_runtime_entry_policy = {}
        if plan_target_pct is not None:
            plan_target_pct = max(0.0, min(float(plan_target_pct), float(effective_target_cap)))
        if manage_open_position_only:
            # Meeting plan switched symbol while we still hold another asset.
            # In this mode, avoid adding to the orphan position and only allow risk-reducing exits.
            plan_allowed_actions = {"buy": False, "sell": True}
        quote_ts = _utcnow()
        repo.insert_market_quote(
            ts=quote_ts,
            symbol=symbol,
            best_bid=snapshot.best_bid,
            best_ask=snapshot.best_ask,
            mid_price=snapshot.mid_price,
            spread_abs=snapshot.best_ask - snapshot.best_bid,
            spread_bps=snapshot.spread_bps,
            source="upbit_public",
        )
        repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=quote_ts,
                event_type="MARKET_SNAPSHOT",
                entity_type="market_quotes",
                entity_id=f"{symbol}:{int(snapshot.ts_ms)}",
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={
                    "symbol": symbol,
                    "last_price": snapshot.last_price,
                    "best_bid": snapshot.best_bid,
                    "best_ask": snapshot.best_ask,
                    "mid_price": snapshot.mid_price,
                    "spread_bps": snapshot.spread_bps,
                    "rules_hash": rules_hash,
                    "universe_id": universe_id,
                },
            )
        )

        candles = fetch_candles_minutes(symbol, unit=tf_min, count=200)
        highs = [float(c["high_price"]) for c in candles]
        lows = [float(c["low_price"]) for c in candles]
        closes = [float(c["trade_price"]) for c in candles]
        volumes = [float(c["candle_acc_trade_volume"]) for c in candles]
        feat = build_feature_snapshot_from_candles(highs=highs, lows=lows, closes=closes, volumes=volumes)
        candles_1m = fetch_candles_minutes(symbol, unit=1, count=int(alpha_lookback))
        opens_1m = [float(c.get("opening_price") or c["trade_price"]) for c in candles_1m]
        highs_1m = [float(c["high_price"]) for c in candles_1m]
        lows_1m = [float(c["low_price"]) for c in candles_1m]
        closes_1m = [float(c["trade_price"]) for c in candles_1m]
        volumes_1m = [float(c["candle_acc_trade_volume"]) for c in candles_1m]
        turnovers_1m = [float(c.get("candle_acc_trade_price") or 0.0) for c in candles_1m]
        alpha_features = build_alpha_features_from_1m_candles(
            opens=opens_1m,
            highs=highs_1m,
            lows=lows_1m,
            closes=closes_1m,
            volumes=volumes_1m,
            turnover_values=turnovers_1m,
            ema_fast=int(alpha_cfg.ema_fast),
            ema_slow=int(alpha_cfg.ema_slow),
            ret_short_bars=int(alpha_cfg.ret_short_mins),
            ret_long_bars=int(alpha_cfg.ret_long_mins),
        )
        feat_map = asdict(feat)
        feat_map.update(alpha_features)
        edge_calibration_cfg = load_edge_calibration_config(rules_raw=raw_rules)
        edge_calibration_summary: dict[str, Any] = {"enabled": False, "symbol": symbol, "sample_count": 0}
        edge_calibration_dataset = None
        if bool(edge_calibration_cfg.get("enabled", False)):
            cache_key = str(symbol).strip().upper()
            cache_row = edge_calibration_state.get(cache_key) or {}
            refresh_minutes = max(1, int(_as_int(edge_calibration_cfg.get("refresh_minutes"), default=10)))
            cache_built_at = cache_row.get("built_at")
            cache_rules_hash = str(cache_row.get("rules_hash") or "")
            cache_valid = isinstance(cache_built_at, datetime) and (_utcnow() - cache_built_at) < timedelta(minutes=refresh_minutes)
            if not cache_valid or cache_rules_hash != str(rules_hash):
                calibration_outcomes = repo.fetch_decision_outcomes(
                    symbol=symbol,
                    limit=max(200, int(_as_int(edge_calibration_cfg.get("outcome_limit"), default=5000))),
                )
                calibration_trades = repo.fetch_realized_trades(
                    symbol=symbol,
                    limit=max(200, int(_as_int(edge_calibration_cfg.get("trade_limit"), default=5000))),
                )
                calibration_decision_ids = [
                    str(row.get("decision_id") or "").strip()
                    for row in list(calibration_outcomes or [])
                    if str(row.get("decision_id") or "").strip()
                ]
                edge_calibration_dataset = build_edge_calibration_dataset(
                    events=repo.fetch_events(
                        event_type="SAFE_DECISION",
                        entity_type="decisions",
                        symbol=symbol,
                        entity_ids=calibration_decision_ids,
                        limit=max(
                            max(200, len(calibration_decision_ids) + 20),
                            int(_as_int(edge_calibration_cfg.get("safe_decision_limit"), default=5000)),
                        ),
                    ),
                    outcomes=calibration_outcomes,
                    trades=calibration_trades,
                    symbol=symbol,
                    now_utc=_utcnow(),
                    rules_raw=raw_rules,
                )
                edge_calibration_state[cache_key] = {
                    "built_at": _utcnow(),
                    "rules_hash": str(rules_hash),
                    "dataset": edge_calibration_dataset,
                }
            else:
                edge_calibration_dataset = cache_row.get("dataset")
            if edge_calibration_dataset is not None and hasattr(edge_calibration_dataset, "as_runtime_summary"):
                edge_calibration_summary = dict(edge_calibration_dataset.as_runtime_summary())
        repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=_utcnow(),
                event_type="FEATURE_SNAPSHOT",
                entity_type="features",
                entity_id=f"{symbol}:{decision_id}",
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={
                    "symbol": symbol,
                    "decision_id": str(decision_id),
                    "features": feat_map,
                    "rules_hash": rules_hash,
                    "universe_id": universe_id,
                },
            )
        )

        pause_state = bool(repo.fetch_pause_state().get("paused") or False)
        latest_recon = repo.fetch_latest_reconciliation(symbol=symbol)
        recon_status = str((latest_recon or {}).get("status") or "OK").upper()
        ops = {"rate_limit_alert": False, "reconciliation_status": recon_status, "pause_state": pause_state}
        learning_feedback = _latest_symbol_learning_feedback(repo=repo, symbol=symbol)
        research_signal = _latest_research_signal(repo=repo, symbol=symbol)
        runtime_controls = build_runtime_controls(
            rules_raw=raw_rules,
            account={
                "daily_loss_pct": float(daily_loss_pct),
                "daily_trades_count": int(daily_trades_count),
                "cash_krw": float(cash),
                "equity_krw": float(equity),
                "position_value_krw": float(pos_value),
                "capital_profile": capital_profile.as_dict(),
            },
            risk_limits={
                "max_daily_loss_pct": float(rules.risk.max_daily_loss_pct),
                "max_slippage_bps": float(rules.cost_guard.max_predicted_slippage_bps),
                "max_spread_bps_entry": float(rules.cost_guard.max_spread_bps_entry),
            },
            learning_feedback=learning_feedback,
            research_signal=research_signal,
        )
        payload = build_common_payload(
            run_id=run_id,
            rule_version_id=rule_version_id,
            decision_id=decision_id,
            snapshot=snapshot,
            features=feat_map,
            ops=ops,
            context={
                "account": {
                    "daily_loss_pct": float(daily_loss_pct),
                    "daily_trades_count": int(daily_trades_count),
                    "cash_krw": float(cash),
                    "equity_krw": float(equity),
                    "position_value_krw": float(pos_value),
                    "capital_profile": capital_profile.as_dict(),
                },
                "risk_limits": {
                    "max_daily_loss_pct": float(rules.risk.max_daily_loss_pct),
                    "max_slippage_bps": float(rules.cost_guard.max_predicted_slippage_bps),
                    "max_spread_bps_entry": float(rules.cost_guard.max_spread_bps_entry),
                },
                "position": {
                    "current_qty": float(current_qty),
                    "current_position_pct": float(current_pct),
                    "avg_entry_price": float(pos.avg_entry_price) if (pos and pos.avg_entry_price) else None,
                },
                "position_state": position_state.to_context(now=_utcnow()),
                "runtime": {
                    "rules_hash": str(rules_hash),
                    "universe_id": str(universe_id),
                    "universe_mode": str(universe_mode),
                },
                "trade_plan": {
                    "slot_key": plan.get("slot_key") if plan else None,
                    "time_horizon": plan.get("time_horizon") if plan else None,
                    "target_position_pct": plan_target_pct,
                    "raw_target_position_pct": (raw_plan_target_pct if raw_plan_target_pct is not None else (plan.get("target_position_pct") if plan else None)),
                    "valid_to_kst": plan.get("valid_to_kst") if plan else None,
                    "allowed_actions": dict(plan_allowed_actions),
                    "execution_scope": (
                        "MANAGE_OPEN_POSITION_ONLY"
                        if manage_open_position_only
                        else ("INTER_SLOT_REALTIME" if inter_slot_realtime_mode else "PLAN_SYMBOL")
                    ),
                    "symbol_source": str(symbol_source),
                    "plan_symbol": plan_symbol,
                    "activation_gate": dict(plan_activation_gate or {}),
                    "execution_plan": dict(plan_execution_plan or {}),
                },
                "learning_feedback": dict(learning_feedback),
                "research_signal": dict(research_signal),
                "runtime_controls": dict(runtime_controls),
                "edge_calibration": dict(edge_calibration_summary),
                "_edge_calibration_dataset": edge_calibration_dataset,
                "entry_confirmation": {
                    "required_bars": int(entry_confirm_bars),
                    "current_streak": int(_as_int(entry_confirm_state.get(symbol), default=0)),
                },
            },
        )

        # Agents (opinion-only)
        market = market_agent_opine(payload, rules=rules)
        regime = regime_agent_opine(payload, rules=rules)
        risk = risk_agent_opine(payload, rules=rules)
        ops_op = ops_agent_opine(payload)
        if current_qty > 0:
            entry_confirm_state[symbol] = 0
        elif int(entry_confirm_bars) > 1:
            prev_streak = int(_as_int(entry_confirm_state.get(symbol), default=0))
            if market.signal == "LONG" and bool(market.entry_allowed):
                next_streak = prev_streak + 1
                entry_confirm_state[symbol] = int(next_streak)
                if next_streak < int(entry_confirm_bars):
                    pending_reason = dict(market.reason or {})
                    pending_reason["entry_confirm_bars"] = int(entry_confirm_bars)
                    pending_reason["entry_confirm_streak"] = int(next_streak)
                    market = replace(
                        market,
                        signal="HOLD",
                        confidence=min(0.60, float(market.confidence)),
                        target_position_pct=0.0,
                        signal_target_pct=0.0,
                        entry_allowed=False,
                        reason_codes=[ReasonCode.RG_CAP_PENDING.value],
                        reason=pending_reason,
                    )
            else:
                entry_confirm_state[symbol] = 0

        now = _utcnow()

        def store_agent_opinion(agent_name: str, signal: str, confidence: float, raw: dict[str, Any], reason_codes: list[str]) -> None:
            opinion_id = uuid.uuid4()
            repo.insert_agent_opinion(
                DbAgentOpinion(
                    opinion_id=opinion_id,
                    ts=now,
                    symbol=symbol,
                    agent_name=agent_name,
                    signal=signal,
                    confidence=confidence,
                    horizon=timeframe_entry,
                    features=payload.get("features") or {},
                    reason={"reason_codes": reason_codes},
                    raw_payload=raw,
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                )
            )
            repo.insert_event(
                DbEvent(
                    event_id=uuid.uuid4(),
                    ts=now,
                    event_type="AGENT_OPINION",
                    entity_type="agent_opinions",
                    entity_id=str(opinion_id),
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                    payload={
                        "symbol": symbol,
                        "decision_id": str(decision_id),
                        "rules_hash": rules_hash,
                        "universe_id": universe_id,
                        "agent_name": agent_name,
                        "opinion_id": str(opinion_id),
                        "opinion": raw,
                    },
                )
            )

        store_agent_opinion(
            "market_agent",
            market.signal,
            market.confidence,
            asdict(market),
            list(market.reason_codes),
        )
        store_agent_opinion(
            "regime_agent",
            "NONE",
            1.0,
            asdict(regime),
            list(regime.reason_codes),
        )
        store_agent_opinion(
            "risk_agent",
            "NONE",
            1.0,
            asdict(risk),
            list(risk.reason_codes),
        )
        store_agent_opinion(
            "ops_agent",
            "NONE",
            1.0,
            asdict(ops_op),
            list(ops_op.reason_codes),
        )

        runtime_activation_gate = dict(plan_activation_gate or {})
        runtime_execution_plan = dict(plan_execution_plan or {})
        runtime_allowed_actions = dict(plan_allowed_actions)
        runtime_entry_policy = dict(plan_runtime_entry_policy or {})
        runtime_target_pct = plan_target_pct
        runtime_control_cap = _as_float(runtime_controls.get("max_position_pct"), default=runtime_target_pct)
        if runtime_target_pct is not None and runtime_control_cap > 0:
            runtime_target_pct = min(float(runtime_target_pct), float(runtime_control_cap))
        if not bool(runtime_controls.get("buy_enabled", True)):
            runtime_allowed_actions["buy"] = False
            runtime_activation_gate["runtime_buy_blocked"] = True
            runtime_activation_gate["runtime_reason_codes"] = list(runtime_controls.get("reason_codes") or [])
        runtime_decision_effective = (
            str(runtime_activation_gate.get("decision_effective") or plan_activation_decision_effective or "").strip().upper()
            or str(runtime_activation_gate.get("decision") or plan_activation_decision or "").strip().upper()
            or None
        )
        runtime_micro_cfg = (
            (((raw_rules.get("governance") or {}).get("micro_mode") or {}))
            if isinstance(raw_rules, Mapping)
            else {}
        )
        runtime_entry_policy.setdefault(
            "runtime_entry_allowed",
            bool(runtime_activation_gate.get("inter_slot_realtime_mode")),
        )
        runtime_entry_policy.setdefault(
            "runtime_promotion_enabled",
            bool(runtime_entry_policy.get("runtime_entry_allowed")) and not bool(runtime_allowed_actions.get("buy")),
        )
        runtime_activation_gate, runtime_entry_policy, live_learning_policy_applied = _apply_live_learning_runtime_policy_compat(
            raw_rules=raw_rules,
            universe_mode=str(universe_mode),
            runtime_activation_gate=runtime_activation_gate,
            runtime_entry_policy=runtime_entry_policy,
            runtime_target_pct=runtime_target_pct,
        )
        if live_learning_policy_applied:
            policy_cap_target_pct = _as_float(runtime_entry_policy.get("policy_cap_target_pct"), default=0.0)
            if policy_cap_target_pct > 0.0:
                runtime_target_pct = min(float(policy_cap_target_pct), float(effective_target_cap))
                runtime_allowed_actions.setdefault("sell", True)
        runtime_entry_policy.setdefault(
            "execution_authority",
            "realtime_loop" if bool(runtime_entry_policy.get("runtime_entry_allowed")) else "meeting_plan",
        )
        runtime_entry_policy.setdefault("entry_timing_owner", str(runtime_entry_policy.get("execution_authority") or "meeting_plan"))
        runtime_entry_policy.setdefault(
            "entry_objective",
            "profit-first" if universe_mode == "live" else "feedback-loop",
        )
        runtime_entry_policy.setdefault(
            "exploration_enabled",
            bool(runtime_micro_cfg.get("allow_live_exploration", False)) if universe_mode == "live" else True,
        )
        runtime_entry_policy.setdefault(
            "profit_floor_bps",
            float(_as_float(runtime_micro_cfg.get("live_profit_floor_bps"), default=1.0))
            if universe_mode == "live"
            else 0.0,
        )
        runtime_entry_policy.setdefault(
            "profit_required_margin_bps",
            float(_as_float(runtime_micro_cfg.get("live_profit_required_margin_bps"), default=0.5))
            if universe_mode == "live"
            else 0.0,
        )
        runtime_entry_policy.setdefault("meeting_buy_flag", bool(plan_allowed_actions.get("buy")))
        runtime_entry_policy.setdefault("meeting_sell_flag", bool(plan_allowed_actions.get("sell")))
        runtime_entry_policy.setdefault(
            "policy_cap_target_pct",
            float(_as_float(runtime_target_pct, default=0.0)),
        )

        def emit_cap_event(event_type: str, payload_map: Mapping[str, Any]) -> None:
            repo.insert_event(
                DbEvent(
                    event_id=uuid.uuid4(),
                    ts=_utcnow(),
                    event_type=event_type,
                    entity_type="trade_plans",
                    entity_id=f"{symbol}:{str(plan.get('slot_key') if plan else '')}",
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                    payload=dict(payload_map),
                )
            )

        cap_cfg = _resolve_cap_config(runtime_activation_gate)
        if "conditional_activation" not in runtime_activation_gate:
            runtime_activation_gate["conditional_activation"] = dict(cap_cfg)
        hold_mode = str(runtime_activation_gate.get("hold_mode") or "").strip().upper()
        if not hold_mode:
            hold_mode = "HOLD_CONDITIONAL" if bool(cap_cfg.get("enabled")) else "HOLD_STATIC"
            runtime_activation_gate["hold_mode"] = hold_mode

        cap_state_key = f"{symbol}:{str(plan.get('slot_key') if plan else '')}"
        cap_state = cap_runtime_state.get(cap_state_key) or {}
        required_passes = _cap_required_passes(
            sustain_seconds=int((cap_cfg.get("conditions") or {}).get("sustain_seconds") or 180),
            loop_interval_seconds=int(decision_interval_sec),
        )
        promotion_cfg = (cap_cfg.get("promotion") or {}) if isinstance(cap_cfg.get("promotion"), Mapping) else {}
        configured_score_threshold = _as_float(promotion_cfg.get("promotion_score_threshold"), default=0.0)
        score_threshold = float(
            max(
                1.0,
                float(configured_score_threshold if configured_score_threshold > 0.0 else float(required_passes)),
            )
        )
        pass_score_weight = float(max(0.1, _as_float(promotion_cfg.get("pass_score_weight"), default=1.0)))
        extra_pass_bonus = float(max(0.0, _as_float(promotion_cfg.get("extra_pass_bonus"), default=0.15)))
        miss_decay = float(max(0.1, _as_float(promotion_cfg.get("miss_decay"), default=0.5)))
        hard_block_reset = bool(promotion_cfg.get("hard_block_reset", True))
        scoring_mode = str(promotion_cfg.get("scoring_mode") or "decay_score").strip().lower() or "decay_score"
        if (
            int(_as_int(cap_state.get("required_passes"), default=0)) != int(required_passes)
            or abs(_as_float(cap_state.get("promotion_score_threshold"), default=score_threshold) - float(score_threshold)) > 1e-9
        ):
            cap_state = {
                "consecutive_passes": 0,
                "promotion_score": 0.0,
                "promotion_score_threshold": float(score_threshold),
                "promoted_at": None,
                "promote_expires_at": None,
                "required_passes": int(required_passes),
            }

        hard_gate_blocked = bool(pause_state) or str(recon_status or "").upper() == "FAIL" or bool(
            ops.get("rate_limit_alert")
        ) or bool(risk.veto) or bool(ops_op.veto)
        cap_runtime_payload = {
            "last_eval_at": _utcnow().astimezone(KST).isoformat(),
            "consecutive_passes": int(_as_int(cap_state.get("consecutive_passes"), default=0)),
            "required_passes": int(required_passes),
            "promotion_score": float(_as_float(cap_state.get("promotion_score"), default=0.0)),
            "promotion_score_threshold": float(score_threshold),
            "scoring_mode": str(scoring_mode),
            "promoted_at": cap_state.get("promoted_at"),
            "promote_expires_at": cap_state.get("promote_expires_at"),
        }

        if (
            plan
            and str(runtime_decision_effective or "").upper() == "HOLD"
            and str(hold_mode).upper() == "HOLD_CONDITIONAL"
            and bool(cap_cfg.get("enabled"))
        ):
            cond = (cap_cfg.get("conditions") or {}) if isinstance(cap_cfg.get("conditions"), Mapping) else {}
            promotion = (cap_cfg.get("promotion") or {}) if isinstance(cap_cfg.get("promotion"), Mapping) else {}
            now_utc = _utcnow()
            promote_expires_at = _parse_dt(str(cap_state.get("promote_expires_at") or ""))
            promotion_active = promote_expires_at is not None and now_utc < promote_expires_at
            if promotion_active and hard_gate_blocked:
                promotion_active = False
                cap_state["promoted_at"] = None
                cap_state["promote_expires_at"] = None
                cap_state["consecutive_passes"] = 0
                cap_state["promotion_score"] = 0.0 if hard_block_reset else max(
                    0.0,
                    float(_as_float(cap_state.get("promotion_score"), default=0.0)) - float(miss_decay),
                )
            if promote_expires_at is not None and now_utc >= promote_expires_at:
                cap_state["promoted_at"] = None
                cap_state["promote_expires_at"] = None
                cap_state["consecutive_passes"] = 0
                cap_state["promotion_score"] = 0.0
                promotion_active = False
                emit_cap_event(
                    "CAP_PROMOTION_EXPIRED",
                    {
                        "symbol": symbol,
                        "slot_key": plan.get("slot_key") if plan else None,
                        "expired_at": now_utc.astimezone(KST).isoformat(),
                    },
                )

            effective_min_alpha = resolve_effective_cap_min_alpha(
                configured_min_alpha=_as_float(cond.get("min_alpha"), default=0.75),
                edge_calibration=edge_calibration_summary,
            )
            alpha_condition_passed = (
                float(market.expected_net_edge_bps) >= float(market.min_edge_required_bps)
                if bool(edge_calibration_summary.get("enabled", False))
                else float(market.alpha) >= float(effective_min_alpha)
            )
            cond_results = {
                "alpha": bool(alpha_condition_passed),
                "spread": float(snapshot.spread_bps) <= _as_float(cond.get("max_spread_bps"), default=1.5),
                "vol_z": _as_float(feat_map.get("vol_zscore"), default=0.0) >= _as_float(cond.get("min_vol_z"), default=0.0),
                "atr": _as_float(feat_map.get("atr_pct"), default=0.0) >= _as_float(cond.get("min_atr_pct"), default=0.08),
            }
            pass_count = sum(1 for v in cond_results.values() if bool(v))
            min_pass = max(1, _as_int(cond.get("min_pass_conditions"), default=3))
            require_alpha_confirm = bool(cond.get("require_alpha_confirm", True))
            progress_pass_count, mandatory_conditions_passed = _cap_progress_pass_count(
                cond_results=cond_results,
                min_pass=min_pass,
                require_alpha_confirm=require_alpha_confirm,
            )

            if hard_gate_blocked:
                cap_state["consecutive_passes"] = 0
                cap_state["promotion_score"] = 0.0 if hard_block_reset else max(
                    0.0,
                    float(_as_float(cap_state.get("promotion_score"), default=0.0)) - float(miss_decay),
                )
                emit_cap_event(
                    "CAP_BLOCKED_BY_HARD_GATE",
                    {
                        "symbol": symbol,
                        "slot_key": plan.get("slot_key") if plan else None,
                        "reason_code": "RG_CAP_BLOCKED",
                        "hard_gate": {
                            "pause_state": bool(pause_state),
                            "reconciliation_status": str(recon_status),
                            "rate_limit_alert": bool(ops.get("rate_limit_alert")),
                            "risk_veto": bool(risk.veto),
                            "ops_veto": bool(ops_op.veto),
                        },
                    },
                )
            else:
                current_score = float(_as_float(cap_state.get("promotion_score"), default=0.0))
                if progress_pass_count >= min_pass:
                    cap_state["consecutive_passes"] = int(_as_int(cap_state.get("consecutive_passes"), default=0)) + 1
                    score_gain = float(pass_score_weight) + float(extra_pass_bonus) * float(
                        max(0, progress_pass_count - min_pass)
                    )
                    cap_state["promotion_score"] = min(float(score_threshold), float(current_score + score_gain))
                else:
                    cap_state["consecutive_passes"] = 0
                    shortfall = max(1, int(min_pass - progress_pass_count))
                    cap_state["promotion_score"] = max(0.0, float(current_score) - float(miss_decay) * float(shortfall))

                if (not promotion_active) and float(_as_float(cap_state.get("promotion_score"), default=0.0)) >= float(score_threshold):
                    promotion_ttl_minutes = max(1, _as_int(promotion.get("promotion_ttl_minutes"), default=120))
                    promote_expires = now_utc + timedelta(minutes=promotion_ttl_minutes)
                    cap_state["promoted_at"] = now_utc.astimezone(KST).isoformat()
                    cap_state["promote_expires_at"] = promote_expires.astimezone(KST).isoformat()
                    promotion_active = True
                    emit_cap_event(
                        "CAP_PROMOTED_TO_PAPER",
                        {
                            "symbol": symbol,
                            "slot_key": plan.get("slot_key") if plan else None,
                            "reason_code": "RG_CAP_PROMOTED",
                            "promotion_ttl_minutes": promotion_ttl_minutes,
                            "target_position_pct_cap": _as_float(
                                promotion.get("target_position_pct_cap"),
                                default=3.0,
                            ),
                            "promotion_score": float(_as_float(cap_state.get("promotion_score"), default=0.0)),
                            "promotion_score_threshold": float(score_threshold),
                            "conditions": cond_results,
                            "progress_pass_count": int(progress_pass_count),
                            "mandatory_conditions_passed": bool(mandatory_conditions_passed),
                        },
                    )

            emit_cap_event(
                "CAP_EVALUATED",
                {
                    "symbol": symbol,
                    "slot_key": plan.get("slot_key") if plan else None,
                    "hold_mode": hold_mode,
                    "enabled": bool(cap_cfg.get("enabled")),
                    "reason_code": "RG_CAP_PENDING",
                    "hard_gate_blocked": bool(hard_gate_blocked),
                    "conditions": cond_results,
                    "effective_min_alpha": float(effective_min_alpha),
                    "alpha_gate_mode": (
                        "after_cost_calibrated"
                        if bool(edge_calibration_summary.get("enabled", False))
                        else "raw_alpha_threshold"
                    ),
                    "pass_count": int(pass_count),
                    "progress_pass_count": int(progress_pass_count),
                    "min_pass_conditions": int(min_pass),
                    "mandatory_conditions": ["alpha"] if bool(require_alpha_confirm) else [],
                    "mandatory_conditions_passed": bool(mandatory_conditions_passed),
                    "consecutive_passes": int(_as_int(cap_state.get("consecutive_passes"), default=0)),
                    "required_passes": int(required_passes),
                    "promotion_score": float(_as_float(cap_state.get("promotion_score"), default=0.0)),
                    "promotion_score_threshold": float(score_threshold),
                    "scoring_mode": str(scoring_mode),
                    "promotion_active": bool(promotion_active),
                },
            )

            if promotion_active and not hard_gate_blocked:
                runtime_decision_effective = "PAPER"
                runtime_activation_gate["decision_effective"] = "PAPER"
                runtime_activation_gate["cap_promoted"] = True
                runtime_allowed_actions = {"buy": True, "sell": True}
                runtime_entry_policy["promotion_active"] = True
                execution_target = None
                if isinstance(runtime_execution_plan.get("final_numbers"), Mapping):
                    execution_target = _as_float(
                        (runtime_execution_plan.get("final_numbers") or {}).get("target_position_pct"),
                        default=0.0,
                    )
                promo_cap = _as_float(promotion.get("target_position_pct_cap"), default=3.0)
                runtime_target_pct = float(min(float(execution_target) if execution_target and execution_target > 0 else promo_cap, promo_cap))
                runtime_target_pct = max(0.0, runtime_target_pct)
                if not isinstance(runtime_execution_plan.get("final_numbers"), Mapping):
                    runtime_execution_plan["final_numbers"] = {}
                runtime_execution_plan["final_numbers"] = dict(runtime_execution_plan.get("final_numbers") or {})
                runtime_execution_plan["final_numbers"]["target_position_pct"] = float(runtime_target_pct)
            else:
                runtime_activation_gate["cap_promoted"] = False
                runtime_entry_policy["promotion_active"] = False

            cap_runtime_payload = {
                "last_eval_at": _utcnow().astimezone(KST).isoformat(),
                "consecutive_passes": int(_as_int(cap_state.get("consecutive_passes"), default=0)),
                "required_passes": int(required_passes),
                "promotion_score": float(_as_float(cap_state.get("promotion_score"), default=0.0)),
                "promotion_score_threshold": float(score_threshold),
                "scoring_mode": str(scoring_mode),
                "promoted_at": cap_state.get("promoted_at"),
                "promote_expires_at": cap_state.get("promote_expires_at"),
            }
            runtime_activation_gate["cap_runtime"] = dict(cap_runtime_payload)
            cap_runtime_state[cap_state_key] = dict(cap_state)
        else:
            runtime_activation_gate["cap_runtime"] = dict(cap_runtime_payload)

        # Safe Judge decision
        payload["context"]["trade_plan"] = {
            **dict(payload.get("context", {}).get("trade_plan") or {}),
            "target_position_pct": runtime_target_pct,
            "allowed_actions": dict(runtime_allowed_actions),
            "activation_gate": dict(runtime_activation_gate),
            "execution_plan": dict(runtime_execution_plan),
            "runtime_entry_policy": dict(runtime_entry_policy),
        }
        safe = safe_judge_decide(
            payload,
            rules=rules,
            market=_market_input_for_safe_judge(market),
            regime={"trade_allowed": regime.trade_allowed, "reason_codes": list(regime.reason_codes or [])},
            risk={"veto": risk.veto, "reason_codes": list(risk.reason_codes or [])},
            ops={"veto": ops_op.veto, "reason_codes": list(ops_op.reason_codes or [])},
        )
        repo.insert_decision(
            DbDecision(
                decision_id=decision_id,
                ts=now,
                symbol=symbol,
                judge_type="SAFE",
                action=safe.action,
                score=safe.score,
                confidence=safe.confidence,
                gates=safe.gates,
                selected_reasons=safe.selected_reasons,
                rejected_reasons=safe.rejected_reasons,
                expected_cost_bps=safe.expected_cost_bps,
                expected_rr=safe.expected_rr,
                run_id=run_id,
                rule_version_id=rule_version_id,
            )
        )
        repo.insert_event(
            DbEvent(
                event_id=(safe_event_id := uuid.uuid4()),
                ts=now,
                event_type="SAFE_DECISION",
                entity_type="decisions",
                entity_id=str(decision_id),
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={
                    "symbol": symbol,
                    "decision_id": str(decision_id),
                    "rules_hash": rules_hash,
                    "universe_id": universe_id,
                    "decision": asdict(safe),
                    "trade_plan": {
                        "event_id": plan.get("event_id"),
                        "slot_key": plan.get("slot_key"),
                        "symbol": plan.get("symbol"),
                        "target_position_pct": runtime_target_pct,
                        "valid_to_kst": plan.get("valid_to_kst"),
                        "allowed_actions": dict(runtime_allowed_actions),
                        "activation_gate": dict(runtime_activation_gate),
                        "execution_plan": dict(runtime_execution_plan),
                        "runtime_entry_policy": dict(runtime_entry_policy),
                    }
                    if plan
                    else None,
                    "agent_inputs": {
                        "market": asdict(market),
                        "regime": asdict(regime),
                        "risk": asdict(risk),
                        "ops": asdict(ops_op),
                    },
                },
            )
        )
        notifier.notify_safe_decision(
            event_id=safe_event_id,
            symbol=symbol,
            action=safe.action,
            reasons=list(safe.selected_reasons),
            run_id=run_id,
            context={
                "last_price": snapshot.last_price,
                "spread_bps": snapshot.spread_bps,
                "rsi_14": feat_map.get("rsi_14"),
                "atr_pct": feat_map.get("atr_pct"),
                "vol_zscore": feat_map.get("vol_zscore"),
                "alpha": market.alpha,
                "alpha_raw": market.alpha_raw,
                "alpha_regime": market.regime,
                "expected_edge_bps": market.expected_edge_bps,
                "expected_cost_bps": market.expected_cost_bps,
                "expected_net_edge_bps": market.expected_net_edge_bps,
                "signal_target_pct": market.signal_target_pct,
                "effective_target_pct": safe.effective_target_pct,
                "exit_reason": market.exit_reason,
                "market_signal": market.signal,
                "market_confidence": market.confidence,
                "regime": regime.regime,
                "regime_trade_allowed": regime.trade_allowed,
                "risk_veto": risk.veto,
                "ops_state": ops_op.system_state,
                "ops_veto": ops_op.veto,
                "reconciliation_status": ops_op.reconciliation_status,
                "pause_state": ops.get("pause_state"),
                "trade_plan_slot_key": plan.get("slot_key") if plan else None,
                "trade_plan_target_pct": runtime_target_pct,
                "trade_plan_cooldown_minutes": _as_int(
                    ((runtime_execution_plan.get("final_numbers") or {}) if isinstance(runtime_execution_plan.get("final_numbers"), Mapping) else {}).get("cooldown_minutes"),
                    default=int(alpha_cfg.cooldown_minutes),
                ),
                "trade_plan_min_hold_seconds": _as_int(
                    ((runtime_execution_plan.get("final_numbers") or {}) if isinstance(runtime_execution_plan.get("final_numbers"), Mapping) else {}).get("min_hold_seconds"),
                    default=int(rules.risk.min_hold_seconds),
                ),
                "capital_tier": capital_profile.tier_name,
                "capital_target_cap_pct": effective_target_cap,
                "rules_hash": rules_hash,
                "universe_id": universe_id,
            },
        )

        # Paper execution
        runtime_final_numbers = (
            dict(runtime_execution_plan.get("final_numbers") or {})
            if isinstance(runtime_execution_plan.get("final_numbers"), Mapping)
            else {}
        )
        runtime_cooldown_minutes = _as_int(
            runtime_final_numbers.get("cooldown_minutes"),
            default=int(alpha_cfg.cooldown_minutes),
        )
        runtime_min_hold_seconds = _as_int(
            runtime_final_numbers.get("min_hold_seconds"),
            default=int(rules.risk.min_hold_seconds),
        )
        exec_res = executor.execute(
            run_id=run_id,
            rule_version_id=rule_version_id,
            decision_id=decision_id,
            action=safe.action,
            snapshot=snapshot,
            rules=rules,
            target_position_pct=(safe.effective_target_pct if safe.effective_target_pct is not None else runtime_target_pct),
            strategy_tag=market.strategy_tag,
            exit_reason=market.exit_reason,
            cooldown_minutes=int(runtime_cooldown_minutes),
            allow_min_order_round_up=bool(runtime_entry_policy.get("learning_mode")),
        )
        if exec_res is not None:
            notifier.notify_fill(
                event_id=exec_res.fill_event_id,
                symbol=symbol,
                side=exec_res.side,
                qty=exec_res.fill_qty,
                price=exec_res.fill_price,
                fee=exec_res.fee,
                fee_currency=(symbol.split("-", 1)[0] if "-" in symbol else "KRW"),
            )
            if exec_res.closed_trade is not None:
                ev = evaluate_closed_trade(
                    qty=exec_res.closed_trade.qty,
                    avg_entry_price=exec_res.closed_trade.avg_entry_price,
                    avg_exit_price=exec_res.closed_trade.avg_exit_price,
                    realized_pnl_krw=exec_res.closed_trade.realized_pnl,
                    fees_total_krw=exec_res.closed_trade.fees_total,
                    ts_open=exec_res.closed_trade.ts_open,
                    ts_close=exec_res.closed_trade.ts_close,
                    pnl_bps=exec_res.closed_trade.pnl_bps,
                    exit_reason=exec_res.closed_trade.exit_reason,
                    min_hold_seconds=int(runtime_min_hold_seconds),
                )
                outcome_id = uuid.uuid4()
                outcome_decision_id = exec_res.closed_trade.entry_decision_id or exec_res.closed_trade.exit_decision_id
                repo.insert_decision_outcome(
                    DbDecisionOutcome(
                        outcome_id=outcome_id,
                        decision_id=outcome_decision_id,
                        trade_id=exec_res.closed_trade.trade_id,
                        symbol=symbol,
                        ts_open=exec_res.closed_trade.ts_open,
                        ts_close=exec_res.closed_trade.ts_close,
                        outcome_label=ev.outcome_label,
                        error_type=ev.error_type,
                        root_cause=ev.root_cause,
                        evidence_refs={"order_id": exec_res.order_id, "fill_id": str(exec_res.fill_id)},
                        fix_hypothesis=ev.fix_hypothesis,
                        reviewed_by="system",
                        reviewed_at=_utcnow(),
                        run_id=run_id,
                        rule_version_id=rule_version_id,
                        meta={"paper": True, "eval": dict(ev.meta)},
                    )
                )
                repo.insert_event(
                    DbEvent(
                        event_id=uuid.uuid4(),
                        ts=_utcnow(),
                        event_type="DECISION_OUTCOME_RECORDED",
                        entity_type="decision_outcomes",
                        entity_id=str(outcome_id),
                        run_id=run_id,
                        rule_version_id=rule_version_id,
                        payload={
                            "symbol": symbol,
                            "decision_id": str(outcome_decision_id),
                            "trade_id": str(exec_res.closed_trade.trade_id),
                            "outcome_label": ev.outcome_label,
                            "error_type": ev.error_type,
                        },
                    )
                )

        if safe.action == "BUY":
            entry_confirm_state[symbol] = 0

        # AI shadow decision (stored, no execution)
        ai = ai_judge_shadow_decide(
            payload,
            rules=rules,
            market={"signal": market.signal, "confidence": market.confidence},
            regime={"trade_allowed": regime.trade_allowed},
            risk={"veto": risk.veto},
            ops={"veto": ops_op.veto},
        )
        ai_decision_id = uuid.uuid4()
        repo.insert_decision(
            DbDecision(
                decision_id=ai_decision_id,
                ts=_utcnow(),
                symbol=symbol,
                judge_type="AI",
                action=ai.action,
                score=ai.score,
                confidence=ai.confidence,
                gates={"shadow": True, "baseline": ai.meta},
                selected_reasons=ai.selected_reasons,
                rejected_reasons=ai.rejected_reasons,
                expected_cost_bps=None,
                expected_rr=None,
                run_id=run_id,
                rule_version_id=rule_version_id,
            )
        )
        repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=_utcnow(),
                event_type="AI_DECISION",
                entity_type="decisions",
                entity_id=str(ai_decision_id),
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={
                    "symbol": symbol,
                    "shadow_of": str(decision_id),
                    "rules_hash": rules_hash,
                    "universe_id": universe_id,
                    "decision": asdict(ai),
                },
            )
        )

        # Reconciliation
        check = record_reconciliation_check(repo, run_id=run_id, symbol=symbol)
        if check.status == "FAIL":
            recon_event_id = uuid.uuid4()
            repo.insert_event(
                DbEvent(
                    event_id=recon_event_id,
                    ts=_utcnow(),
                    event_type="RECONCILIATION_FAIL",
                    entity_type="reconciliation_checks",
                    entity_id=str(check.check_id),
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                    payload={"symbol": symbol, "check_id": str(check.check_id), "diff": check.diff_payload},
                )
            )
            notifier.notify_recon_fail(
                event_id=recon_event_id,
                symbol=symbol,
                diff_summary=check.diff_summary,
                run_id=run_id,
            )
            pause_event_id = uuid.uuid4()
            repo.insert_event(
                DbEvent(
                    event_id=pause_event_id,
                    ts=_utcnow(),
                    event_type="PAUSE",
                    entity_type="pause_log",
                    entity_id="AUTO",
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                    payload={"symbol": symbol, "reason_type": "RECON_FAIL"},
                )
            )
            notifier.notify_pause(event_id=pause_event_id, symbol=symbol, reason_type="RECON_FAIL", run_id=run_id)
            repo.insert_pause_log(
                DbPauseLog(
                    pause_id=uuid.uuid4(),
                    ts_pause=_utcnow(),
                    ts_resume=None,
                    reason_type="RECON_FAIL",
                    severity="HIGH",
                    auto_resumable=False,
                    resume_policy={},
                    notes="auto pause due to recon fail",
                    run_id=run_id,
                )
            )
            break

        if sleep_sec is not None:
            time.sleep(float(sleep_sec))

    if snapshot_hub is not None:
        snapshot_hub.close()
