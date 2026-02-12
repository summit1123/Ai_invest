from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from pydantic import BaseModel, Field
from zoneinfo import ZoneInfo

from ai_invest.agents.prompt_contract import (
    governance_coordinator_instructions,
    governance_critique_instructions,
    governance_ops_instructions,
    governance_quant_instructions,
    governance_research_instructions,
    governance_risk_instructions,
    governance_secretary_instructions,
)
from ai_invest.config.capital_policy import resolve_capital_policy
from ai_invest.config.llm_router import LLMRoute, llm_route_for_agent
from ai_invest.config.rules_loader import RulesConfig, load_rules
from ai_invest.market_data.features import build_feature_snapshot_from_candles
from ai_invest.market_data.upbit_public import fetch_candles_minutes, fetch_market_snapshot
from ai_invest.notifications.service import NotificationService
from ai_invest.research.rss import summarize_headlines_text
from ai_invest.storage.postgres import DbEvent, DbMeetingMessage, DbMeetingSession, PostgresRepo
from ai_invest.work.agent_work_loop import collect_latest_work_reports, run_agent_work_cycle

KST = ZoneInfo("Asia/Seoul")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_kst() -> datetime:
    return _utcnow().astimezone(KST)


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(obj)


def _clip(s: str, n: int) -> str:
    s = str(s or "")
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def _as_float(value: Any, *, default: float) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        if not s:
            return float(default)
        return float(s)
    except Exception:
        return float(default)


def _as_int(value: Any, *, default: int) -> int:
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
        if not s:
            return int(default)
        return int(float(s))
    except Exception:
        return int(default)


def should_block_prework(*, require_prework_reports: bool, prework: Mapping[str, Any]) -> bool:
    if not bool(require_prework_reports):
        return False
    missing = list(prework.get("missing") or []) if isinstance(prework, Mapping) else []
    stale = list(prework.get("stale") or []) if isinstance(prework, Mapping) else []
    return bool(missing or stale)


def _prework_refresh_slot_payload(
    *,
    slot_key: str,
    prework: Mapping[str, Any],
    selected_agents: Sequence[str],
    reason_code: str,
) -> dict[str, Any]:
    return {
        "slot_key": str(slot_key),
        "reason_code": str(reason_code),
        "missing": [str(x) for x in list(prework.get("missing") or [])],
        "stale": [str(x) for x in list(prework.get("stale") or [])],
        "selected_agents": [str(x) for x in list(selected_agents or [])],
        "max_age_minutes": int(_as_float(prework.get("max_age_minutes"), default=0.0)),
        "checked_at_utc": str(prework.get("checked_at_utc") or ""),
    }


def _recent_prework_refresh_requested(
    *,
    repo: PostgresRepo,
    slot_key: str,
    cooldown_min: int,
) -> bool:
    last = repo.fetch_event_by_entity(
        event_type="MEETING_PREWORK_REFRESH_REQUESTED",
        entity_type="meeting_slots",
        entity_id=str(slot_key),
    )
    if not isinstance(last, Mapping):
        return False
    ts = last.get("ts")
    if not isinstance(ts, datetime):
        return False
    ts_utc = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    age_min = (_utcnow() - ts_utc).total_seconds() / 60.0
    return age_min < float(max(1, int(cooldown_min)))


def ensure_prework_ready_for_slot(
    *,
    repo: PostgresRepo,
    rules_raw: Mapping[str, Any],
    slot_key: str,
) -> bool:
    gov = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    require_prework_reports = bool(gov.get("require_prework_reports", False))
    prework_max_age_min = int(_as_float(gov.get("prework_max_age_min"), default=360.0))
    refresh_cooldown_min = int(_as_float(gov.get("prework_refresh_cooldown_min"), default=5.0))
    prework_agents = ["research_agent", "quant_strategist", "risk_manager", "ops_manager"]

    prework = collect_latest_work_reports(
        repo=repo,
        agent_names=prework_agents,
        max_age_minutes=prework_max_age_min,
        include_details=True,
    )
    if not should_block_prework(require_prework_reports=require_prework_reports, prework=prework):
        return True

    selected_agents = sorted(
        set(str(x) for x in list(prework.get("missing") or []) + list(prework.get("stale") or []) if str(x).strip())
    )

    # Avoid storm: if we already requested refresh recently for this slot, wait for next scheduler tick.
    if _recent_prework_refresh_requested(repo=repo, slot_key=slot_key, cooldown_min=refresh_cooldown_min):
        return False

    repo.insert_event(
        DbEvent(
            event_id=uuid.uuid4(),
            ts=_utcnow(),
            event_type="MEETING_PREWORK_REFRESH_REQUESTED",
            entity_type="meeting_slots",
            entity_id=str(slot_key),
            run_id=None,
            rule_version_id=None,
            payload=_prework_refresh_slot_payload(
                slot_key=slot_key,
                prework=prework,
                selected_agents=selected_agents,
                reason_code="PREWORK_MISSING_OR_STALE",
            ),
        )
    )

    try:
        run_agent_work_cycle(
            repo=repo,
            rules_raw=rules_raw,
            meeting_context=f"scheduler_prework_refresh:{slot_key}",
            selected_agents=selected_agents,
        )
    except Exception as exc:
        repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=_utcnow(),
                event_type="MEETING_PREWORK_REFRESH_FAILED",
                entity_type="meeting_slots",
                entity_id=str(slot_key),
                run_id=None,
                rule_version_id=None,
                payload={
                    **_prework_refresh_slot_payload(
                        slot_key=slot_key,
                        prework=prework,
                        selected_agents=selected_agents,
                        reason_code="PREWORK_REFRESH_EXCEPTION",
                    ),
                    "error": str(exc)[:240],
                },
            )
        )
        return False

    prework2 = collect_latest_work_reports(
        repo=repo,
        agent_names=prework_agents,
        max_age_minutes=prework_max_age_min,
        include_details=True,
    )
    if should_block_prework(require_prework_reports=require_prework_reports, prework=prework2):
        repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=_utcnow(),
                event_type="MEETING_PREWORK_PENDING",
                entity_type="meeting_slots",
                entity_id=str(slot_key),
                run_id=None,
                rule_version_id=None,
                payload=_prework_refresh_slot_payload(
                    slot_key=slot_key,
                    prework=prework2,
                    selected_agents=selected_agents,
                    reason_code="PREWORK_STILL_NOT_READY",
                ),
            )
        )
        return False

    repo.insert_event(
        DbEvent(
            event_id=uuid.uuid4(),
            ts=_utcnow(),
            event_type="MEETING_PREWORK_READY",
            entity_type="meeting_slots",
            entity_id=str(slot_key),
            run_id=None,
            rule_version_id=None,
            payload=_prework_refresh_slot_payload(
                slot_key=slot_key,
                prework=prework2,
                selected_agents=selected_agents,
                reason_code="PREWORK_READY_AFTER_REFRESH",
            ),
        )
    )
    return True


def _timeframe_to_minutes(tf: str) -> int:
    tf = str(tf or "").strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    raise ValueError(f"Unsupported timeframe: {tf}")


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = str(value or "").split(":", 1)
    return int(hh), int(mm)


def _slot_dt_for_today_kst(now_kst: datetime, hhmm: str) -> datetime:
    hh, mm = _parse_hhmm(hhmm)
    return now_kst.replace(hour=hh, minute=mm, second=0, microsecond=0)


def _slot_key_for_dt(slot_dt: datetime) -> str:
    return f"{slot_dt.date().isoformat()} {slot_dt.strftime('%H:%M')}"


def default_meeting_times_kst() -> list[str]:
    # 정기 회의: 하루 3회(8시간 간격) 권장
    # 24:00은 다음날 00:00과 동일하므로 00:00/08:00/16:00으로 표현한다.
    return ["00:00", "08:00", "16:00"]


def get_meeting_times_kst(rules_raw: Mapping[str, Any]) -> list[str]:
    gov = rules_raw.get("governance") if isinstance(rules_raw, Mapping) else None
    times = (gov or {}).get("daily_meeting_times_kst") if isinstance(gov, Mapping) else None
    if isinstance(times, list) and all(isinstance(x, str) and ":" in x for x in times):
        return [x.strip() for x in times]
    return default_meeting_times_kst()


def next_slot_kst(now_kst: datetime, *, times: Sequence[str], current: str) -> datetime:
    slots = [_slot_dt_for_today_kst(now_kst, t) for t in times]
    cur_dt = _slot_dt_for_today_kst(now_kst, current)
    for s in slots:
        if s > cur_dt:
            return s
    first = _slot_dt_for_today_kst(now_kst, str(times[0]))
    return (first + timedelta(days=1)).replace(tzinfo=KST)


def score_symbol(
    *,
    symbol: str,
    snapshot: Mapping[str, Any],
    features: Mapping[str, Any],
    rsi_min: float,
    vol_min: float,
    max_spread_bps: float,
) -> float:
    # 단순 점수(데모): rsi/볼륨이 높고, 스프레드가 낮을수록 가산.
    rsi = _as_float(features.get("rsi_14"), default=50.0)
    volz = _as_float(features.get("vol_zscore"), default=0.0)
    spread = _as_float(snapshot.get("spread_bps"), default=0.0)
    score = 0.0
    score += (rsi - rsi_min) / 100.0
    score += (volz - vol_min) / 10.0
    if spread > max_spread_bps:
        score -= (spread - max_spread_bps) / 100.0
    return float(score)


class EvidenceCard(BaseModel):
    title: str = Field(..., max_length=180)
    source: str | None = Field(None, max_length=80)
    url: str | None = Field(None, max_length=500)
    published_at: str | None = Field(None, max_length=40)
    impact: str = Field(..., max_length=240, description="이슈/뉴스가 시장/리스크에 미치는 영향(1문장)")
    confidence: float = Field(0.6, ge=0.0, le=1.0)


class ResearchGovOutput(BaseModel):
    briefing: str = Field(..., max_length=500)
    evidence_cards: list[EvidenceCard] = Field(default_factory=list)
    risk_watchlist: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class AllowedActions(BaseModel):
    buy: bool = True
    sell: bool = True


class QuantPlanDraft(BaseModel):
    symbol: str = Field(..., max_length=20)
    target_position_pct: float = Field(..., ge=0.0, le=100.0)
    allowed_actions: AllowedActions = Field(default_factory=AllowedActions)
    entry_triggers: list[str] = Field(default_factory=list)
    exit_triggers: list[str] = Field(default_factory=list)
    rebalance_band_pct: float = Field(2.0, ge=0.0, le=20.0)
    cooldown_minutes: int = Field(60, ge=0, le=7 * 24 * 60)
    notes: str = Field(..., max_length=800)


class RiskDraft(BaseModel):
    veto: bool = False
    max_position_pct: float = Field(..., ge=0.0, le=100.0)
    max_loss_per_trade_pct: float = Field(..., ge=0.0, le=100.0)
    max_daily_loss_pct: float = Field(..., ge=0.0, le=100.0)
    required_constraints: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(..., max_length=800)


class OpsDraft(BaseModel):
    veto: bool = False
    trade_window_allowed: bool = True
    required_ops_gates: list[str] = Field(default_factory=list)
    data_quality_flags: list[str] = Field(default_factory=list)
    notes: str = Field(..., max_length=800)


class CritiqueOutput(BaseModel):
    critical_issues: list[str] = Field(default_factory=list)
    suggested_changes: list[str] = Field(default_factory=list)


class FinalTradePlan(BaseModel):
    symbol: str = Field(..., max_length=20)
    target_position_pct: float = Field(..., ge=0.0, le=100.0)
    allowed_actions: AllowedActions = Field(default_factory=AllowedActions)
    rebalance_band_pct: float = Field(2.0, ge=0.0, le=20.0)
    cooldown_minutes: int = Field(60, ge=0, le=7 * 24 * 60)
    valid_from_kst: str = Field(..., max_length=40)
    valid_to_kst: str = Field(..., max_length=40)
    constraints: dict[str, Any] = Field(default_factory=dict)
    rationale: dict[str, str] = Field(default_factory=dict, description="agent_name -> 1~3문장 근거 요약")
    evidence_refs: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    conflict_resolution: list[str] = Field(default_factory=list)
    notes: str = Field(..., max_length=1200)


class PlanIntent(BaseModel):
    mode: str = Field("hold", pattern="^(accumulate|reduce|hold|market_neutral)$")
    direction_bias: str = Field("long_only", pattern="^(long_only|short_only|both)$")
    time_horizon: str = Field("intraday", pattern="^(intraday|1d|swing)$")


class PositionPolicyRange(BaseModel):
    target_position_pct_range: tuple[float, float] = Field(default=(0.0, 0.0))
    rebalance_band_pct_range: tuple[float, float] = Field(default=(1.0, 2.0))
    cooldown_minutes_range: tuple[int, int] = Field(default=(30, 120))
    priority: str = Field("risk_sensitive", pattern="^(cost_sensitive|signal_sensitive|risk_sensitive)$")


class PlanConfidence(BaseModel):
    data_sufficiency: str = Field("low", pattern="^(low|medium|high)$")
    backtest_trades: int = 0
    backtest_window: str = "500 bars"
    paper_only_recommended: bool = True


class FinalTradePlanV2(BaseModel):
    schema_version: str = Field(default="FinalTradePlanV2@2026-02-12")
    symbol: str = Field(..., max_length=20)
    intent: PlanIntent = Field(default_factory=PlanIntent)
    position_policy: PositionPolicyRange = Field(default_factory=PositionPolicyRange)
    allowed_actions: AllowedActions = Field(default_factory=AllowedActions)
    valid_from_kst: str = Field(..., max_length=40)
    valid_to_kst: str = Field(..., max_length=40)
    constraints: dict[str, Any] = Field(default_factory=dict)
    rationale: dict[str, str] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    conflict_resolution: list[dict[str, Any]] = Field(default_factory=list)
    conditional_activation: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(..., max_length=1200)
    confidence: PlanConfidence = Field(default_factory=PlanConfidence)


class ExecutionFinalNumbers(BaseModel):
    target_position_pct: float = Field(0.0, ge=0.0, le=100.0)
    rebalance_band_pct: float = Field(1.0, ge=0.0, le=50.0)
    cooldown_minutes: int = Field(30, ge=0, le=10080)


class ExecutionPlan(BaseModel):
    schema_version: str = Field(default="ExecutionPlan@2026-02-12")
    symbol: str = Field(..., max_length=20)
    final_numbers: ExecutionFinalNumbers = Field(default_factory=ExecutionFinalNumbers)
    gates: dict[str, Any] = Field(default_factory=dict)
    sizing_rule: str = "min(max_target, signal_strength_scaled)"
    rebalance_rule: str = "rebalance if abs(curr-target) > band"
    execution_style: str = Field("post_only", pattern="^(post_only|ioc|limit_mid|market_last_resort)$")


class TradePlanSetEnvelope(BaseModel):
    schema_version: str = Field(default="TradePlanSet@2026-02-12")
    slot_key: str
    meeting_id: str
    plan_version: str
    inputs_hash: str
    created_at_kst: str
    activation_status: str
    activation_gate: dict[str, Any]
    final_trade_plan: dict[str, Any]
    final_trade_plan_v2: dict[str, Any]
    execution_plan: dict[str, Any]
    allocator_result: dict[str, Any]
    cost_model: dict[str, Any]
    paper_live_policy: dict[str, Any]


@dataclass(frozen=True)
class AgentRunMeta:
    used_llm: bool
    model: str | None
    response_id: str | None
    error: str | None


@dataclass(frozen=True)
class GovernanceOutputs:
    research: ResearchGovOutput
    quant: QuantPlanDraft
    risk: RiskDraft
    ops: OpsDraft
    critiques: dict[str, CritiqueOutput]
    final_plan: FinalTradePlan
    secretary_minutes: str
    llm_meta: dict[str, AgentRunMeta]


def _next_policy_version(repo: PostgresRepo) -> int:
    latest = repo.fetch_latest_governance_policy()
    if not latest:
        return 1
    return max(0, int(_as_float(latest.get("policy_version"), default=0.0))) + 1


def _build_policy_payload(
    *,
    policy_version: int,
    slot_key: str,
    outputs: GovernanceOutputs,
    fact_pack: Mapping[str, Any],
    activation_gate: Mapping[str, Any],
    activation_status: str | None = None,
    resolved_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    universe_sel = fact_pack.get("universe_selection") if isinstance(fact_pack.get("universe_selection"), Mapping) else {}
    prework = fact_pack.get("prework_reports") if isinstance(fact_pack.get("prework_reports"), Mapping) else {}
    quant = prework.get("quant_strategist") if isinstance(prework.get("quant_strategist"), Mapping) else {}
    quant_findings = quant.get("findings") if isinstance(quant.get("findings"), Mapping) else {}
    backtest = quant_findings.get("backtest") if isinstance(quant_findings.get("backtest"), Mapping) else {}
    plan_map = dict(resolved_plan or {})
    plan_symbol = str(plan_map.get("symbol") or outputs.final_plan.symbol)
    plan_target = _as_float(plan_map.get("target_position_pct"), default=float(outputs.final_plan.target_position_pct))
    plan_cooldown = int(_as_float(plan_map.get("cooldown_minutes"), default=float(outputs.final_plan.cooldown_minutes)))
    plan_rebalance = _as_float(plan_map.get("rebalance_band_pct"), default=float(outputs.final_plan.rebalance_band_pct))
    return {
        "policy_version": int(policy_version),
        "slot_key": str(slot_key),
        "updated_at_kst": _now_kst().isoformat(),
        "activation_status": str(activation_status or ("ACTIVE" if bool(activation_gate.get("passed")) else "PROPOSED")),
        "activation_gate": dict(activation_gate or {}),
        "quant": {
            "universe_selection": dict(universe_sel or {}),
            "backtest_engine": str((backtest or {}).get("engine") or "unknown"),
            "backtest_params": dict((backtest or {}).get("params") or {}),
        },
        "risk": {
            "max_position_pct": float(outputs.risk.max_position_pct),
            "max_loss_per_trade_pct": float(outputs.risk.max_loss_per_trade_pct),
            "max_daily_loss_pct": float(outputs.risk.max_daily_loss_pct),
        },
        "ops": {
            "required_ops_gates": list(outputs.ops.required_ops_gates),
            "trade_window_allowed": bool(outputs.ops.trade_window_allowed),
        },
        "plan": {
            "symbol": plan_symbol,
            "target_position_pct": float(plan_target),
            "cooldown_minutes": int(plan_cooldown),
            "rebalance_band_pct": float(plan_rebalance),
        },
    }


def _extract_quant_backtest_for_symbol(*, fact_pack: Mapping[str, Any], symbol: str) -> Mapping[str, Any] | None:
    prework = fact_pack.get("prework_reports") if isinstance(fact_pack.get("prework_reports"), Mapping) else {}
    quant = prework.get("quant_strategist") if isinstance(prework, Mapping) else {}
    findings = quant.get("findings") if isinstance(quant, Mapping) else {}
    backtest = findings.get("backtest") if isinstance(findings, Mapping) else {}
    ranked = backtest.get("ranked") if isinstance(backtest, Mapping) else []
    rows = [r for r in list(ranked or []) if isinstance(r, Mapping)]
    if not rows:
        return None
    sym = str(symbol or "").strip().upper()
    if sym:
        for row in rows:
            if str(row.get("symbol") or "").strip().upper() == sym:
                return row
    return rows[0]


def _stable_hash(obj: Any) -> str:
    try:
        raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    except Exception:
        raw = str(obj).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _round_num(value: Any, *, digits: int, default: float = 0.0) -> float:
    return round(_as_float(value, default=default), int(digits))


def _normalized_conditional_activation_config(
    *,
    rules_raw: Mapping[str, Any],
    force_enabled: bool | None = None,
) -> dict[str, Any]:
    gov = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    gate_cfg = (gov.get("activation_gate") or {}) if isinstance(gov, Mapping) else {}
    cap_cfg = (gate_cfg.get("conditional_activation") or {}) if isinstance(gate_cfg, Mapping) else {}
    cond = (cap_cfg.get("conditions") or {}) if isinstance(cap_cfg, Mapping) else {}
    promotion = (cap_cfg.get("promotion") or {}) if isinstance(cap_cfg, Mapping) else {}
    enabled_default = bool(cap_cfg.get("enabled", True))
    enabled = bool(force_enabled if force_enabled is not None else enabled_default)
    sustain_seconds = max(15, _as_int(cond.get("sustain_seconds"), default=180))
    min_pass_conditions = max(1, min(4, _as_int(cond.get("min_pass_conditions"), default=3)))
    return {
        "enabled": bool(enabled),
        "auto_promote_to": "PAPER",
        "conditions": {
            "min_alpha": _as_float(cond.get("min_alpha"), default=0.75),
            "max_spread_bps": _as_float(cond.get("max_spread_bps"), default=1.5),
            "min_vol_z": _as_float(cond.get("min_vol_z"), default=0.0),
            "min_atr_pct": _as_float(cond.get("min_atr_pct"), default=0.08),
            "sustain_seconds": int(sustain_seconds),
            "min_pass_conditions": int(min_pass_conditions),
        },
        "promotion": {
            "target_position_pct_cap": _as_float(promotion.get("target_position_pct_cap"), default=3.0),
            "cooldown_after_promotion_minutes": _as_int(
                promotion.get("cooldown_after_promotion_minutes"),
                default=60,
            ),
            "promotion_ttl_minutes": _as_int(promotion.get("promotion_ttl_minutes"), default=120),
        },
    }


def _activation_hold_mode(*, activation_decision_effective: str, conditional_activation: Mapping[str, Any]) -> str:
    if str(activation_decision_effective or "").upper() != "HOLD":
        return "HOLD_STATIC"
    return "HOLD_CONDITIONAL" if bool(conditional_activation.get("enabled")) else "HOLD_STATIC"


def _initial_cap_runtime(
    *,
    conditional_activation: Mapping[str, Any],
    decision_interval_sec: int,
) -> dict[str, Any]:
    cond = conditional_activation.get("conditions") if isinstance(conditional_activation, Mapping) else {}
    sustain_seconds = max(15, _as_int((cond or {}).get("sustain_seconds"), default=180))
    required_passes = max(1, int(math.ceil(float(sustain_seconds) / float(max(1, int(decision_interval_sec))))))
    return {
        "last_eval_at": None,
        "consecutive_passes": 0,
        "required_passes": int(required_passes),
        "promoted_at": None,
        "promote_expires_at": None,
    }


def _normalized_gate_checks_for_hash(checks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in list(checks or []):
        if not isinstance(raw, Mapping):
            continue
        out.append(
            {
                "name": str(raw.get("name") or ""),
                "passed": bool(raw.get("passed")),
                "required": raw.get("required"),
                "actual": raw.get("actual"),
            }
        )
    out.sort(key=lambda x: str(x.get("name") or ""))
    return out


def _normalized_evaluated_for_hash(evaluated: Sequence[Mapping[str, Any]], *, top_n: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in list(evaluated or []):
        if not isinstance(raw, Mapping):
            continue
        snap = raw.get("snapshot") if isinstance(raw.get("snapshot"), Mapping) else {}
        feat = raw.get("features") if isinstance(raw.get("features"), Mapping) else {}
        rows.append(
            {
                "symbol": str(raw.get("symbol") or "").upper(),
                "score": _round_num(raw.get("score"), digits=4),
                "spread_bps": _round_num((snap or {}).get("spread_bps"), digits=3),
                "rsi_14": _round_num((feat or {}).get("rsi_14"), digits=4),
                "vol_zscore": _round_num((feat or {}).get("vol_zscore"), digits=4),
                "atr_pct": _round_num((feat or {}).get("atr_pct"), digits=4),
            }
        )
    rows.sort(key=lambda x: (-float(x.get("score") or 0.0), str(x.get("symbol") or "")))
    return rows[: max(1, int(top_n))]


def _build_inputs_hash_payload(
    *,
    slot_key: str,
    symbol: str,
    allowed_symbols: Sequence[str],
    evaluated: Sequence[Mapping[str, Any]],
    activation_checks: Sequence[Mapping[str, Any]],
    cost_model: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "slot_key": str(slot_key),
        "symbol": str(symbol).upper(),
        "allowed_symbols": sorted({str(x).upper() for x in list(allowed_symbols or []) if str(x).strip()}),
        "evaluated_top_n": _normalized_evaluated_for_hash(evaluated),
        "activation_checks": _normalized_gate_checks_for_hash(activation_checks),
        "cost_model": {
            "fee_total_bps": _round_num(cost_model.get("fee_total_bps"), digits=4),
            "base_slippage_bps": _round_num(cost_model.get("base_slippage_bps"), digits=4),
            "spread_penalty_mult": _round_num(cost_model.get("spread_penalty_mult"), digits=4),
            "low_liquidity_penalty_bps": _round_num(cost_model.get("low_liquidity_penalty_bps"), digits=4),
        },
    }


def _bounded_pair(lo: float, hi: float, *, min_v: float, max_v: float) -> tuple[float, float]:
    lo2 = max(float(min_v), min(float(max_v), float(lo)))
    hi2 = max(float(min_v), min(float(max_v), float(hi)))
    if lo2 > hi2:
        lo2, hi2 = hi2, lo2
    return float(lo2), float(hi2)


def _bounded_pair_int(lo: int, hi: int, *, min_v: int, max_v: int) -> tuple[int, int]:
    lo2 = max(int(min_v), min(int(max_v), int(lo)))
    hi2 = max(int(min_v), min(int(max_v), int(hi)))
    if lo2 > hi2:
        lo2, hi2 = hi2, lo2
    return int(lo2), int(hi2)


def _activation_decision_from_gate(
    *,
    activation_gate: Mapping[str, Any],
    hard_plan_block: bool,
) -> str:
    if bool(hard_plan_block):
        return "HOLD"
    reason = str(activation_gate.get("reason_code") or "")
    if reason == "POLICY_GATE_PASS":
        return "LIVE"
    if reason in {"POLICY_GATE_INSUFFICIENT_DATA", "POLICY_GATE_BLOCKED", "POLICY_GATE_BACKTEST_MISSING"}:
        return "PAPER"
    return "HOLD"


def _execution_style_from_rules(*, rules_raw: Mapping[str, Any]) -> str:
    ex = (rules_raw.get("execution") or {}) if isinstance(rules_raw, Mapping) else {}
    style = str(ex.get("order_style") or "").strip().lower()
    if "post_only" in style:
        return "post_only"
    if "ioc" in style:
        return "ioc"
    if "mid" in style:
        return "limit_mid"
    if bool(ex.get("fallback_to_market")):
        return "market_last_resort"
    return "post_only"


def _to_final_trade_plan_v2(
    *,
    final_plan: FinalTradePlan,
    rules_raw: Mapping[str, Any],
    fact_pack: Mapping[str, Any],
    activation_gate: Mapping[str, Any],
    conditional_activation: Mapping[str, Any] | None = None,
) -> FinalTradePlanV2:
    target = float(final_plan.target_position_pct)
    if bool(final_plan.allowed_actions.buy):
        tgt_lo, tgt_hi = _bounded_pair(target * 0.7, max(target, target * 1.3), min_v=0.0, max_v=100.0)
    else:
        tgt_lo, tgt_hi = (0.0, 0.0)
    rb = float(final_plan.rebalance_band_pct)
    rb_lo, rb_hi = _bounded_pair(rb * 0.7, rb * 1.3, min_v=0.0, max_v=50.0)
    cd = int(final_plan.cooldown_minutes)
    cd_lo, cd_hi = _bounded_pair_int(int(cd * 0.5), int(cd * 1.5), min_v=0, max_v=10080)

    side = str(((rules_raw.get("universe") or {}).get("trade_side") or "long_only")).strip().lower()
    direction_bias = "long_only" if side in {"long_only", "long"} else ("short_only" if side in {"short_only", "short"} else "both")
    if not bool(final_plan.allowed_actions.buy) and bool(final_plan.allowed_actions.sell):
        mode = "reduce"
    elif float(target) <= 0.0:
        mode = "hold"
    elif bool(final_plan.allowed_actions.buy):
        mode = "accumulate"
    else:
        mode = "hold"

    max_spread = _as_float(
        (final_plan.constraints or {}).get("max_spread_bps"),
        default=_as_float(
            (final_plan.constraints or {}).get("max_spread_bps_entry"),
            default=_as_float(((rules_raw.get("cost_guard") or {}).get("max_spread_bps_entry")), default=8.0),
        ),
    )
    max_atr = _as_float(
        (final_plan.constraints or {}).get("max_atr_pct"),
        default=_as_float(((rules_raw.get("regime") or {}).get("volatility_block_atr_pct")), default=2.5),
    )
    min_edge = _as_float(
        (final_plan.constraints or {}).get("min_expected_edge_bps"),
        default=_as_float(((rules_raw.get("cost_guard") or {}).get("min_expected_edge_bps")), default=28.0),
    )
    uv = (rules_raw.get("universe") or {}) if isinstance(rules_raw, Mapping) else {}
    dyn = (uv.get("dynamic") or {}) if isinstance(uv, Mapping) else {}
    if bool(dyn.get("enabled")) and list(dyn.get("include_symbols") or []):
        universe_mode = "hybrid"
    elif bool(dyn.get("enabled")):
        universe_mode = "dynamic_top_turnover"
    else:
        universe_mode = "fixed_allowed_symbols"
    min_turnover = _as_float(dyn.get("min_24h_turnover_krw"), default=0.0)

    prio = "signal_sensitive"
    if not bool(final_plan.allowed_actions.buy):
        prio = "risk_sensitive"
    elif float(max_spread) <= 3.0:
        prio = "cost_sensitive"

    bt = _extract_quant_backtest_for_symbol(fact_pack=fact_pack, symbol=str(final_plan.symbol))
    bt_trades = int(_as_float((bt or {}).get("trades"), default=0.0))
    suff = "high" if bt_trades >= 30 else ("medium" if bt_trades >= 10 else "low")
    decision = str(activation_gate.get("decision") or "PAPER").upper()
    paper_only_recommended = decision != "LIVE"

    refs: list[dict[str, Any]] = []
    for raw_ref in list(final_plan.evidence_refs or [])[:20]:
        ref = str(raw_ref or "").strip()
        if not ref:
            continue
        ref_type = "link" if ref.startswith("http") else "note"
        refs.append({"ref_type": ref_type, "ref_id": ref})

    conflicts: list[dict[str, Any]] = []
    for idx, raw_conflict in enumerate(list(final_plan.conflict_resolution or [])[:20], start=1):
        c = str(raw_conflict or "").strip()
        if not c:
            continue
        conflicts.append({"topic": f"conflict_{idx}", "positions": [], "resolution": c})

    return FinalTradePlanV2(
        symbol=str(final_plan.symbol),
        intent=PlanIntent(mode=mode, direction_bias=direction_bias, time_horizon="intraday"),
        position_policy=PositionPolicyRange(
            target_position_pct_range=(tgt_lo, tgt_hi),
            rebalance_band_pct_range=(rb_lo, rb_hi),
            cooldown_minutes_range=(cd_lo, cd_hi),
            priority=prio,
        ),
        allowed_actions=final_plan.allowed_actions,
        valid_from_kst=str(final_plan.valid_from_kst),
        valid_to_kst=str(final_plan.valid_to_kst),
        constraints={
            "max_spread_bps": float(max_spread),
            "min_expected_edge_bps": float(min_edge),
            "max_atr_pct": float(max_atr),
            "min_liquidity_turnover_24h_krw": float(min_turnover),
            "universe_mode": universe_mode,
            "allow_symbols_override": [],
        },
        rationale=dict(final_plan.rationale or {}),
        evidence_refs=refs,
        open_questions=list(final_plan.open_questions or []),
        conflict_resolution=conflicts,
        conditional_activation=dict(conditional_activation or {}),
        notes=str(final_plan.notes or ""),
        confidence=PlanConfidence(
            data_sufficiency=suff,
            backtest_trades=bt_trades,
            backtest_window="500 bars",
            paper_only_recommended=bool(paper_only_recommended),
        ),
    )


def _build_execution_plan(
    *,
    final_plan: FinalTradePlan,
    plan_v2: FinalTradePlanV2,
    rules: RulesConfig,
    rules_raw: Mapping[str, Any],
    capital_profile: Mapping[str, Any],
    risk_max_position_pct: float,
    activation_decision: str,
    live_execution_enabled: bool,
    max_trades_per_day: int = 6,
) -> ExecutionPlan:
    rng = plan_v2.position_policy
    tgt_lo, tgt_hi = _bounded_pair(
        float(rng.target_position_pct_range[0]),
        float(rng.target_position_pct_range[1]),
        min_v=0.0,
        max_v=100.0,
    )
    rb_lo, rb_hi = _bounded_pair(
        float(rng.rebalance_band_pct_range[0]),
        float(rng.rebalance_band_pct_range[1]),
        min_v=0.0,
        max_v=50.0,
    )
    cd_lo, cd_hi = _bounded_pair_int(
        int(rng.cooldown_minutes_range[0]),
        int(rng.cooldown_minutes_range[1]),
        min_v=0,
        max_v=10080,
    )

    capital_cap = _as_float(capital_profile.get("max_target_position_pct"), default=100.0)
    hard_cap = min(float(capital_cap), float(rules.risk.max_position_pct_per_symbol), float(risk_max_position_pct))
    chosen_target = min(float(tgt_hi), float(hard_cap))
    chosen_target = max(0.0, float(chosen_target))
    if chosen_target < float(tgt_lo) and float(tgt_lo) <= float(hard_cap):
        chosen_target = float(tgt_lo)
    if str(activation_decision).upper() == "HOLD" or not bool(final_plan.allowed_actions.buy):
        chosen_target = 0.0

    chosen_rebalance = min(max(float(final_plan.rebalance_band_pct), float(rb_lo)), float(rb_hi))
    chosen_cooldown = min(max(int(final_plan.cooldown_minutes), int(cd_lo)), int(cd_hi))

    max_spread = _as_float(
        (plan_v2.constraints or {}).get("max_spread_bps"),
        default=float(rules.cost_guard.max_spread_bps_entry),
    )
    min_edge = _as_float(
        (plan_v2.constraints or {}).get("min_expected_edge_bps"),
        default=float(rules.cost_guard.min_expected_edge_bps),
    )
    decision = str(activation_decision or "PAPER").upper()
    return ExecutionPlan(
        symbol=str(final_plan.symbol),
        final_numbers=ExecutionFinalNumbers(
            target_position_pct=float(chosen_target),
            rebalance_band_pct=float(chosen_rebalance),
            cooldown_minutes=int(chosen_cooldown),
        ),
        gates={
            "spread_bps_max": float(max_spread),
            "min_edge_bps": float(min_edge),
            "max_daily_loss_pct": float(rules.risk.max_daily_loss_pct),
            "max_trades_per_day": int(max(0, int(max_trades_per_day))),
            "regime_trade_allowed": True,
            "paper_only": bool(decision != "LIVE" or not live_execution_enabled),
            "activation_decision": decision,
        },
        sizing_rule="min(plan_cap_target, signal_target)",
        rebalance_rule="rebalance if abs(curr-target) > band",
        execution_style=_execution_style_from_rules(rules_raw=rules_raw),
    )


def evaluate_policy_activation_gate(
    *,
    rules_raw: Mapping[str, Any],
    fact_pack: Mapping[str, Any],
    final_symbol: str,
) -> dict[str, Any]:
    gov = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    gate_cfg = (gov.get("activation_gate") or {}) if isinstance(gov, Mapping) else {}
    paper_mode_cfg = (rules_raw.get("paper_mode") or {}) if isinstance(rules_raw, Mapping) else {}
    data_collection_cfg = (
        (paper_mode_cfg.get("data_collection") or {}) if isinstance(paper_mode_cfg, Mapping) else {}
    )
    is_paper = str(((rules_raw.get("universe") or {}).get("mode") or "paper")).strip().lower() == "paper"
    data_collection_enabled = bool(data_collection_cfg.get("enabled", False))
    enabled = bool(gate_cfg.get("enabled", True))
    if not enabled:
        return {
            "enabled": False,
            "passed": True,
            "checks": [{"name": "gate_disabled", "passed": True, "actual": "disabled", "required": "disabled"}],
            "selected_backtest": None,
            "reason_code": "POLICY_GATE_DISABLED",
            "decision": "PAPER",
        }

    min_trades = int(_as_float(gate_cfg.get("min_backtest_trades"), default=3.0))
    min_win_rate_pct = float(_as_float(gate_cfg.get("min_win_rate_pct"), default=40.0))
    min_backtest_score = float(_as_float(gate_cfg.get("min_backtest_score"), default=0.0))
    max_drawdown_pct = float(_as_float(gate_cfg.get("max_drawdown_pct"), default=25.0))
    require_symbol_match = bool(gate_cfg.get("require_symbol_match", True))
    strict_min_trades = int(_as_float(data_collection_cfg.get("min_trades_for_strict_gate"), default=30.0))
    relaxed_min_win = float(
        _as_float(data_collection_cfg.get("relaxed_min_win_rate_pct"), default=min_win_rate_pct)
    )
    relaxed_min_score = float(
        _as_float(data_collection_cfg.get("relaxed_min_backtest_score"), default=min_backtest_score)
    )
    relaxed_min_pf = float(_as_float(data_collection_cfg.get("relaxed_min_profit_factor"), default=1.05))
    relaxed_min_expectancy = float(_as_float(data_collection_cfg.get("relaxed_min_expectancy_pct"), default=0.0))

    bt = _extract_quant_backtest_for_symbol(fact_pack=fact_pack, symbol=final_symbol)
    if bt is None:
        return {
            "enabled": True,
            "passed": False,
            "checks": [{"name": "backtest_presence", "passed": False, "actual": "missing", "required": "present"}],
            "selected_backtest": None,
            "reason_code": "POLICY_GATE_BACKTEST_MISSING",
            "decision": "PAPER",
        }

    bt_symbol = str(bt.get("symbol") or "").strip().upper()
    final_symbol_u = str(final_symbol or "").strip().upper()
    trades_actual = int(_as_float(bt.get("trades"), default=0.0))
    win_rate_actual = float(_as_float(bt.get("win_rate_pct"), default=0.0))
    backtest_score_actual = float(_as_float(bt.get("backtest_score"), default=-999.0))
    mdd_actual = float(_as_float(bt.get("max_drawdown_pct"), default=999.0))
    pf_actual = float(_as_float(bt.get("profit_factor"), default=0.0))
    expectancy_actual = float(
        _as_float(bt.get("expectancy_after_cost_pct"), default=_as_float(bt.get("avg_trade_return_pct"), default=0.0))
    )
    min_trades_effective = min_trades
    if is_paper and data_collection_enabled:
        min_trades_effective = max(min_trades, strict_min_trades)

    if is_paper and data_collection_enabled and trades_actual < int(strict_min_trades):
        checks = [
            {
                "name": "symbol_match",
                "passed": (not require_symbol_match) or (bt_symbol == final_symbol_u),
                "actual": bt_symbol,
                "required": final_symbol_u if require_symbol_match else "any",
            },
            {
                "name": "trades_for_strict_gate",
                "passed": False,
                "actual": trades_actual,
                "required": int(strict_min_trades),
            },
        ]
        return {
            "enabled": True,
            "passed": False,
            "checks": checks,
            "selected_backtest": dict(bt),
            "reason_code": "POLICY_GATE_INSUFFICIENT_DATA",
            "paper_data_collection_mode": True,
            "decision": "PAPER",
        }

    checks = [
        {
            "name": "symbol_match",
            "passed": (not require_symbol_match) or (bt_symbol == final_symbol_u),
            "actual": bt_symbol,
            "required": final_symbol_u if require_symbol_match else "any",
        },
        {
            "name": "trades",
            "passed": trades_actual >= int(min_trades_effective),
            "actual": trades_actual,
            "required": int(min_trades_effective),
        },
        {
            "name": "max_drawdown_pct",
            "passed": mdd_actual <= float(max_drawdown_pct),
            "actual": mdd_actual,
            "required": float(max_drawdown_pct),
        },
    ]
    if is_paper and data_collection_enabled:
        perf_pass = (
            (win_rate_actual >= float(relaxed_min_win))
            or (backtest_score_actual >= float(relaxed_min_score))
            or (pf_actual >= float(relaxed_min_pf))
            or (expectancy_actual >= float(relaxed_min_expectancy))
        )
        checks.append(
            {
                "name": "performance_or",
                "passed": bool(perf_pass),
                "actual": {
                    "win_rate_pct": win_rate_actual,
                    "backtest_score": backtest_score_actual,
                    "profit_factor": pf_actual,
                    "expectancy_after_cost_pct": expectancy_actual,
                },
                "required": {
                    "any_of": {
                        "win_rate_pct": float(relaxed_min_win),
                        "backtest_score": float(relaxed_min_score),
                        "profit_factor": float(relaxed_min_pf),
                        "expectancy_after_cost_pct": float(relaxed_min_expectancy),
                    }
                },
            }
        )
    else:
        checks.extend(
            [
                {
                    "name": "win_rate_pct",
                    "passed": win_rate_actual >= float(min_win_rate_pct),
                    "actual": win_rate_actual,
                    "required": float(min_win_rate_pct),
                },
                {
                    "name": "backtest_score",
                    "passed": backtest_score_actual >= float(min_backtest_score),
                    "actual": backtest_score_actual,
                    "required": float(min_backtest_score),
                },
            ]
        )
    passed = all(bool(c.get("passed")) for c in checks)
    return {
        "enabled": True,
        "passed": bool(passed),
        "checks": checks,
        "selected_backtest": dict(bt),
        "reason_code": "POLICY_GATE_PASS" if passed else "POLICY_GATE_BLOCKED",
        "paper_data_collection_mode": bool(is_paper and data_collection_enabled),
        "decision": "LIVE" if bool(passed) else "PAPER",
    }


def _build_agent_tasks(*, slot_key: str, outputs: GovernanceOutputs) -> list[dict[str, Any]]:
    due_ts = (_now_kst() + timedelta(hours=8)).isoformat()
    symbol = str(outputs.final_plan.symbol)
    return [
        {
            "task_id": str(uuid.uuid4()),
            "target_agent": "research_agent",
            "task_type": "NEWS_DIGEST",
            "priority": "HIGH",
            "status": "READY",
            "due_ts_kst": due_ts,
            "description": f"{symbol} 포함 주요 이슈/리스크 브리프 갱신",
            "slot_key": slot_key,
            "payload": {"symbol": symbol, "focus": "catalyst+risk"},
        },
        {
            "task_id": str(uuid.uuid4()),
            "target_agent": "quant_strategist",
            "task_type": "UNIVERSE_SCAN_BACKTEST",
            "priority": "HIGH",
            "status": "READY",
            "due_ts_kst": due_ts,
            "description": "업비트 동적 유니버스 스캔 + 후보 백테스트 재실행",
            "slot_key": slot_key,
            "payload": {
                "symbol_hint": symbol,
                "rebalance_band_pct": float(outputs.final_plan.rebalance_band_pct),
                "cooldown_minutes": int(outputs.final_plan.cooldown_minutes),
            },
        },
        {
            "task_id": str(uuid.uuid4()),
            "target_agent": "risk_manager",
            "task_type": "LIMIT_AUDIT",
            "priority": "HIGH",
            "status": "READY",
            "due_ts_kst": due_ts,
            "description": "노출/손실 한도 및 실패 시나리오 점검",
            "slot_key": slot_key,
            "payload": {
                "max_position_pct": float(outputs.risk.max_position_pct),
                "max_daily_loss_pct": float(outputs.risk.max_daily_loss_pct),
            },
        },
        {
            "task_id": str(uuid.uuid4()),
            "target_agent": "ops_manager",
            "task_type": "EXCHANGE_HEALTH_RECON",
            "priority": "HIGH",
            "status": "READY",
            "due_ts_kst": due_ts,
            "description": "정합성/지연/알림 실패 모니터링 및 리포트",
            "slot_key": slot_key,
            "payload": {"required_ops_gates": list(outputs.ops.required_ops_gates)},
        },
    ]


def enforce_final_trade_plan(
    *,
    plan: FinalTradePlan,
    quant: QuantPlanDraft,
    risk: RiskDraft,
    ops: OpsDraft,
    fact_pack: Mapping[str, Any],
    allowed_symbols: set[str],
    fallback_symbol: str,
    hard_max_position_pct: float,
) -> FinalTradePlan:
    """Hard enforcement layer (deterministic).

    Safe Judge currently consumes `target_position_pct` only, so if BUY is not allowed we must
    force `target_position_pct=0` to avoid accidental buys.
    """

    # Validity window is deterministic (do not trust model output here).
    vf = str(fact_pack.get("valid_from_kst") or "").strip()
    vt = str(fact_pack.get("valid_to_kst") or "").strip()
    if not vf:
        vf = _now_kst().isoformat()
    if not vt:
        vt = (_now_kst() + timedelta(hours=12)).isoformat()

    sym = str(plan.symbol or "").strip()
    if sym not in allowed_symbols:
        sym = fallback_symbol if fallback_symbol in allowed_symbols else (next(iter(allowed_symbols), sym))

    # Hard gate: ops/risk veto or ops window closed => disable BUY and flatten target.
    buy_allowed = bool(plan.allowed_actions.buy) and bool(quant.allowed_actions.buy)
    if bool(ops.veto) or bool(risk.veto) or not bool(ops.trade_window_allowed):
        buy_allowed = False

    # Clamp target to risk max and hard max. If buy not allowed => target 0.
    max_pos = min(float(hard_max_position_pct), float(risk.max_position_pct))
    tgt = float(plan.target_position_pct)
    tgt2 = 0.0 if not buy_allowed else max(0.0, min(float(tgt), float(max_pos)))

    # Constraints: start from cost_guard, then merge risk.required_constraints, then plan.constraints.
    def _num_map(obj: Any) -> dict[str, float]:
        if not isinstance(obj, Mapping):
            return {}
        out: dict[str, float] = {}
        for k, v in obj.items():
            try:
                out[str(k)] = float(v)
            except Exception:
                continue
        return out

    base_constraints = _num_map(((fact_pack.get("rules") or {}).get("cost_guard") or {}))
    risk_constraints = _num_map(dict(risk.required_constraints or {}))
    plan_constraints = _num_map(dict(plan.constraints or {}))
    merged_constraints: dict[str, float] = {
        **base_constraints,
        **risk_constraints,
        **plan_constraints,
    }

    conflict = list(plan.conflict_resolution or [])
    if sym != str(plan.symbol or "").strip():
        conflict.append(f"심볼 보정: allowed_symbols 밖 -> {sym}")
    if not buy_allowed and float(tgt2) == 0.0:
        conflict.append("하드 게이트: ops/risk veto 또는 trade_window 차단 -> buy=false, target=0")
    if float(tgt2) != float(tgt):
        conflict.append(f"리스크 상한 적용: target {tgt:.1f}% -> {tgt2:.1f}% (max_pos={max_pos:.1f}%)")

    return plan.model_copy(
        update={
            "symbol": sym,
            "target_position_pct": float(tgt2),
            "allowed_actions": AllowedActions(buy=bool(buy_allowed), sell=bool(plan.allowed_actions.sell)),
            "valid_from_kst": vf,
            "valid_to_kst": vt,
            "constraints": merged_constraints,
            "conflict_resolution": conflict[:16],
        }
    )


def _llm_enabled_for(route: LLMRoute | None) -> bool:
    if route is not None:
        return bool(route.enabled) and bool(os.environ.get("OPENAI_API_KEY", "").strip())
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _agents_sdk_available() -> bool:
    try:
        import agents  # noqa: F401

        return True
    except Exception:
        return False


def _to_model_settings(route: LLMRoute) -> Any:
    # Delayed import so unit tests can run even if dependency missing.
    from agents import ModelSettings
    from openai.types.shared import Reasoning

    reasoning = None
    effort = str(route.reasoning_effort or "").strip().lower()
    if effort in {"none", "minimal", "low", "medium", "high", "xhigh"}:
        reasoning = Reasoning(effort=effort)
    # Many reasoning models reject temperature (400: unsupported parameter). Keep unset.
    return ModelSettings(reasoning=reasoning, include_usage=True)


def _run_agent_typed(
    *,
    name: str,
    instructions: str,
    output_type: type[BaseModel],
    input_payload: Mapping[str, Any],
    route: LLMRoute,
    strict_json_schema: bool = True,
) -> tuple[BaseModel, AgentRunMeta]:
    from agents import Agent, AgentOutputSchema, Runner

    schema = AgentOutputSchema(output_type, strict_json_schema=bool(strict_json_schema))
    agent = Agent(
        name=name,
        instructions=instructions,
        model=str(route.model),
        model_settings=_to_model_settings(route),
        output_type=schema,
    )

    text_in = "입력(Fact Pack) JSON:\n" + _safe_json(dict(input_payload))
    try:
        res = Runner.run_sync(agent, text_in, max_turns=4)
        out = res.final_output_as(output_type, raise_if_incorrect_type=True)
        return out, AgentRunMeta(used_llm=True, model=str(route.model), response_id=res.last_response_id, error=None)
    except Exception as exc:
        raise RuntimeError(f"{name} failed: {exc}") from exc


def _run_agent_text(
    *,
    name: str,
    instructions: str,
    input_payload: Mapping[str, Any],
    route: LLMRoute,
) -> tuple[str, AgentRunMeta]:
    from agents import Agent, Runner

    agent = Agent(
        name=name,
        instructions=instructions,
        model=str(route.model),
        model_settings=_to_model_settings(route),
    )
    text_in = "입력(JSON):\n" + _safe_json(dict(input_payload))
    try:
        res = Runner.run_sync(agent, text_in, max_turns=3)
        out = str(res.final_output or "").strip()
        if not out:
            raise RuntimeError("empty output")
        return out, AgentRunMeta(used_llm=True, model=str(route.model), response_id=res.last_response_id, error=None)
    except Exception as exc:
        raise RuntimeError(f"{name} failed: {exc}") from exc


def evaluate_candidates(
    *,
    rules_raw: Mapping[str, Any],
    rules: RulesConfig,
    symbols: Sequence[str],
) -> list[dict[str, Any]]:
    timeframe_entry = str((rules_raw.get("signal") or {}).get("timeframe_entry") or "15m")
    tf_min = _timeframe_to_minutes(timeframe_entry)

    rsi_min = float((rules_raw.get("signal") or {}).get("rsi_min") or 50.0)
    vol_min = float((rules_raw.get("signal") or {}).get("volume_zscore_min") or 1.2)
    max_spread = float((rules_raw.get("cost_guard") or {}).get("max_spread_bps_entry") or rules.cost_guard.max_spread_bps_entry)

    evaluated: list[dict[str, Any]] = []
    for sym in symbols:
        try:
            snap = fetch_market_snapshot(sym)
            candles = fetch_candles_minutes(sym, unit=tf_min, count=200)
            highs = [float(c["high_price"]) for c in candles]
            lows = [float(c["low_price"]) for c in candles]
            closes = [float(c["trade_price"]) for c in candles]
            volumes = [float(c["candle_acc_trade_volume"]) for c in candles]
            feat = build_feature_snapshot_from_candles(highs=highs, lows=lows, closes=closes, volumes=volumes)

            snapshot_map = {"last_price": snap.last_price, "spread_bps": snap.spread_bps, "mid_price": snap.mid_price}
            features_map = {"rsi_14": feat.rsi_14, "atr_pct": feat.atr_pct, "vol_zscore": feat.vol_zscore}
            score = score_symbol(
                symbol=sym,
                snapshot=snapshot_map,
                features=features_map,
                rsi_min=rsi_min,
                vol_min=vol_min,
                max_spread_bps=max_spread,
            )
            evaluated.append(
                {
                    "symbol": sym,
                    "score": float(score),
                    "snapshot": snapshot_map,
                    "features": features_map,
                }
            )
        except Exception as exc:
            evaluated.append(
                {
                    "symbol": sym,
                    "score": -9.0,
                    "snapshot": {},
                    "features": {},
                    "error": str(exc)[:180],
                }
            )

    # Sort desc by score for stable ordering.
    evaluated.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return evaluated


def _default_fact_pack(
    *,
    rules_raw: Mapping[str, Any],
    rules: RulesConfig,
    slot_key: str,
    symbols: Sequence[str],
    evaluated: Sequence[Mapping[str, Any]],
    ops_state: Mapping[str, Any],
    research_brief: Mapping[str, Any],
    account_state: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "slot_key": slot_key,
        "meeting_type": "DAILY_STRATEGY",
        "allowed_symbols": list(symbols),
        "evaluated": list(evaluated)[:12],
        "rules": {
            "risk": asdict(rules.risk),
            "cost_guard": asdict(rules.cost_guard),
            "stop_policy": asdict(rules.stop_policy),
            "execution": asdict(rules.execution),
        },
        "ops_state": dict(ops_state),
        "account_state": dict(account_state),
        "research_brief": dict(research_brief),
        "raw_rules_hint": {
            "signal": dict(rules_raw.get("signal") or {}),
            "governance": dict(rules_raw.get("governance") or {}),
        },
    }


def run_governance_protocol(
    *,
    fact_pack: Mapping[str, Any],
    rules_raw: Mapping[str, Any],
    on_step: Callable[[str, str, BaseModel | str, AgentRunMeta], None] | None = None,
) -> GovernanceOutputs:
    """Run multi-agent governance protocol (2 rounds + final + secretary).

    - When `on_step` is provided, it is called after each agent completes (for live UI/streaming).
    - No DB writes happen in this function; persistence belongs to the meeting runner.
    """

    # ----- Routes / toggles -----
    llm_meta: dict[str, AgentRunMeta] = {}
    use_llm = bool(os.environ.get("GOVERNANCE_LLM_ENABLED", "1").strip() not in {"0", "false", "no"})
    sdk_ok = _agents_sdk_available()

    def _route(agent_name: str) -> LLMRoute:
        return llm_route_for_agent(rules_raw=rules_raw, agent_name=agent_name)

    def _step(sender_agent: str, message_type: str, output: BaseModel | str, meta: AgentRunMeta) -> None:
        if on_step is None:
            return
        try:
            on_step(str(sender_agent), str(message_type), output, meta)
        except Exception:
            # Never let UI streaming callback break protocol execution.
            return

    # ----- Deterministic fallbacks -----
    allowed = set(str(s) for s in (fact_pack.get("allowed_symbols") or []) if str(s).strip())
    evaluated = list(fact_pack.get("evaluated") or [])
    best = evaluated[0] if evaluated else {"symbol": (next(iter(allowed), "") or "KRW-BTC"), "score": 0.0, "features": {}, "snapshot": {}}
    best_symbol = str(best.get("symbol") or "").strip() or (next(iter(allowed), "") or "KRW-BTC")
    max_pos_base = float(((fact_pack.get("rules") or {}).get("risk") or {}).get("max_position_pct_per_symbol") or 20.0)
    default_target_base = float(((fact_pack.get("raw_rules_hint") or {}).get("governance") or {}).get("default_target_position_pct") or 10.0)
    cap_profile = fact_pack.get("capital_profile") if isinstance(fact_pack.get("capital_profile"), Mapping) else {}
    cap_tier = str(cap_profile.get("tier_name") or "default")
    cap_max_pos = _as_float(cap_profile.get("max_position_pct_per_symbol"), default=max_pos_base)
    cap_max_target = _as_float(cap_profile.get("max_target_position_pct"), default=default_target_base)
    cap_cooldown = int(_as_float(cap_profile.get("cooldown_minutes_after_trigger"), default=0.0))
    max_pos = min(max_pos_base, cap_max_pos)
    default_target = min(default_target_base, cap_max_target, max_pos)

    pause = bool(((fact_pack.get("ops_state") or {}).get("pause") or {}).get("paused") or False)
    recon_status = str(((fact_pack.get("ops_state") or {}).get("latest_reconciliation") or {}).get("status") or "OK").upper()

    # Research deterministic
    headlines = (fact_pack.get("research_brief") or {}).get("headlines") if isinstance(fact_pack.get("research_brief"), Mapping) else None
    headlines = list(headlines or [])
    hl_text = summarize_headlines_text(headlines, max_items=6)
    det_evidence = []
    for h in headlines[:4]:
        if not isinstance(h, Mapping):
            continue
        det_evidence.append(
            EvidenceCard(
                title=str(h.get("title") or "")[:180] or "헤드라인",
                source=str(h.get("source") or "")[:80] or None,
                url=str(h.get("url") or "")[:500] or None,
                published_at=str(h.get("published_at") or "")[:40] or None,
                impact="변동성/심리 영향을 확인(세부 영향은 미확인)",
                confidence=0.55,
            )
        )
    det_risks: list[str] = []
    if pause:
        det_risks.append("시스템 PAUSE 상태(실행 차단 가능)")
    if recon_status == "FAIL":
        det_risks.append("정합성 FAIL(운영 리스크)")
    if not det_risks:
        det_risks.append("특이 운영 리스크 없음(기계적 체크 기준)")
    det_research = ResearchGovOutput(
        briefing=_clip(f"뉴스/시장 브리프: {hl_text or '주요 헤드라인 없음'}", 480),
        evidence_cards=det_evidence[:8],
        risk_watchlist=det_risks[:8],
        unknowns=["헤드라인 기반이며 세부 내용(원문) 미검증"] if det_evidence else [],
    )

    # Quant deterministic
    det_quant = QuantPlanDraft(
        symbol=best_symbol if best_symbol in allowed else (next(iter(allowed), best_symbol)),
        target_position_pct=float(min(max_pos, default_target)),
        allowed_actions=AllowedActions(buy=not pause and recon_status != "FAIL", sell=True),
        entry_triggers=[
            "스프레드(cost_guard.max_spread_bps_entry) 이내",
            "레짐이 허용(TREND 등)일 때만",
            "RSI/볼륨 조건 충족 시",
        ],
        exit_triggers=[
            "하드스탑(stop_policy.hard_stop_pct) 도달",
            "ATR 기반 트레일링(atr_trail_mult) 약화",
            "time_stop_minutes 도달",
            "ops/recon FAIL 또는 PAUSE 발생 시 청산 우선",
        ],
        rebalance_band_pct=2.0,
        cooldown_minutes=max(
            int(((fact_pack.get("rules") or {}).get("risk") or {}).get("cooldown_minutes_after_trigger") or 180),
            max(0, int(cap_cooldown)),
        ),
        notes=f"deterministic 초안: 점수 상위 심볼을 기본 비중으로 채택 (capital_tier={cap_tier})",
    )

    det_risk = RiskDraft(
        veto=bool(recon_status == "FAIL"),
        max_position_pct=float(max_pos),
        max_loss_per_trade_pct=float(((fact_pack.get("rules") or {}).get("risk") or {}).get("max_risk_per_trade_pct") or 0.35),
        max_daily_loss_pct=float(((fact_pack.get("rules") or {}).get("risk") or {}).get("max_daily_loss_pct") or 1.5),
        required_constraints={**dict((fact_pack.get("rules") or {}).get("cost_guard") or {}), "capital_max_position_pct": float(max_pos)},
        notes=f"deterministic: 룰 상한 + 자본 티어(capital_tier={cap_tier})를 적용",
    )

    det_ops = OpsDraft(
        veto=bool(recon_status == "FAIL"),
        trade_window_allowed=not pause and recon_status != "FAIL",
        required_ops_gates=[
            "reconciliation_status != FAIL",
            "pause_state == false",
            "rate_limit_alert == false",
        ],
        data_quality_flags=[] if recon_status == "OK" else [f"recon={recon_status}"],
        notes="deterministic: 운영 하드게이트 기반",
    )

    det_critiques = {
        "research_agent": CritiqueOutput(critical_issues=[], suggested_changes=[]),
        "quant_strategist": CritiqueOutput(
            critical_issues=["전략 초안에 예상비용/스프레드 여유가 부족할 수 있음"],
            suggested_changes=["스프레드가 넓을 때 target_position_pct를 낮추는 규칙 추가"],
        ),
        "risk_manager": CritiqueOutput(
            critical_issues=[] if not det_risk.veto else ["정합성 FAIL 상태에서 매수 시도 금지"],
            suggested_changes=["recon FAIL이면 target_position_pct=0 및 buy=false 강제"],
        ),
        "ops_manager": CritiqueOutput(
            critical_issues=[] if det_ops.trade_window_allowed else ["PAUSE/정합성 이슈로 거래 창 닫힘"],
            suggested_changes=["ops veto 시 플랜은 유지하되 실행은 차단"],
        ),
    }

    valid_from = str(fact_pack.get("valid_from_kst") or "") or _now_kst().isoformat()
    valid_to = str(fact_pack.get("valid_to_kst") or "") or (_now_kst() + timedelta(hours=12)).isoformat()

    det_final = FinalTradePlan(
        symbol=det_quant.symbol,
        target_position_pct=float(det_quant.target_position_pct),
        allowed_actions=det_quant.allowed_actions,
        rebalance_band_pct=float(det_quant.rebalance_band_pct),
        cooldown_minutes=int(det_quant.cooldown_minutes),
        valid_from_kst=valid_from,
        valid_to_kst=valid_to,
        constraints={
            **dict((fact_pack.get("rules") or {}).get("cost_guard") or {}),
            **dict((fact_pack.get("rules") or {}).get("risk") or {}),
        },
        rationale={
            "research_agent": det_research.briefing,
            "quant_strategist": det_quant.notes,
            "risk_manager": det_risk.notes,
            "ops_manager": det_ops.notes,
        },
        evidence_refs=[str(c.url) for c in det_research.evidence_cards if c.url][:8],
        open_questions=det_research.unknowns[:8],
        conflict_resolution=[
            "하드 룰: ops/risk veto가 있으면 BUY 차단",
            "비중: quant 초안과 risk 상한을 비교해 낮은 값을 채택",
            f"자본 티어 상한 적용: tier={cap_tier}, max_target={default_target:.1f}%, max_pos={max_pos:.1f}%",
        ],
        notes="deterministic 최종: round 결과를 보수적으로 합성",
    )
    det_final = enforce_final_trade_plan(
        plan=det_final,
        quant=det_quant,
        risk=det_risk,
        ops=det_ops,
        fact_pack=fact_pack,
        allowed_symbols=allowed,
        fallback_symbol=best_symbol,
        hard_max_position_pct=max_pos,
    )

    if not (use_llm and sdk_ok and _llm_enabled_for(_route("governance_coordinator"))):
        llm_meta["research_agent"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=None)
        llm_meta["quant_strategist"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=None)
        llm_meta["risk_manager"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=None)
        llm_meta["ops_manager"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=None)
        llm_meta["governance_coordinator"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=None)
        llm_meta["secretary_agent"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=None)
        for agent_key in det_critiques.keys():
            llm_meta[f"{agent_key}_critique"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=None)

        secretary_minutes = _clip(
            "\n".join(
                [
                    "회의록(요약, deterministic)",
                    f"- 결론: {det_final.symbol} 목표비중 {det_final.target_position_pct:.1f}%",
                    f"- 근거(리서치): {det_research.briefing}",
                    f"- 근거(전략): {det_quant.notes}",
                    f"- 제약(리스크): {det_risk.notes}",
                    f"- 제약(운영): {det_ops.notes}",
                ]
            ),
            3200,
        )

        # Live callback support (even in deterministic mode).
        _step("research_agent", "ROUND1_EVIDENCE", det_research, llm_meta["research_agent"])
        _step("quant_strategist", "ROUND1_PROPOSAL", det_quant, llm_meta["quant_strategist"])
        _step("risk_manager", "ROUND1_CONSTRAINTS", det_risk, llm_meta["risk_manager"])
        _step("ops_manager", "ROUND1_OPS", det_ops, llm_meta["ops_manager"])
        for agent_key, crit in det_critiques.items():
            _step(agent_key, "ROUND2_CRITIQUE", crit, llm_meta[f"{agent_key}_critique"])
        _step("governance_coordinator", "FINAL_TRADE_PLAN", det_final, llm_meta["governance_coordinator"])
        _step("secretary_agent", "MINUTES", secretary_minutes, llm_meta["secretary_agent"])

        return GovernanceOutputs(
            research=det_research,
            quant=det_quant,
            risk=det_risk,
            ops=det_ops,
            critiques=det_critiques,
            final_plan=det_final,
            secretary_minutes=secretary_minutes,
            llm_meta=llm_meta,
        )

    # ----- LLM path (Agents SDK) -----
    # Round 1: independent role outputs
    research_route = _route("governance_research_agent")
    quant_route = _route("governance_quant_strategist")
    risk_route = _route("governance_risk_manager")
    ops_route = _route("governance_ops_manager")
    coord_route = _route("governance_coordinator")
    sec_route = _route("governance_secretary")

    def _ensure_symbol(sym: str) -> str:
        sym2 = str(sym or "").strip()
        if sym2 in allowed:
            return sym2
        return best_symbol if best_symbol in allowed else (next(iter(allowed), sym2))

    try:
        r1_research, m_research = _run_agent_typed(
            name="research_agent",
            instructions=governance_research_instructions(),
            output_type=ResearchGovOutput,
            input_payload=fact_pack,
            route=research_route,
        )
        llm_meta["research_agent"] = m_research
    except Exception as exc:
        llm_meta["research_agent"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=str(exc)[:200])
        r1_research = det_research
    _step("research_agent", "ROUND1_EVIDENCE", r1_research, llm_meta["research_agent"])

    try:
        r1_quant, m_quant = _run_agent_typed(
            name="quant_strategist",
            instructions=governance_quant_instructions(),
            output_type=QuantPlanDraft,
            input_payload=fact_pack,
            route=quant_route,
        )
        r1_quant = r1_quant.model_copy(update={"symbol": _ensure_symbol(r1_quant.symbol)})
        llm_meta["quant_strategist"] = m_quant
    except Exception as exc:
        llm_meta["quant_strategist"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=str(exc)[:200])
        r1_quant = det_quant
    _step("quant_strategist", "ROUND1_PROPOSAL", r1_quant, llm_meta["quant_strategist"])

    try:
        r1_risk, m_risk = _run_agent_typed(
            name="risk_manager",
            instructions=governance_risk_instructions(),
            output_type=RiskDraft,
            input_payload=fact_pack,
            route=risk_route,
            strict_json_schema=False,
        )
        llm_meta["risk_manager"] = m_risk
    except Exception as exc:
        llm_meta["risk_manager"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=str(exc)[:200])
        r1_risk = det_risk
    _step("risk_manager", "ROUND1_CONSTRAINTS", r1_risk, llm_meta["risk_manager"])

    try:
        r1_ops, m_ops = _run_agent_typed(
            name="ops_manager",
            instructions=governance_ops_instructions(),
            output_type=OpsDraft,
            input_payload=fact_pack,
            route=ops_route,
        )
        llm_meta["ops_manager"] = m_ops
    except Exception as exc:
        llm_meta["ops_manager"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=str(exc)[:200])
        r1_ops = det_ops
    _step("ops_manager", "ROUND1_OPS", r1_ops, llm_meta["ops_manager"])

    # Round 2: critique (each agent gets others' outputs)
    critique_input = dict(fact_pack)
    critique_input["round1"] = {
        "research_agent": r1_research.model_dump(),
        "quant_strategist": r1_quant.model_dump(),
        "risk_manager": r1_risk.model_dump(),
        "ops_manager": r1_ops.model_dump(),
    }

    critiques: dict[str, CritiqueOutput] = {}
    for agent_key, route in (
        ("research_agent", research_route),
        ("quant_strategist", quant_route),
        ("risk_manager", risk_route),
        ("ops_manager", ops_route),
    ):
        try:
            c, mc = _run_agent_typed(
                name=f"{agent_key}_critique",
                instructions=governance_critique_instructions(),
                output_type=CritiqueOutput,
                input_payload=critique_input,
                route=route,
            )
            critiques[agent_key] = c
            llm_meta[f"{agent_key}_critique"] = mc
        except Exception as exc:
            llm_meta[f"{agent_key}_critique"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=str(exc)[:200])
            critiques[agent_key] = det_critiques.get(agent_key) or CritiqueOutput(critical_issues=[], suggested_changes=[])
        _step(agent_key, "ROUND2_CRITIQUE", critiques[agent_key], llm_meta[f"{agent_key}_critique"])

    # Final: coordinator composes plan (hard constraints first)
    final_input = dict(fact_pack)
    final_input["round1"] = critique_input["round1"]
    final_input["critiques"] = {k: v.model_dump() for k, v in critiques.items()}

    try:
        final_plan, m_final = _run_agent_typed(
            name="governance_coordinator",
            instructions=governance_coordinator_instructions(),
            output_type=FinalTradePlan,
            input_payload=final_input,
            route=coord_route,
            strict_json_schema=False,
        )
        final_plan = final_plan.model_copy(update={"symbol": _ensure_symbol(final_plan.symbol)})
        llm_meta["governance_coordinator"] = m_final
    except Exception as exc:
        llm_meta["governance_coordinator"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=str(exc)[:200])
        final_plan = det_final

    final_plan = enforce_final_trade_plan(
        plan=final_plan,
        quant=r1_quant,
        risk=r1_risk,
        ops=r1_ops,
        fact_pack=fact_pack,
        allowed_symbols=allowed,
        fallback_symbol=best_symbol,
        hard_max_position_pct=max_pos,
    )
    _step("governance_coordinator", "FINAL_TRADE_PLAN", final_plan, llm_meta["governance_coordinator"])

    # Secretary: human minutes
    secretary_input = dict(fact_pack)
    secretary_input["round1"] = critique_input["round1"]
    secretary_input["critiques"] = {k: v.model_dump() for k, v in critiques.items()}
    secretary_input["final_plan"] = final_plan.model_dump()
    try:
        minutes, m_minutes = _run_agent_text(
            name="secretary_agent",
            instructions=governance_secretary_instructions(),
            input_payload=secretary_input,
            route=sec_route,
        )
        llm_meta["secretary_agent"] = m_minutes
    except Exception as exc:
        llm_meta["secretary_agent"] = AgentRunMeta(used_llm=False, model=None, response_id=None, error=str(exc)[:200])
        minutes = _clip(
            "\n".join(
                [
                    "회의록(요약, fallback)",
                    f"- 결론: {final_plan.symbol} 목표비중 {final_plan.target_position_pct:.1f}%",
                    f"- 근거(리서치): {r1_research.briefing}",
                    f"- 근거(전략): {r1_quant.notes}",
                    f"- 제약(리스크): {r1_risk.notes}",
                    f"- 제약(운영): {r1_ops.notes}",
                ]
            ),
            3200,
        )
    _step("secretary_agent", "MINUTES", _clip(str(minutes), 3200), llm_meta["secretary_agent"])

    return GovernanceOutputs(
        research=r1_research,
        quant=r1_quant,
        risk=r1_risk,
        ops=r1_ops,
        critiques=critiques,
        final_plan=final_plan,
        secretary_minutes=_clip(minutes, 3200),
        llm_meta=llm_meta,
    )


def _msg_content_for(output: BaseModel | str) -> str:
    if isinstance(output, str):
        return _clip(output, 1400)
    data = output.model_dump()
    # Keep it readable for UI transcript.
    if "briefing" in data:
        lines = [str(data.get("briefing") or "").strip()]
        risks = data.get("risk_watchlist") or []
        if isinstance(risks, list) and risks:
            lines.append("리스크:")
            for r in risks[:6]:
                lines.append(f"- {r}")
        return _clip("\n".join([x for x in lines if str(x).strip()]), 1400)
    if "target_position_pct" in data and "symbol" in data:
        sym = str(data.get("symbol") or "")
        tgt = data.get("target_position_pct")
        notes = str(data.get("notes") or "")
        return _clip(f"{sym} 목표비중 {float(tgt):.1f}%\n{notes}", 1400) if isinstance(tgt, (int, float)) else _clip(notes, 1400)
    if "veto" in data:
        veto = bool(data.get("veto"))
        notes = str(data.get("notes") or "")
        return _clip(f"veto={veto}\n{notes}", 1400)
    if "critical_issues" in data:
        issues = data.get("critical_issues") or []
        sugg = data.get("suggested_changes") or []
        lines: list[str] = []
        if isinstance(issues, list) and issues:
            lines.append("치명 이슈:")
            for x in issues[:6]:
                lines.append(f"- {x}")
        if isinstance(sugg, list) and sugg:
            lines.append("수정 제안:")
            for x in sugg[:6]:
                lines.append(f"- {x}")
        return _clip("\n".join(lines) or "특이사항 없음", 1400)
    return _clip(_safe_json(data), 1400)


def _store_message(
    *,
    repo: PostgresRepo,
    meeting_id: uuid.UUID,
    sender_agent: str,
    message_type: str,
    content: str,
    payload: Any | None,
    confidence: float | None,
    emit: Callable[[str, Mapping[str, Any]], None] | None,
) -> None:
    msg_id = uuid.uuid4()
    ts = _utcnow()
    repo.insert_meeting_message(
        DbMeetingMessage(
            message_id=msg_id,
            meeting_id=meeting_id,
            ts=ts,
            sender_agent=sender_agent,
            message_type=message_type,
            content=content,
            payload=payload,
            confidence=confidence,
        )
    )
    repo.insert_event(
        DbEvent(
            event_id=uuid.uuid4(),
            ts=ts,
            event_type="MEETING_MESSAGE",
            entity_type="meeting_messages",
            entity_id=str(msg_id),
            run_id=None,
            rule_version_id=None,
            payload={
                "meeting_id": str(meeting_id),
                "sender_agent": sender_agent,
                "message_type": message_type,
                "content": content,
            },
        )
    )
    if emit is not None:
        emit(
            "message",
            {
                "message_id": str(msg_id),
                "meeting_id": str(meeting_id),
                "ts": ts.isoformat(),
                "sender_agent": sender_agent,
                "message_type": message_type,
                "content": content,
                "payload": payload,
                "confidence": confidence,
            },
        )


def _as_kst_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).astimezone(KST)
        return value.astimezone(KST)
    return None


def _close_or_skip_open_meeting(
    *,
    repo: PostgresRepo,
    rules_raw: Mapping[str, Any],
    emit: Callable[[str, Mapping[str, Any]], None] | None,
) -> bool:
    """Return True when a currently running meeting should block a new one."""

    governance_cfg = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    stale_min = int(governance_cfg.get("max_open_meeting_minutes") or 45)
    now_kst = _now_kst()

    sessions = repo.fetch_meeting_sessions(limit=30)
    for s in sessions:
        if str(s.get("meeting_type") or "").upper() != "DAILY_STRATEGY":
            continue
        if str(s.get("status") or "").upper() != "OPEN":
            continue
        mid = str(s.get("meeting_id") or "")
        started_kst = _as_kst_dt(s.get("started_at"))
        age_min = None
        if started_kst is not None:
            age_min = max(0.0, (now_kst - started_kst).total_seconds() / 60.0)

        # 오래된 OPEN 회의는 자동 종료해서 다음 슬롯이 막히지 않게 한다.
        if age_min is not None and age_min >= float(stale_min):
            ended_at = _utcnow()
            repo.update_meeting_session(
                meeting_id=mid,
                status="CLOSED",
                ended_at=ended_at,
                summary=_clip(f"자동 종료: 회의 최대 실행 시간 초과({age_min:.1f}분)", 900),
                decisions={"error": "MEETING_TIMEOUT", "auto_closed": True, "age_min": age_min},
                action_items={"items": []},
            )
            repo.insert_event(
                DbEvent(
                    event_id=uuid.uuid4(),
                    ts=ended_at,
                    event_type="MEETING_AUTO_CLOSED",
                    entity_type="meeting_sessions",
                    entity_id=mid,
                    run_id=None,
                    rule_version_id=None,
                    payload={"meeting_id": mid, "reason_code": "MEETING_TIMEOUT", "age_min": age_min},
                )
            )
            if emit is not None:
                emit(
                    "run_warning",
                    {"meeting_id": mid, "reason": "stale_open_auto_closed", "age_min": age_min},
                )
            continue

        if emit is not None:
            emit(
                "run_skipped",
                {"reason": "another_meeting_open", "meeting_id": mid, "age_min": age_min},
            )
        return True
    return False


def run_governance_meeting_now(
    *,
    repo: PostgresRepo,
    notifier: NotificationService,
    rules_raw: Mapping[str, Any],
    force_slot_key: str | None = None,
    emit: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> str:
    """Run a single governance meeting immediately and persist everything.

    This is used by:
    - API SSE live meeting endpoint
    - scripts (manual run)
    """

    rules = load_rules("rules.yaml")
    now_kst = _now_kst()
    times = get_meeting_times_kst(rules_raw)
    slot_key = force_slot_key or f"{now_kst.date().isoformat()} LIVE {now_kst.strftime('%H:%M:%S')}"

    if _close_or_skip_open_meeting(repo=repo, rules_raw=rules_raw, emit=emit):
        return slot_key

    # Governance meeting must consume DB prework outputs (no live universe scan here).
    symbols = list(rules.universe.symbols)

    # Valid window (deterministic):
    # - scheduled slot: [slot_dt, next_slot_dt)
    # - live/ad-hoc: [now, now+8h)
    hit_slot: str | None = None
    if force_slot_key:
        parts = str(force_slot_key).split()
        if len(parts) >= 2 and ":" in parts[1]:
            hit_slot = parts[1].strip()

    if hit_slot and hit_slot in set(times):
        vf_kst = _slot_dt_for_today_kst(now_kst, hit_slot)
        try:
            vt_kst = next_slot_kst(vf_kst, times=times, current=hit_slot)
        except Exception:
            vt_kst = vf_kst + timedelta(hours=8)
        agenda_mode = "SCHEDULED"
    else:
        vf_kst = now_kst
        vt_kst = now_kst + timedelta(hours=8)
        agenda_mode = "LIVE"

    meeting_id = uuid.uuid4()
    started_at = _utcnow()

    # Create session as OPEN first (so UI can watch live).
    participants = ["research_agent", "quant_strategist", "risk_manager", "ops_manager", "governance_coordinator", "secretary_agent"]
    session_agenda = {
        "slot_key": slot_key,
        "symbols": symbols,
        "universe_source": "prework_reports",
        "timeframe_entry": str((rules_raw.get("signal") or {}).get("timeframe_entry") or "15m"),
        "mode": agenda_mode,
    }
    repo.insert_meeting_session(
        DbMeetingSession(
            meeting_id=meeting_id,
            meeting_type="DAILY_STRATEGY",
            status="OPEN",
            started_at=started_at,
            ended_at=None,
            facilitator="governance_coordinator",
            participants=participants,
            agenda=session_agenda,
            summary=None,
            decisions=None,
            action_items={"items": []},
            run_id=None,
        )
    )
    repo.insert_event(
        DbEvent(
            event_id=uuid.uuid4(),
            ts=started_at,
            event_type="MEETING_STARTED",
            entity_type="meeting_sessions",
            entity_id=str(meeting_id),
            run_id=None,
            rule_version_id=None,
            payload={"meeting_id": str(meeting_id), "meeting_type": "DAILY_STRATEGY", "slot_key": slot_key},
        )
    )
    if emit is not None:
        emit(
            "meta",
            {
                "meeting_id": str(meeting_id),
                "slot_key": slot_key,
                "started_at": started_at.isoformat(),
                "meeting_type": "DAILY_STRATEGY",
                "status": "OPEN",
            },
        )

    try:
        # Prework orchestration: each meeting should consume fresh agent work reports.
        prework_agents = ["research_agent", "quant_strategist", "risk_manager", "ops_manager"]
        prework_max_age_min = int(((rules_raw.get("governance") or {}).get("prework_max_age_min") or 360) if isinstance(rules_raw, Mapping) else 360)
        require_prework_reports = bool(((rules_raw.get("governance") or {}).get("require_prework_reports")) if isinstance(rules_raw, Mapping) else False)
        prework = collect_latest_work_reports(
            repo=repo,
            agent_names=prework_agents,
            max_age_minutes=prework_max_age_min,
            include_details=True,
        )
        prework_cycle_info: dict[str, Any] | None = {"mode": "db_only"}

        fresh_agents = sorted(
            set(prework_agents)
            - set(str(x) for x in list(prework.get("missing") or []))
            - set(str(x) for x in list(prework.get("stale") or []))
        )
        prework_content = _clip(
            "\n".join(
                [
                    f"사전업무 상태: fresh={len(fresh_agents)}, stale={len(list(prework.get('stale') or []))}, missing={len(list(prework.get('missing') or []))}",
                    f"- fresh: {', '.join(fresh_agents) or '(없음)'}",
                    f"- stale: {', '.join(list(prework.get('stale') or [])) or '(없음)'}",
                    f"- missing: {', '.join(list(prework.get('missing') or [])) or '(없음)'}",
                ]
            ),
            1200,
        )
        _store_message(
            repo=repo,
            meeting_id=meeting_id,
            sender_agent="orchestrator",
            message_type="PREWORK_STATUS",
            content=prework_content,
            payload={"prework": prework, "refresh_cycle": prework_cycle_info},
            confidence=0.95,
            emit=emit,
        )
        if should_block_prework(require_prework_reports=require_prework_reports, prework=prework):
            raise RuntimeError(
                f"prework_missing_or_stale: missing={list(prework.get('missing') or [])}, stale={list(prework.get('stale') or [])}"
            )

        prework_reports = dict((prework or {}).get("reports") or {})
        quant_report = prework_reports.get("quant_strategist") if isinstance(prework_reports.get("quant_strategist"), Mapping) else {}
        quant_findings = dict((quant_report or {}).get("findings") or {}) if isinstance(quant_report, Mapping) else {}
        evaluated_raw = quant_findings.get("candidates") if isinstance(quant_findings.get("candidates"), list) else []
        evaluated = [x for x in list(evaluated_raw or []) if isinstance(x, Mapping)]
        if not evaluated:
            evaluated = [{"symbol": (symbols[0] if symbols else "KRW-BTC"), "score": -9.0, "snapshot": {}, "features": {}}]

        pause = repo.fetch_pause_state()
        recon = repo.fetch_latest_reconciliation()

        suggested_plan = quant_findings.get("suggested_plan") if isinstance(quant_findings.get("suggested_plan"), Mapping) else {}
        best_symbol = str((suggested_plan or {}).get("symbol") or (evaluated[0] if evaluated else {}).get("symbol") or (symbols[0] if symbols else "KRW-BTC"))
        research_report = prework_reports.get("research_agent") if isinstance(prework_reports.get("research_agent"), Mapping) else {}
        research_findings = dict((research_report or {}).get("findings") or {}) if isinstance(research_report, Mapping) else {}
        headlines_compact = [h for h in list(research_findings.get("headlines") or []) if isinstance(h, Mapping)][:10]
        research_summary = str((research_report or {}).get("summary") or "").strip()
        research_brief = {
            "headlines": headlines_compact,
            "headlines_text": summarize_headlines_text(headlines_compact, max_items=6) or research_summary,
        }

        # Account state snapshot (paper sizing / context)
        try:
            quote_ccy = best_symbol.split("-", 1)[0] if "-" in best_symbol else "KRW"
        except Exception:
            quote_ccy = "KRW"
        cash = repo.fetch_cash_balance(currency=quote_ccy)
        pos = repo.fetch_position(best_symbol)
        pos_value = (float(pos.qty) * float((evaluated[0] if evaluated else {}).get("snapshot", {}).get("mid_price") or 0.0)) if pos else 0.0
        equity = float(cash) + float(pos_value)
        capital_profile = resolve_capital_policy(
            rules_raw=rules_raw,
            equity_krw=equity,
            default_target_position_pct=float(((rules_raw.get("governance") or {}).get("default_target_position_pct") or 10.0)),
            max_position_pct_per_symbol=float(rules.risk.max_position_pct_per_symbol),
            cooldown_minutes_after_trigger=int(rules.risk.cooldown_minutes_after_trigger),
        )
        account_state = {
            "cash_krw": float(cash),
            "equity_krw": float(equity),
            "position_value_krw": float(pos_value),
            "current_qty": float(pos.qty) if pos else 0.0,
            "avg_entry_price": float(pos.avg_entry_price) if (pos and pos.avg_entry_price) else None,
            "capital_profile": capital_profile.as_dict(),
        }

        fact_pack = _default_fact_pack(
            rules_raw=rules_raw,
            rules=rules,
            slot_key=slot_key,
            symbols=symbols,
            evaluated=evaluated,
            ops_state={"pause": pause, "latest_reconciliation": recon},
            research_brief=research_brief,
            account_state=account_state,
        )
        fact_pack["prework_reports"] = dict((prework or {}).get("reports") or {})
        fact_pack["prework_status"] = {
            "require_prework_reports": bool(require_prework_reports),
            "fresh_agents": fresh_agents,
            "stale_agents": list(prework.get("stale") or []),
            "missing_agents": list(prework.get("missing") or []),
            "max_age_minutes": prework_max_age_min,
            "refresh_cycle": prework_cycle_info,
        }
        fact_pack["valid_from_kst"] = vf_kst.isoformat()
        fact_pack["valid_to_kst"] = vt_kst.isoformat()
        fact_pack["capital_profile"] = capital_profile.as_dict()
        universe_selection = quant_findings.get("universe_selection") if isinstance(quant_findings.get("universe_selection"), Mapping) else {}
        fact_pack["universe_selection"] = {
            "source": str((universe_selection or {}).get("source") or "prework_unknown"),
            "total_krw_markets": int(_as_float((universe_selection or {}).get("total_krw_markets"), default=0.0)),
            "ranked_count": int(_as_float((universe_selection or {}).get("ranked_count"), default=0.0)),
            "top24h_turnover": [x for x in list((universe_selection or {}).get("top24h_turnover") or []) if isinstance(x, Mapping)][:10],
        }

        def _confidence_for(message_type: str) -> float | None:
            m = str(message_type or "")
            if m.startswith("ROUND1_"):
                return 0.75
            if m.startswith("ROUND2_"):
                return 0.65
            if m.startswith("FINAL"):
                return 0.75
            if m.startswith("MINUTES"):
                return 0.7
            return None

        def on_step(sender_agent: str, message_type: str, output: BaseModel | str, meta: AgentRunMeta) -> None:
            payload: dict[str, Any] = {"llm_meta": asdict(meta)}
            if isinstance(output, BaseModel):
                payload["output"] = output.model_dump()
            else:
                payload["output"] = {"text": str(output)}
            _store_message(
                repo=repo,
                meeting_id=meeting_id,
                sender_agent=sender_agent,
                message_type=message_type,
                content=_msg_content_for(output),
                payload=payload,
                confidence=_confidence_for(message_type),
                emit=emit,
            )

        # Protocol run (LLM or fallback). Messages are persisted/streamed via `on_step`.
        outputs = run_governance_protocol(fact_pack=fact_pack, rules_raw=rules_raw, on_step=on_step)

        ended_at = _utcnow()
        activation_gate = evaluate_policy_activation_gate(
            rules_raw=rules_raw,
            fact_pack=fact_pack,
            final_symbol=outputs.final_plan.symbol,
        )
        activation_gate = dict(activation_gate)
        activation_passed = bool(activation_gate.get("passed"))
        gate_reason_code = str(activation_gate.get("reason_code") or "")
        gov_cfg = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
        gate_cfg = (gov_cfg.get("activation_gate") or {}) if isinstance(gov_cfg, Mapping) else {}
        live_execution_enabled = bool(gate_cfg.get("live_execution_enabled", False))
        paper_mode_cfg = (rules_raw.get("paper_mode") or {}) if isinstance(rules_raw, Mapping) else {}
        data_collection_cfg = (
            (paper_mode_cfg.get("data_collection") or {}) if isinstance(paper_mode_cfg, Mapping) else {}
        )
        force_plan_buy_allowed = bool(data_collection_cfg.get("force_plan_buy_allowed", True))
        force_plan_target_pct = float(_as_float(data_collection_cfg.get("force_plan_target_pct"), default=5.0))
        hard_plan_block = bool(outputs.ops.veto) or bool(outputs.risk.veto) or (not bool(outputs.ops.trade_window_allowed))
        activation_decision = _activation_decision_from_gate(
            activation_gate=activation_gate,
            hard_plan_block=hard_plan_block,
        )
        activation_decision_effective = str(activation_decision)
        if str(activation_decision).upper() == "LIVE" and not bool(live_execution_enabled):
            activation_decision_effective = "PAPER"
        conditional_activation_cfg = _normalized_conditional_activation_config(
            rules_raw=rules_raw,
            force_enabled=None,
        )
        hold_mode = _activation_hold_mode(
            activation_decision_effective=str(activation_decision_effective),
            conditional_activation=conditional_activation_cfg,
        )
        decision_interval_sec = int(
            _as_float(
                ((rules_raw.get("scheduling") or {}).get("decision_interval_sec")),
                default=15.0,
            )
        )
        cap_runtime_seed = _initial_cap_runtime(
            conditional_activation=conditional_activation_cfg,
            decision_interval_sec=max(1, decision_interval_sec),
        )
        activation_gate["decision"] = str(activation_decision)
        activation_gate["decision_effective"] = str(activation_decision_effective)
        activation_gate["live_execution_enabled"] = bool(live_execution_enabled)
        activation_gate["hard_plan_block"] = bool(hard_plan_block)
        activation_gate["hold_mode"] = str(hold_mode)
        activation_gate["conditional_activation"] = dict(conditional_activation_cfg)
        activation_gate["cap_runtime"] = dict(cap_runtime_seed)
        paper_data_collection_applied = bool(
            activation_gate.get("paper_data_collection_mode")
            and gate_reason_code == "POLICY_GATE_INSUFFICIENT_DATA"
            and (not hard_plan_block)
        )

        resolved_allowed_actions = outputs.final_plan.allowed_actions.model_dump()
        resolved_target_position_pct = float(outputs.final_plan.target_position_pct)
        if paper_data_collection_applied:
            resolved_allowed_actions["buy"] = bool(force_plan_buy_allowed)
            if bool(resolved_allowed_actions["buy"]):
                resolved_target_position_pct = max(
                    0.0,
                    min(
                        float(force_plan_target_pct),
                        float(outputs.risk.max_position_pct),
                        float(rules.risk.max_position_pct_per_symbol),
                        float(capital_profile.max_target_position_pct),
                    ),
                )
            if float(resolved_target_position_pct) <= 0.0:
                resolved_allowed_actions["buy"] = False
            activation_gate = dict(activation_gate)
            activation_gate["paper_data_collection_applied"] = True
            activation_gate["paper_data_collection_target_pct"] = float(resolved_target_position_pct)
            activation_gate["paper_data_collection_buy_allowed"] = bool(resolved_allowed_actions["buy"])

        if str(activation_decision_effective).upper() == "HOLD":
            resolved_allowed_actions["buy"] = False
            resolved_allowed_actions["sell"] = False
            resolved_target_position_pct = 0.0

        hold_only_plan = (float(resolved_target_position_pct) <= 0.0) or (not bool(resolved_allowed_actions.get("buy")))
        if str(activation_decision_effective).upper() == "HOLD":
            activation_status = "ACTIVE_HOLD"
        elif str(activation_decision_effective).upper() == "PAPER":
            activation_status = "ACTIVE_DATA_COLLECTION" if paper_data_collection_applied else "ACTIVE_PAPER"
        else:
            activation_status = "ACTIVE"

        gate_checks = [x for x in list(activation_gate.get("checks") or []) if isinstance(x, Mapping)]
        gate_fail_lines = [
            f"- {str(c.get('name'))}: actual={c.get('actual')} required={c.get('required')}"
            for c in gate_checks
            if not bool(c.get("passed"))
        ]
        gate_msg = (
            f"정책 활성화 게이트: {activation_status} "
            f"(decision={activation_gate.get('decision')}, effective={activation_gate.get('decision_effective')}, "
            f"reason={activation_gate.get('reason_code')}, symbol={outputs.final_plan.symbol})"
        )
        if gate_fail_lines:
            gate_msg += "\n" + "\n".join(gate_fail_lines[:8])
        if paper_data_collection_applied:
            gate_msg += (
                f"\n- paper_data_collection: buy={bool(resolved_allowed_actions.get('buy'))}, "
                f"target={float(resolved_target_position_pct):.1f}%"
            )
        _store_message(
            repo=repo,
            meeting_id=meeting_id,
            sender_agent="orchestrator",
            message_type="POLICY_ACTIVATION_GATE",
            content=_clip(gate_msg, 1400),
            payload={"activation_gate": activation_gate},
            confidence=0.95 if activation_passed else 0.8,
            emit=emit,
        )

        summary_short = (
            f"[{slot_key}] Trade Plan({activation_status}): "
            f"{outputs.final_plan.symbol} target={float(resolved_target_position_pct):.1f}%"
        )
        action_items = [
            {"owner": "research_agent", "action": "주요 뉴스 원문 확인 및 영향 업데이트", "due_date": str(now_kst.date())},
            {"owner": "ops_manager", "action": "recon/pause/알림 누락 여부 점검", "due_date": str(now_kst.date())},
            {"owner": "quant_strategist", "action": "과매매 방지(cooldown/rebalance band) 파라미터 점검", "due_date": str(now_kst.date())},
        ]
        if not activation_passed:
            action_items.append(
                {
                    "owner": "quant_strategist",
                    "action": "백테스트 게이트 미통과 원인 수정 후 재검증",
                    "due_date": str(now_kst.date()),
                }
            )

        # Trade plan payload (active/proposed 모두 저장).
        plan_notes = str(outputs.final_plan.notes or "")
        if paper_data_collection_applied:
            plan_notes = _clip(
                "\n".join(
                    [
                        plan_notes,
                        "[paper_data_collection] strict gate 이전 표본 확보 모드 적용",
                        f"- forced_buy_allowed={bool(resolved_allowed_actions.get('buy'))}",
                        f"- forced_target_position_pct={float(resolved_target_position_pct):.1f}",
                    ]
                ),
                1200,
            )
        resolved_final_plan = outputs.final_plan.model_copy(
            update={
                "target_position_pct": float(resolved_target_position_pct),
                "allowed_actions": AllowedActions(
                    buy=bool(resolved_allowed_actions.get("buy")),
                    sell=bool(resolved_allowed_actions.get("sell")),
                ),
            }
        )
        final_plan_v2 = _to_final_trade_plan_v2(
            final_plan=resolved_final_plan,
            rules_raw=rules_raw,
            fact_pack=fact_pack,
            activation_gate=activation_gate,
            conditional_activation=activation_gate.get("conditional_activation")
            if isinstance(activation_gate.get("conditional_activation"), Mapping)
            else None,
        )
        execution_plan = _build_execution_plan(
            final_plan=resolved_final_plan,
            plan_v2=final_plan_v2,
            rules=rules,
            rules_raw=rules_raw,
            capital_profile=capital_profile.as_dict(),
            risk_max_position_pct=float(outputs.risk.max_position_pct),
            activation_decision=str(activation_decision_effective),
            live_execution_enabled=bool(live_execution_enabled),
        )
        fee_total_bps = float(
            _as_float(((rules_raw.get("fees") or {}).get("fallback_bid_fee_bps")), default=5.0)
            + _as_float(((rules_raw.get("fees") or {}).get("fallback_ask_fee_bps")), default=5.0)
        )
        bt_cfg = (rules_raw.get("quant_backtest") or {}) if isinstance(rules_raw, Mapping) else {}
        cost_model = {
            "fee_total_bps": float(fee_total_bps),
            "base_slippage_bps": float(_as_float(bt_cfg.get("base_slippage_bps"), default=1.0)),
            "spread_penalty_mult": float(_as_float(bt_cfg.get("spread_penalty_mult"), default=0.30)),
            "low_liquidity_penalty_bps": float(_as_float(bt_cfg.get("low_liquidity_penalty_bps"), default=1.2)),
        }
        gate_cfg_raw = (gov_cfg.get("activation_gate") or {}) if isinstance(gov_cfg, Mapping) else {}
        paper_live_policy = {
            "live_allowed": bool(str(activation_decision_effective).upper() == "LIVE" and live_execution_enabled),
            "promotion_rules": [
                f"min_backtest_trades>={int(_as_float(gate_cfg_raw.get('min_backtest_trades'), default=3.0))}",
                f"min_win_rate_pct>={float(_as_float(gate_cfg_raw.get('min_win_rate_pct'), default=40.0)):.1f}",
                f"min_backtest_score>={float(_as_float(gate_cfg_raw.get('min_backtest_score'), default=0.0)):.1f}",
            ],
            "demotion_rules": [
                "ops_veto=true -> HOLD",
                "risk_veto=true -> HOLD",
                "reconciliation_status=FAIL -> HOLD",
                "daily_loss_limit_hit -> HOLD",
            ],
        }
        allocator_result = {
            "tier_name": str(capital_profile.tier_name),
            "equity_krw": float(account_state.get("equity_krw") or 0.0),
            "limits": {
                "max_target_position_pct": float(capital_profile.max_target_position_pct),
                "max_position_pct_per_symbol": float(capital_profile.max_position_pct_per_symbol),
            },
            "chosen_target_pct": float(execution_plan.final_numbers.target_position_pct),
            "reason": (
                f"decision={activation_decision_effective}, "
                f"cap_target={float(capital_profile.max_target_position_pct):.1f}, "
                f"risk_max={float(outputs.risk.max_position_pct):.1f}"
            ),
        }
        inputs_hash_payload = _build_inputs_hash_payload(
            slot_key=slot_key,
            symbol=str(resolved_final_plan.symbol),
            allowed_symbols=[str(x) for x in list(fact_pack.get("allowed_symbols") or [])],
            evaluated=[x for x in list(fact_pack.get("evaluated") or []) if isinstance(x, Mapping)],
            activation_checks=gate_checks,
            cost_model=cost_model,
        )
        inputs_hash = _stable_hash(inputs_hash_payload)
        plan_payload = {
            "slot_key": slot_key,
            "meeting_id": str(meeting_id),
            "symbol": resolved_final_plan.symbol,
            "target_position_pct": float(execution_plan.final_numbers.target_position_pct),
            "valid_from_kst": resolved_final_plan.valid_from_kst,
            "valid_to_kst": resolved_final_plan.valid_to_kst,
            "constraints": resolved_final_plan.constraints,
            "notes": plan_notes,
            "allowed_actions": dict(resolved_allowed_actions),
            "rebalance_band_pct": float(execution_plan.final_numbers.rebalance_band_pct),
            "cooldown_minutes": int(execution_plan.final_numbers.cooldown_minutes),
            "rationale": resolved_final_plan.rationale,
            "conflict_resolution": resolved_final_plan.conflict_resolution,
            "evidence_refs": resolved_final_plan.evidence_refs,
            "open_questions": resolved_final_plan.open_questions,
            "activation_status": activation_status,
            "activation_gate": activation_gate,
            "plan_version": "v2.0.0+governance",
            "inputs_hash": str(inputs_hash),
            "created_at_kst": _now_kst().isoformat(),
            "final_trade_plan": resolved_final_plan.model_dump(),
            "final_trade_plan_v2": final_plan_v2.model_dump(),
            "execution_plan": execution_plan.model_dump(),
            "allocator_result": allocator_result,
            "cost_model": cost_model,
            "paper_live_policy": paper_live_policy,
        }

        repo.update_meeting_session(
            meeting_id=meeting_id,
            status="CLOSED",
            ended_at=ended_at,
            summary=outputs.secretary_minutes,
            decisions={
                "trade_plan": resolved_final_plan.model_dump(),
                "trade_plan_v2": final_plan_v2.model_dump(),
                "execution_plan": execution_plan.model_dump(),
                "plan_payload": plan_payload,
                "activation_status": activation_status,
                "activation_gate": activation_gate,
            },
            action_items={"items": action_items},
        )

        summary_event_id = uuid.uuid4()
        repo.insert_event(
            DbEvent(
                event_id=summary_event_id,
                ts=ended_at,
                event_type="MEETING_SUMMARY",
                entity_type="meeting_sessions",
                entity_id=str(meeting_id),
                run_id=None,
                rule_version_id=None,
                payload={
                    "meeting_id": str(meeting_id),
                    "slot_key": slot_key,
                    "summary_short": summary_short,
                    "assistant_minutes": outputs.secretary_minutes,
                    "activation_status": activation_status,
                    "activation_gate": activation_gate,
                    "trade_plan": plan_payload,
                    "llm_meta": {k: asdict(v) for k, v in outputs.llm_meta.items()},
                },
            )
        )
        try:
            secretary_meta = asdict(outputs.llm_meta.get("secretary_agent")) if outputs.llm_meta.get("secretary_agent") else {}
            secretary_meta["llm_meta"] = {k: asdict(v) for k, v in outputs.llm_meta.items()}
            notifier.notify_meeting_summary(
                event_id=summary_event_id,
                meeting_id=str(meeting_id),
                summary=summary_short,
                assistant_minutes=outputs.secretary_minutes,
                assistant_meta=secretary_meta,
                trade_plan=plan_payload,
            )
        except Exception:
            pass

        action_event_id = uuid.uuid4()
        repo.insert_event(
            DbEvent(
                event_id=action_event_id,
                ts=ended_at,
                event_type="MEETING_ACTION_ASSIGNED",
                entity_type="meeting_sessions",
                entity_id=str(meeting_id),
                run_id=None,
                rule_version_id=None,
                payload={"meeting_id": str(meeting_id), "slot_key": slot_key, "items": action_items},
            )
        )
        try:
            notifier.notify_meeting_action_items(event_id=action_event_id, meeting_id=str(meeting_id), items=action_items)
        except Exception:
            pass

        # Trade plan activation gate:
        # decision 기반 실행 모드:
        # - LIVE/PAPER/HOLD 모두 계획은 기록하고 런타임에서 분기한다.
        # - 알 수 없는 decision일 때만 fail-closed로 PROPOSED 처리.
        plan_set_allowed = str(activation_decision_effective).upper() in {"LIVE", "PAPER", "HOLD"}
        plan_event_type = "TRADE_PLAN_SET" if plan_set_allowed else "TRADE_PLAN_PROPOSED"
        trade_plan_event_id = uuid.uuid4()
        repo.insert_event(
            DbEvent(
                event_id=trade_plan_event_id,
                ts=ended_at,
                event_type=plan_event_type,
                entity_type="trade_plans",
                entity_id=slot_key,
                run_id=None,
                rule_version_id=None,
                payload=plan_payload,
            )
        )
        if plan_set_allowed:
            try:
                rationale_lines = [str(v).strip() for v in dict(outputs.final_plan.rationale or {}).values() if str(v).strip()]
                notifier.notify_trade_plan_set(
                    event_id=trade_plan_event_id,
                    meeting_id=str(meeting_id),
                    slot_key=slot_key,
                    symbol=str(plan_payload.get("symbol") or outputs.final_plan.symbol),
                    target_position_pct=float(plan_payload.get("target_position_pct") or 0.0),
                    valid_from_kst=outputs.final_plan.valid_from_kst,
                    valid_to_kst=outputs.final_plan.valid_to_kst,
                    allowed_actions=dict(plan_payload.get("allowed_actions") or {}),
                    rebalance_band_pct=float(plan_payload.get("rebalance_band_pct") or 0.0),
                    cooldown_minutes=int(_as_float(plan_payload.get("cooldown_minutes"), default=0.0)),
                    constraints=dict(plan_payload.get("constraints") or {}),
                    rationale_summary=" | ".join(rationale_lines[:3]),
                )
            except Exception:
                pass
        else:
            repo.insert_event(
                DbEvent(
                    event_id=uuid.uuid4(),
                    ts=ended_at,
                    event_type="TRADE_PLAN_BLOCKED",
                    entity_type="trade_plans",
                    entity_id=slot_key,
                    run_id=None,
                    rule_version_id=None,
                    payload={
                        "slot_key": slot_key,
                        "meeting_id": str(meeting_id),
                        "reason_code": activation_gate.get("reason_code"),
                        "activation_gate": activation_gate,
                    },
                )
            )

        # Governance memory loop: versioned policy snapshot + per-agent task assignment.
        policy_version = _next_policy_version(repo)
        policy_payload = _build_policy_payload(
            policy_version=policy_version,
            slot_key=slot_key,
            outputs=outputs,
            fact_pack=fact_pack,
            activation_gate=activation_gate,
            activation_status=activation_status,
            resolved_plan=plan_payload,
        )
        policy_event_id = uuid.uuid4()
        repo.insert_event(
            DbEvent(
                event_id=policy_event_id,
                ts=ended_at,
                event_type="GOVERNANCE_POLICY_SET" if plan_set_allowed else "GOVERNANCE_POLICY_PROPOSED",
                entity_type="policies",
                entity_id=f"v{policy_version}",
                run_id=None,
                rule_version_id=None,
                payload=policy_payload,
            )
        )
        assigned_tasks = _build_agent_tasks(slot_key=slot_key, outputs=outputs)
        for task in assigned_tasks:
            repo.insert_event(
                DbEvent(
                    event_id=uuid.uuid4(),
                    ts=ended_at,
                    event_type="AGENT_TASK_ASSIGNED",
                    entity_type="agent_tasks",
                    entity_id=str(task.get("task_id")),
                    run_id=None,
                    rule_version_id=None,
                    payload=dict(task),
                )
            )
        try:
            repo.update_meeting_session(
                meeting_id=meeting_id,
                decisions={
                    "trade_plan": outputs.final_plan.model_dump(),
                    "plan_payload": plan_payload,
                    "policy_version": int(policy_version),
                    "activation_status": activation_status,
                    "activation_gate": activation_gate,
                    "assigned_tasks": assigned_tasks,
                },
                action_items={
                    "items": list(action_items)
                    + [
                        {
                            "owner": str(t.get("target_agent")),
                            "action": str(t.get("description")),
                            "due_date": str(t.get("due_ts_kst")),
                        }
                        for t in assigned_tasks
                    ],
                },
            )
        except Exception:
            pass

        if emit is not None:
            emit(
                "summary",
                {
                    "meeting_id": str(meeting_id),
                    "slot_key": slot_key,
                    "ended_at": ended_at.isoformat(),
                    "summary_short": summary_short,
                    "assistant_minutes": outputs.secretary_minutes,
                    "trade_plan": plan_payload,
                    "policy_version": int(policy_version),
                    "activation_status": activation_status,
                    "activation_gate": activation_gate,
                    "assigned_tasks": assigned_tasks,
                },
            )
            emit("done", {"ok": True})

        return slot_key
    except Exception as exc:
        ended_at = _utcnow()
        repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=ended_at,
                event_type="MEETING_FAIL_CLOSED",
                entity_type="meeting_sessions",
                entity_id=str(meeting_id),
                run_id=None,
                rule_version_id=None,
                payload={"meeting_id": str(meeting_id), "slot_key": slot_key, "error": str(exc)[:300]},
            )
        )
        repo.update_meeting_session(
            meeting_id=meeting_id,
            status="CLOSED",
            ended_at=ended_at,
            summary=_clip(f"회의 실행 실패: {exc}", 900),
            decisions={"error": str(exc)[:300]},
            action_items={"items": []},
        )
        if emit is not None:
            emit("run_error", {"error": str(exc)[:300]})
            emit("done", {"ok": False})
        return slot_key


def maybe_run_scheduled_governance_meeting(
    *,
    repo: PostgresRepo,
    notifier: NotificationService,
    rules_raw: Mapping[str, Any],
    force: bool = False,
) -> str | None:
    """Scheduler gate: run only when within meeting window and slot not already processed."""

    now_kst = _now_kst()
    times = get_meeting_times_kst(rules_raw)
    governance_cfg = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    window_min = int((governance_cfg.get("meeting_window_min") or 5))
    catchup_enabled = bool(governance_cfg.get("catchup_enabled", True))
    catchup_lookback_hours = int(governance_cfg.get("catchup_lookback_hours") or 36)

    if force:
        slot_key = f"{now_kst.date().isoformat()} {now_kst.strftime('%H:%M')}"
        if repo.meeting_slot_exists(slot_key=slot_key):
            return slot_key
        if not ensure_prework_ready_for_slot(repo=repo, rules_raw=rules_raw, slot_key=slot_key):
            return None
        run_governance_meeting_now(repo=repo, notifier=notifier, rules_raw=rules_raw, force_slot_key=slot_key, emit=None)
        return slot_key

    # Strict schedule + restart catch-up:
    # 1) near real-time window slot
    # 2) if enabled, the oldest missed regular slot within lookback horizon
    slot_candidates: list[datetime] = []
    for t in times:
        try:
            slot_candidates.append(_slot_dt_for_today_kst(now_kst, t))
        except Exception:
            continue

    now_date = now_kst.date()
    for slot_dt in sorted(slot_candidates):
        delta_min = abs((now_kst - slot_dt).total_seconds()) / 60.0
        if delta_min > float(window_min):
            continue
        slot_key = _slot_key_for_dt(slot_dt)
        if not repo.meeting_slot_exists(slot_key=slot_key):
            if not ensure_prework_ready_for_slot(repo=repo, rules_raw=rules_raw, slot_key=slot_key):
                return None
            run_governance_meeting_now(repo=repo, notifier=notifier, rules_raw=rules_raw, force_slot_key=slot_key, emit=None)
            return slot_key

    if not catchup_enabled:
        return None

    lookback_hours = max(1, int(catchup_lookback_hours))
    start_dt = now_kst - timedelta(hours=lookback_hours)
    day_cursor = start_dt.date()
    due_slots: list[datetime] = []
    while day_cursor <= now_date:
        for t in times:
            try:
                hh, mm = _parse_hhmm(t)
            except Exception:
                continue
            slot_dt = datetime(
                year=day_cursor.year,
                month=day_cursor.month,
                day=day_cursor.day,
                hour=hh,
                minute=mm,
                second=0,
                microsecond=0,
                tzinfo=KST,
            )
            if slot_dt < start_dt or slot_dt > now_kst:
                continue
            due_slots.append(slot_dt)
        day_cursor += timedelta(days=1)

    for slot_dt in sorted(due_slots):
        slot_key = _slot_key_for_dt(slot_dt)
        if repo.meeting_slot_exists(slot_key=slot_key):
            continue
        if not ensure_prework_ready_for_slot(repo=repo, rules_raw=rules_raw, slot_key=slot_key):
            return None
        run_governance_meeting_now(repo=repo, notifier=notifier, rules_raw=rules_raw, force_slot_key=slot_key, emit=None)
        return slot_key
    return None
