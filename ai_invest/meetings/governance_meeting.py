from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import threading
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


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return bool(value)
    if value is None:
        return bool(default)
    s = str(value).strip().lower()
    if not s:
        return bool(default)
    return s in {"1", "true", "yes", "y", "on"}


TIME_HORIZON_VALUES = {"intraday", "1d", "swing"}


def _run_with_timeout(
    *,
    fn: Callable[[], Any],
    timeout_sec: int | None,
    label: str,
) -> Any:
    """Run callable with soft timeout.

    On timeout, raise TimeoutError so caller can fail-closed/fallback.
    Worker thread is daemonized to avoid blocking scheduler loop.
    """

    if timeout_sec is None or int(timeout_sec) <= 0:
        return fn()

    q: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def _target() -> None:
        try:
            out = fn()
            try:
                q.put_nowait((True, out))
            except Exception:
                return
        except BaseException as exc:  # noqa: BLE001
            try:
                q.put_nowait((False, exc))
            except Exception:
                return

    t = threading.Thread(target=_target, name=f"gov-timeout-{label}", daemon=True)
    t.start()
    try:
        ok, payload = q.get(timeout=float(timeout_sec))
    except queue.Empty as exc:
        raise TimeoutError(f"{label} timeout after {int(timeout_sec)}s") from exc
    if ok:
        return payload
    raise payload


def _normalize_time_horizon(value: Any, *, default: str = "1d") -> str:
    v = str(value or "").strip().lower()
    if v in TIME_HORIZON_VALUES:
        return v
    d = str(default or "").strip().lower()
    if d in TIME_HORIZON_VALUES:
        return d
    return "1d"


def _symbol_eval_row(*, fact_pack: Mapping[str, Any], symbol: str | None) -> Mapping[str, Any]:
    sym = str(symbol or "").strip().upper()
    rows = [x for x in list(fact_pack.get("evaluated") or []) if isinstance(x, Mapping)]
    if sym:
        for row in rows:
            if str(row.get("symbol") or "").strip().upper() == sym:
                return row
    return rows[0] if rows else {}


def _infer_time_horizon(*, fact_pack: Mapping[str, Any], symbol: str | None = None, fallback: str = "1d") -> str:
    """Infer execution horizon from current market state + recent outcome quality.

    This is used as deterministic fallback and as default when coordinator output omits horizon.
    """
    fallback_h = _normalize_time_horizon(fallback, default="1d")

    row = _symbol_eval_row(fact_pack=fact_pack, symbol=symbol)
    features = (row.get("features") or {}) if isinstance(row, Mapping) else {}
    snapshot = (row.get("snapshot") or {}) if isinstance(row, Mapping) else {}
    atr_pct = _as_float(features.get("atr_pct"), default=0.0)
    spread_bps = _as_float(snapshot.get("spread_bps"), default=0.0)
    vol_z = _as_float(features.get("vol_zscore"), default=0.0)

    learning = (fact_pack.get("learning_context") or {}) if isinstance(fact_pack.get("learning_context"), Mapping) else {}
    outcome_windows = (learning.get("outcome_windows") or {}) if isinstance(learning.get("outcome_windows"), Mapping) else {}
    performance_windows = (
        (learning.get("performance_windows") or {})
        if isinstance(learning.get("performance_windows"), Mapping)
        else {}
    )
    outcomes = (learning.get("recent_outcomes") or {}) if isinstance(learning.get("recent_outcomes"), Mapping) else {}
    short_outcomes = (
        (outcome_windows.get("short") or {})
        if isinstance(outcome_windows.get("short"), Mapping)
        else outcomes
    )
    execution_outcomes = (
        (outcome_windows.get("execution") or {})
        if isinstance(outcome_windows.get("execution"), Mapping)
        else outcomes
    )
    execution_performance = (
        (performance_windows.get("execution") or {})
        if isinstance(performance_windows.get("execution"), Mapping)
        else {}
    )
    short_performance = (
        (performance_windows.get("short") or {})
        if isinstance(performance_windows.get("short"), Mapping)
        else {}
    )
    total_trades = max(0, int(_as_float(short_outcomes.get("total_trades"), default=_as_float(outcomes.get("total_trades"), default=0.0))))
    top_errors = [x for x in list(short_outcomes.get("top_error_types") or []) if isinstance(x, Mapping)]
    cost_error_count = 0
    for row_err in top_errors:
        err = str(row_err.get("error_type") or "").strip().upper()
        if err == "OC_COST_UNDERESTIMATED":
            cost_error_count = max(0, int(_as_float(row_err.get("count"), default=0.0)))
            break
    cost_error_ratio = (float(cost_error_count) / float(total_trades)) if total_trades > 0 else 0.0
    execution_win_rate = float(_as_float(execution_outcomes.get("win_rate_pct"), default=_as_float(outcomes.get("win_rate_pct"), default=0.0)))
    execution_net_after_fees = float(_as_float(execution_performance.get("net_pnl_after_fees"), default=0.0))
    execution_avg_hold_min = float(_as_float(execution_performance.get("avg_hold_minutes"), default=0.0))
    execution_perf_trades = int(_as_float(execution_performance.get("trades_count"), default=0.0))
    short_fee_to_realized = _as_float(short_performance.get("fee_to_realized_ratio"), default=-1.0)

    # 초단타 + 비용잠식 구간에서는 의도적으로 horizon을 늘려 churn을 줄인다.
    if execution_perf_trades >= 6 and execution_net_after_fees < 0.0 and execution_avg_hold_min <= 8.0:
        if atr_pct <= 1.1 and spread_bps <= 4.5:
            return "swing"
        if atr_pct <= 2.0 and spread_bps <= 8.0:
            return "1d"

    # 단기 수수료 비중이 과도하면 빈도 축소.
    if short_fee_to_realized >= 0.7 and atr_pct <= 1.8 and spread_bps <= 7.0:
        return "1d"

    # Cost pain -> prefer slower horizon when microstructure is acceptable.
    if total_trades >= 12 and cost_error_ratio >= 0.30:
        if atr_pct <= 1.8 and spread_bps <= 6.0:
            return "swing"
        return "1d"

    # 최근 실행창 성과가 약하고 변동성이 높지 않으면 빈도 낮춘다.
    if int(_as_float(execution_outcomes.get("total_trades"), default=0.0)) >= 4 and execution_win_rate < 40.0:
        if atr_pct <= 1.8 and spread_bps <= 6.5:
            return "1d"

    # Very noisy tape -> shorter horizon.
    if atr_pct >= 2.4 or spread_bps >= 12.0:
        return "intraday"

    # Calm + liquid tape -> allow swing plan.
    if atr_pct <= 1.0 and spread_bps <= 4.5 and vol_z >= 0.0:
        return "swing"

    # Middle ground -> day horizon.
    if atr_pct <= 1.8 and spread_bps <= 7.0:
        return "1d"

    return fallback_h


def _execution_profile_for_horizon(*, horizon: str, rules: RulesConfig) -> dict[str, int]:
    h = _normalize_time_horizon(horizon, default="1d")
    base_time_stop = max(60, int(_as_float(rules.stop_policy.time_stop_minutes, default=360.0)))
    if h == "intraday":
        return {"min_hold_seconds": 0, "max_hold_minutes": min(base_time_stop, 360)}
    if h == "1d":
        return {"min_hold_seconds": 15 * 60, "max_hold_minutes": max(base_time_stop, 12 * 60)}
    return {"min_hold_seconds": 90 * 60, "max_hold_minutes": max(base_time_stop, 48 * 60)}


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


def _slot_dt_from_key_kst(slot_key: str) -> datetime | None:
    parts = str(slot_key or "").strip().split()
    if len(parts) < 2:
        return None
    date_part = str(parts[0]).strip()
    time_part = str(parts[1]).strip()
    if len(time_part) < 5 or ":" not in time_part:
        return None
    time_hhmm = time_part[:5]
    try:
        dt = datetime.strptime(f"{date_part} {time_hhmm}", "%Y-%m-%d %H:%M")
    except Exception:
        return None
    return dt.replace(tzinfo=KST)


def _normalize_meeting_times(times: Sequence[str], *, on_the_hour_only: bool) -> list[str]:
    normalized: list[str] = []
    for raw in list(times or []):
        try:
            hh, mm = _parse_hhmm(str(raw))
        except Exception:
            continue
        if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
            continue
        if on_the_hour_only and int(mm) != 0:
            continue
        normalized.append(f"{int(hh):02d}:{int(mm):02d}")
    # Keep deterministic order: earliest slot first and no duplicates.
    return sorted(set(normalized), key=lambda x: _parse_hhmm(x))


def default_meeting_times_kst() -> list[str]:
    # 정기 회의 기본값: 6시간 간격(하루 4회).
    return ["00:00", "06:00", "12:00", "18:00"]


def get_meeting_times_kst(rules_raw: Mapping[str, Any]) -> list[str]:
    gov = rules_raw.get("governance") if isinstance(rules_raw, Mapping) else None
    times = (gov or {}).get("daily_meeting_times_kst") if isinstance(gov, Mapping) else None
    on_the_hour_only = bool((gov or {}).get("meeting_on_the_hour_only", True)) if isinstance(gov, Mapping) else True
    if isinstance(times, list):
        norm = _normalize_meeting_times([str(x) for x in times], on_the_hour_only=on_the_hour_only)
        if norm:
            return norm
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
    time_horizon: str = Field("auto", pattern="^(auto|intraday|1d|swing)$")
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
    time_horizon: str = Field("auto", pattern="^(auto|intraday|1d|swing)$")
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
    min_hold_seconds: int = Field(0, ge=0, le=7 * 24 * 3600)
    max_hold_minutes: int = Field(360, ge=1, le=14 * 24 * 60)


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


def _should_enable_inter_slot_realtime_mode(
    *,
    universe_mode: str,
    live_execution_enabled: bool,
    hard_plan_block: bool,
    final_plan_no_trade: bool,
    activation_decision_effective: str,
    conditional_activation: Mapping[str, Any],
) -> bool:
    decision = str(activation_decision_effective or "").upper()
    if str(universe_mode or "").strip().lower() != "live":
        return False
    if not bool(live_execution_enabled):
        return False
    if bool(hard_plan_block) or bool(final_plan_no_trade):
        return False
    if decision not in {"PAPER", "HOLD"}:
        return False
    return bool(conditional_activation.get("enabled"))


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


def _hard_plan_block_from_fact_pack(*, fact_pack: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Compute hard plan block from deterministic runtime-ops state only.

    Governance meeting output(LLM/round outputs) can be conservative by design.
    We keep hard HOLD fail-closed for true operational blockers only:
    - pause_state == true
    - reconciliation_status == FAIL
    """

    ops_state = fact_pack.get("ops_state") if isinstance(fact_pack, Mapping) else {}
    ops_state = ops_state if isinstance(ops_state, Mapping) else {}
    pause = (ops_state.get("pause") or {}) if isinstance(ops_state.get("pause"), Mapping) else {}
    recon = (ops_state.get("latest_reconciliation") or {}) if isinstance(ops_state.get("latest_reconciliation"), Mapping) else {}

    reasons: list[str] = []
    if bool(pause.get("paused")):
        reasons.append("pause_state=true")
    if str(recon.get("status") or "OK").strip().upper() == "FAIL":
        reasons.append("reconciliation_status=FAIL")

    return bool(reasons), reasons


def _contains_no_trade_language(text: str) -> bool:
    src = str(text or "").strip().lower()
    if not src:
        return False
    needles = [
        "no-trade",
        "no trade",
        "execution blocked",
        "실행 금지",
        "거래창 미허용",
        "신규 주문",
        "하드 게이트 미충족",
        "veto=true",
    ]
    return any(n in src for n in needles)


def _final_plan_declares_no_trade(*, final_plan: FinalTradePlan) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    target_zero = float(_as_float(final_plan.target_position_pct, default=0.0)) <= 0.0
    buy_disabled = not bool(final_plan.allowed_actions.buy)
    notes_no_trade = _contains_no_trade_language(str(final_plan.notes or ""))
    if target_zero:
        reasons.append("target_position_pct<=0")
    if target_zero and buy_disabled:
        reasons.append("allowed_actions.buy=false")
    if notes_no_trade:
        reasons.append("notes_no_trade_language")
    return bool(target_zero or notes_no_trade), reasons


def _policy_target_cap_pct(
    *,
    plan: FinalTradePlan,
    quant: QuantPlanDraft,
    risk_max_position_pct: float,
    hard_max_position_pct: float,
) -> float:
    cap = max(0.0, min(float(hard_max_position_pct), float(risk_max_position_pct)))
    plan_target = max(0.0, float(_as_float(plan.target_position_pct, default=0.0)))
    quant_target = max(0.0, float(_as_float(quant.target_position_pct, default=0.0)))
    candidate = float(plan_target if plan_target > 0.0 else quant_target)
    if candidate <= 0.0 or cap <= 0.0:
        return 0.0
    return max(0.0, min(float(candidate), float(cap)))


def _should_recover_final_plan_no_trade(
    *,
    universe_mode: str,
    live_execution_enabled: bool,
    hard_plan_block: bool,
    conditional_activation: Mapping[str, Any],
    final_plan_no_trade_reasons: Sequence[str],
    conditional_hold_target_pct: float,
) -> bool:
    if str(universe_mode or "").strip().lower() != "live":
        return False
    if not bool(live_execution_enabled):
        return False
    if bool(hard_plan_block):
        return False
    if not bool((conditional_activation or {}).get("enabled")):
        return False
    if float(conditional_hold_target_pct) <= 0.0:
        return False
    reasons = {str(x).strip() for x in list(final_plan_no_trade_reasons or []) if str(x).strip()}
    if not reasons:
        return False
    return reasons.issubset({"target_position_pct<=0", "allowed_actions.buy=false"})


def _build_plan_consistency_checks(
    *,
    hard_plan_block: bool,
    hard_plan_block_reasons: Sequence[str],
    soft_plan_block: bool,
    soft_plan_block_reasons: Sequence[str],
    activation_decision_effective: str,
    hold_mode: str | None = None,
    paper_data_collection_applied: bool,
    allowed_actions: Mapping[str, Any],
    target_position_pct: float,
    notes: str,
    no_trade_declared: bool = False,
    no_trade_reasons: Sequence[str] | None = None,
) -> dict[str, Any]:
    buy_allowed = bool((allowed_actions or {}).get("buy"))
    sell_allowed = bool((allowed_actions or {}).get("sell"))
    target_pct = float(_as_float(target_position_pct, default=0.0))
    hold_effective = str(activation_decision_effective or "").upper() == "HOLD"
    conditional_hold_effective = bool(hold_effective and str(hold_mode or "").upper() == "HOLD_CONDITIONAL")
    plan_execution_blocked = bool(hard_plan_block or soft_plan_block)
    notes_indicate_no_trade = _contains_no_trade_language(notes)

    checks = [
        {
            "name": "blocked_plan_must_not_buy",
            "passed": not (plan_execution_blocked and buy_allowed),
            "actual": bool(buy_allowed),
            "required": False if plan_execution_blocked else None,
        },
        {
            "name": "blocked_plan_target_must_be_zero",
            "passed": not (plan_execution_blocked and target_pct > 0.0),
            "actual": float(target_pct),
            "required": 0.0 if plan_execution_blocked else None,
        },
        {
            "name": "hold_decision_must_be_flat",
            "passed": not ((hold_effective and not conditional_hold_effective) and (buy_allowed or sell_allowed or target_pct > 0.0)),
            "actual": {
                "buy": bool(buy_allowed),
                "sell": bool(sell_allowed),
                "target_position_pct": float(target_pct),
            },
            "required": {"buy": False, "sell": False, "target_position_pct": 0.0}
            if (hold_effective and not conditional_hold_effective)
            else None,
        },
        {
            "name": "conditional_hold_must_keep_buy_disabled",
            "passed": not (conditional_hold_effective and buy_allowed),
            "actual": {
                "buy": bool(buy_allowed),
                "sell": bool(sell_allowed),
                "target_position_pct": float(target_pct),
            },
            "required": {"buy": False} if conditional_hold_effective else None,
        },
        {
            "name": "notes_no_trade_must_not_conflict",
            "passed": not (notes_indicate_no_trade and (buy_allowed or target_pct > 0.0)),
            "actual": {
                "notes_indicate_no_trade": bool(notes_indicate_no_trade),
                "buy": bool(buy_allowed),
                "target_position_pct": float(target_pct),
            },
            "required": {"buy": False, "target_position_pct": 0.0} if notes_indicate_no_trade else None,
        },
        {
            "name": "final_no_trade_must_not_be_promoted",
            "passed": not (bool(no_trade_declared) and bool(paper_data_collection_applied) and (buy_allowed or target_pct > 0.0)),
            "actual": {
                "final_no_trade_declared": bool(no_trade_declared),
                "paper_data_collection_applied": bool(paper_data_collection_applied),
                "buy": bool(buy_allowed),
                "target_position_pct": float(target_pct),
            },
            "required": {"buy": False, "target_position_pct": 0.0}
            if bool(no_trade_declared)
            else None,
        },
    ]

    failed = [str(c.get("name") or "") for c in checks if not bool(c.get("passed"))]
    return {
        "passed": not bool(failed),
        "failed_checks": failed,
        "plan_execution_blocked": bool(plan_execution_blocked),
        "hard_plan_block": bool(hard_plan_block),
        "hard_plan_block_reasons": [str(x) for x in list(hard_plan_block_reasons or []) if str(x).strip()],
        "soft_plan_block": bool(soft_plan_block),
        "soft_plan_block_reasons": [str(x) for x in list(soft_plan_block_reasons or []) if str(x).strip()],
        "activation_decision_effective": str(activation_decision_effective or ""),
        "hold_mode": str(hold_mode or ""),
        "paper_data_collection_applied": bool(paper_data_collection_applied),
        "final_no_trade_declared": bool(no_trade_declared),
        "final_no_trade_reasons": [str(x) for x in list(no_trade_reasons or []) if str(x).strip()],
        "resolved_execution": {
            "buy": bool(buy_allowed),
            "sell": bool(sell_allowed),
            "target_position_pct": float(target_pct),
        },
        "checks": checks,
    }


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
    gate = activation_gate if isinstance(activation_gate, Mapping) else {}
    hold_mode = str(gate.get("hold_mode") or "").upper()
    decision_effective = str(gate.get("decision_effective") or gate.get("decision") or "").upper()
    conditional_hold_target_allowed = bool(
        (decision_effective == "HOLD" or hold_mode == "HOLD_CONDITIONAL")
        and (
            bool((conditional_activation or {}).get("enabled"))
            or hold_mode == "HOLD_CONDITIONAL"
            or bool(gate.get("inter_slot_realtime_mode"))
        )
    )
    if bool(final_plan.allowed_actions.buy) or conditional_hold_target_allowed:
        tgt_lo, tgt_hi = _bounded_pair(target * 0.7, max(target, target * 1.3), min_v=0.0, max_v=100.0)
    else:
        tgt_lo, tgt_hi = (0.0, 0.0)
    rb = float(final_plan.rebalance_band_pct)
    rb_lo, rb_hi = _bounded_pair(rb * 0.7, rb * 1.3, min_v=0.0, max_v=50.0)
    cd = int(final_plan.cooldown_minutes)
    cd_lo, cd_hi = _bounded_pair_int(int(cd * 0.5), int(cd * 1.5), min_v=0, max_v=10080)

    side = str(((rules_raw.get("universe") or {}).get("trade_side") or "long_only")).strip().lower()
    direction_bias = "long_only" if side in {"long_only", "long"} else ("short_only" if side in {"short_only", "short"} else "both")
    if conditional_hold_target_allowed:
        mode = "hold"
    elif not bool(final_plan.allowed_actions.buy) and bool(final_plan.allowed_actions.sell):
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
    conditional_activation_map = (
        conditional_activation
        if isinstance(conditional_activation, Mapping)
        else (
            activation_gate.get("conditional_activation")
            if isinstance(activation_gate.get("conditional_activation"), Mapping)
            else {}
        )
    )
    decision = str(activation_gate.get("decision") or "PAPER").upper()
    inter_slot_realtime_mode = bool(activation_gate.get("inter_slot_realtime_mode"))
    live_runtime_eligible = bool(
        bool((conditional_activation_map or {}).get("enabled"))
        and inter_slot_realtime_mode
    ) or decision == "LIVE"
    paper_only_recommended = not bool(live_runtime_eligible)

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

    inferred_horizon = _infer_time_horizon(
        fact_pack=fact_pack,
        symbol=str(final_plan.symbol),
        fallback="1d",
    )
    horizon = _normalize_time_horizon(getattr(final_plan, "time_horizon", None), default=inferred_horizon)

    return FinalTradePlanV2(
        symbol=str(final_plan.symbol),
        intent=PlanIntent(mode=mode, direction_bias=direction_bias, time_horizon=horizon),
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
        conditional_activation=dict(conditional_activation_map or {}),
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
    conditional_hold_target_allowed: bool = False,
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
    # Keep execution target anchored to the resolved plan target.
    # (The range is for policy bounds, not for implicit +30% promotion.)
    base_target = _as_float(final_plan.target_position_pct, default=float(tgt_hi))
    base_target = min(max(float(base_target), float(tgt_lo)), float(tgt_hi))
    chosen_target = min(float(base_target), float(hard_cap))
    chosen_target = max(0.0, float(chosen_target))
    if chosen_target < float(tgt_lo) and float(tgt_lo) <= float(hard_cap):
        chosen_target = float(tgt_lo)
    if (str(activation_decision).upper() == "HOLD" or not bool(final_plan.allowed_actions.buy)) and not bool(
        conditional_hold_target_allowed
    ):
        chosen_target = 0.0

    chosen_rebalance = min(max(float(final_plan.rebalance_band_pct), float(rb_lo)), float(rb_hi))
    chosen_cooldown = min(max(int(final_plan.cooldown_minutes), int(cd_lo)), int(cd_hi))
    horizon = _normalize_time_horizon(plan_v2.intent.time_horizon, default="1d")
    exec_profile = _execution_profile_for_horizon(horizon=horizon, rules=rules)
    chosen_min_hold_seconds = int(exec_profile.get("min_hold_seconds") or 0)
    chosen_max_hold_minutes = int(exec_profile.get("max_hold_minutes") or max(1, int(rules.stop_policy.time_stop_minutes)))

    constraint_min_hold = (plan_v2.constraints or {}).get("min_hold_seconds")
    if constraint_min_hold is None:
        constraint_min_hold = (final_plan.constraints or {}).get("min_hold_seconds")
    if constraint_min_hold is not None:
        chosen_min_hold_seconds = max(0, int(_as_float(constraint_min_hold, default=float(chosen_min_hold_seconds))))

    constraint_max_hold = (plan_v2.constraints or {}).get("max_hold_minutes")
    if constraint_max_hold is None:
        constraint_max_hold = (final_plan.constraints or {}).get("max_hold_minutes")
    if constraint_max_hold is not None:
        chosen_max_hold_minutes = max(1, int(_as_float(constraint_max_hold, default=float(chosen_max_hold_minutes))))

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
            min_hold_seconds=int(chosen_min_hold_seconds),
            max_hold_minutes=int(chosen_max_hold_minutes),
        ),
        gates={
            "spread_bps_max": float(max_spread),
            "min_edge_bps": float(min_edge),
            "max_daily_loss_pct": float(rules.risk.max_daily_loss_pct),
            "max_trades_per_day": int(max(0, int(max_trades_per_day))),
            "regime_trade_allowed": True,
            "paper_only": bool(not (bool(live_execution_enabled) and (decision == "LIVE" or conditional_hold_target_allowed))),
            "activation_decision": decision,
            "conditional_hold_target_allowed": bool(conditional_hold_target_allowed),
            "time_horizon": str(horizon),
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
    # 표본요건은 lookback 대비 과도하게 높으면 영구적으로 gate가 잠긴다.
    # 기본 strict_min_trades를 상한으로 두고, 백테스트 창 길이에 맞춘 현실적 하한을 적용한다.
    qb_cfg = (rules_raw.get("quant_backtest") or {}) if isinstance(rules_raw, Mapping) else {}
    lookback_bars = int(_as_float(qb_cfg.get("lookback_bars"), default=500.0))
    dynamic_strict_floor = max(6, int(round(float(lookback_bars) / 60.0)))
    strict_min_trades_effective = max(int(min_trades), min(int(strict_min_trades), int(dynamic_strict_floor)))

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

    if is_paper and data_collection_enabled and trades_actual < int(strict_min_trades_effective):
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
                "required": int(strict_min_trades_effective),
            },
        ]
        return {
            "enabled": True,
            "passed": False,
            "checks": checks,
            "selected_backtest": dict(bt),
            "reason_code": "POLICY_GATE_INSUFFICIENT_DATA",
            "paper_data_collection_mode": True,
            "strict_min_trades_effective": int(strict_min_trades_effective),
            "strict_min_trades_config": int(strict_min_trades),
            "lookback_bars": int(lookback_bars),
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
        "strict_min_trades_effective": int(strict_min_trades_effective),
        "strict_min_trades_config": int(strict_min_trades),
        "lookback_bars": int(lookback_bars),
        "decision": "LIVE" if bool(passed) else "PAPER",
    }


def _next_prep_slot_dt_kst(*, slot_key: str, times: Sequence[str]) -> datetime:
    slot_dt = _slot_dt_from_key_kst(slot_key)
    if slot_dt is not None:
        hhmm = slot_dt.strftime("%H:%M")
        if hhmm in set(times):
            try:
                return next_slot_kst(slot_dt, times=times, current=hhmm)
            except Exception:
                pass
    now_kst = _now_kst()
    return now_kst.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _build_agent_tasks(
    *,
    slot_key: str,
    outputs: GovernanceOutputs,
    rules_raw: Mapping[str, Any],
) -> list[dict[str, Any]]:
    times = get_meeting_times_kst(rules_raw)
    next_slot_dt = _next_prep_slot_dt_kst(slot_key=slot_key, times=times)
    prep_slot_key = _slot_key_for_dt(next_slot_dt)
    due_ts = next_slot_dt.isoformat()
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
            "payload": {
                "symbol": symbol,
                "focus": "catalyst+risk",
                "prep_for_slot_key": prep_slot_key,
            },
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
                "prep_for_slot_key": prep_slot_key,
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
                "prep_for_slot_key": prep_slot_key,
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
            "payload": {
                "required_ops_gates": list(outputs.ops.required_ops_gates),
                "prep_for_slot_key": prep_slot_key,
            },
        },
    ]


def _build_execution_playbook(
    *,
    slot_key: str,
    outputs: GovernanceOutputs,
    activation_status: str,
    resolved_target_position_pct: float,
    resolved_allowed_actions: Mapping[str, Any],
) -> dict[str, Any]:
    buy_allowed = bool(resolved_allowed_actions.get("buy"))
    sell_allowed = bool(resolved_allowed_actions.get("sell"))
    entry_triggers = [str(x) for x in list(outputs.quant.entry_triggers or []) if str(x).strip()][:8]
    exit_triggers = [str(x) for x in list(outputs.quant.exit_triggers or []) if str(x).strip()][:8]
    risk_constraints = dict(outputs.risk.required_constraints or {})
    ops_gates = [str(x) for x in list(outputs.ops.required_ops_gates or []) if str(x).strip()][:8]

    if not entry_triggers:
        entry_triggers = [
            "스프레드/슬리피지 가드 통과",
            "recon=OK 및 pause=false",
            "alpha 및 레짐 조건 유지",
        ]
    if not exit_triggers:
        exit_triggers = [
            "alpha 약화 또는 모멘텀 둔화",
            "리스크 한도/운영 게이트 위반",
            "시간 만료(valid_to_kst) 또는 정책 HOLD 전환",
        ]

    return {
        "slot_key": str(slot_key),
        "activation_status": str(activation_status),
        "position_plan": {
            "symbol": str(outputs.final_plan.symbol),
            "target_position_pct": float(resolved_target_position_pct),
            "time_horizon": _normalize_time_horizon(getattr(outputs.final_plan, "time_horizon", None), default="1d"),
            "buy_allowed": bool(buy_allowed),
            "sell_allowed": bool(sell_allowed),
            "rebalance_band_pct": float(outputs.final_plan.rebalance_band_pct),
            "cooldown_minutes": int(outputs.final_plan.cooldown_minutes),
        },
        "entry_plan": {
            "when_to_buy": entry_triggers,
            "safety_checks": [
                "ops.veto=false / risk.veto=false",
                "spread_bps <= max_spread_bps_entry",
                "reconciliation_status=OK",
            ],
        },
        "position_management": {
            "while_holding_checks": [
                "매 주기마다 spread/volatility/ops 상태 점검",
                "리밸런싱 밴드 초과 시만 비중 조정",
                "데일리 손실 접근 시 신규진입 중단",
            ],
            "ops_required_gates": ops_gates,
            "risk_constraints": risk_constraints,
        },
        "exit_plan": {
            "when_to_sell_or_reduce": exit_triggers,
            "fail_closed_conditions": [
                "ops.reconciliation_status=FAIL",
                "risk.veto=true 또는 일손실 제한 도달",
                "거버넌스 활성결정이 HOLD로 전환",
            ],
        },
        "next_review_focus": [
            "이번 슬롯의 진입/청산 트리거 적중 여부",
            "체결품질(슬리피지/미체결)과 기대수익 대비 비용",
            "다음 슬롯에서 심볼/비중/쿨다운 수정 필요성",
        ],
    }


def _build_playbook_action_items(
    *,
    today_kst: str,
    playbook: Mapping[str, Any],
) -> list[dict[str, Any]]:
    position_plan = playbook.get("position_plan") if isinstance(playbook, Mapping) else {}
    symbol = str((position_plan or {}).get("symbol") or "")
    target = _as_float((position_plan or {}).get("target_position_pct"), default=0.0)
    return [
        {
            "owner": "quant_strategist",
            "action": f"{symbol} 진입/청산 트리거 로그 점검 및 target {target:.1f}% 적정성 검토",
            "due_date": str(today_kst),
        },
        {
            "owner": "risk_manager",
            "action": "보유 중 축소/청산 조건(손실/변동성/레짐) 충족 여부 점검",
            "due_date": str(today_kst),
        },
        {
            "owner": "ops_manager",
            "action": "체결지연/슬리피지/정합성 게이트 위반 여부 모니터링",
            "due_date": str(today_kst),
        },
        {
            "owner": "research_agent",
            "action": f"{symbol} 뉴스/이슈 변화가 플랜 가정에 미치는 영향 업데이트",
            "due_date": str(today_kst),
        },
    ]


def _date_plus_days(*, ymd: str, days: int) -> str:
    try:
        return (datetime.fromisoformat(str(ymd)).date() + timedelta(days=int(days))).isoformat()
    except Exception:
        return str(ymd)


def _build_improvement_roadmap(
    *,
    today_kst: str,
    symbol: str,
    activation_gate: Mapping[str, Any],
    playbook: Mapping[str, Any],
) -> list[dict[str, Any]]:
    checks = [c for c in list(activation_gate.get("checks") or []) if isinstance(c, Mapping)]
    reason_code = str(activation_gate.get("reason_code") or "").strip() or "UNKNOWN"
    trades_actual = None
    trades_required = None
    for c in checks:
        name = str(c.get("name") or "").strip()
        if name in {"trades_for_strict_gate", "trades"}:
            try:
                trades_actual = int(float(c.get("actual")))
            except Exception:
                trades_actual = None
            try:
                trades_required = int(float(c.get("required")))
            except Exception:
                trades_required = None
            break

    fail_lines = []
    for c in checks:
        if bool(c.get("passed")):
            continue
        fail_lines.append(f"{c.get('name')}: actual={c.get('actual')} / required={c.get('required')}")
    fail_txt = "; ".join(fail_lines[:3]) if fail_lines else "미확인"
    spread_guard = "spread_bps <= max_spread_bps_entry"
    safety_checks = (
        (playbook.get("entry_plan") or {}).get("safety_checks")
        if isinstance(playbook.get("entry_plan"), Mapping)
        else []
    )
    if isinstance(safety_checks, list) and safety_checks:
        spread_guard = str(safety_checks[0])

    stage1_goal = f"게이트 통과를 위한 운영/표본 정합성 확보 ({reason_code})"
    if reason_code == "POLICY_GATE_INSUFFICIENT_DATA" and trades_required is not None:
        stage1_goal = f"strict gate 표본 확보 ({trades_actual or 0}/{trades_required} -> {trades_required}+)"

    return [
        {
            "stage": "1단계",
            "owner": "ops_manager",
            "title": stage1_goal,
            "task": (
                f"{symbol} 기준 recon 커버리지와 게이트 실패항목을 고정하고, fail 사유를 회의 메시지에 수치로 남긴다 "
                f"(현재: {fail_txt})"
            ),
            "due_date": str(today_kst),
            "success_criteria": [
                "latest_reconciliation.status=OK",
                "활성화 게이트 fail 체크가 0~1개로 감소",
                "회의록에 실패항목 actual/required 수치가 기록됨",
            ],
        },
        {
            "stage": "2단계",
            "owner": "quant_strategist",
            "title": "진입/청산 품질 개선 (즉시청산 감소)",
            "task": (
                "진입 후 즉시 반대매매를 줄이도록 진입·청산 조건을 보정하고, "
                "보유시간/비용/손익 지표를 동일 슬롯 기준으로 비교한다."
            ),
            "due_date": _date_plus_days(ymd=today_kst, days=1),
            "success_criteria": [
                "중앙 보유시간 증가(기준대비)",
                "수수료 대비 순손익 악화 패턴 감소",
                spread_guard,
            ],
        },
        {
            "stage": "3단계",
            "owner": "risk_manager",
            "title": "손실 제어 + 실행 허용조건 재정렬",
            "task": "risk veto 조건과 micro/data-collection 조건 충돌을 줄이고, 허용/차단 이유를 사람이 읽는 문장으로 고정한다.",
            "due_date": _date_plus_days(ymd=today_kst, days=2),
            "success_criteria": [
                "RG_SIGNAL_CONFLICT 비중 감소",
                "trade_plan allowed_actions와 Safe 결과의 일관성 확보",
                "일손실 한도 접근 시 자동 축소 로직 확인",
            ],
        },
        {
            "stage": "4단계",
            "owner": "governance_coordinator",
            "title": "주간 검증 루프 고정",
            "task": "주간 회고에서 승률/기대값/비용을 기준으로 다음 주 1개 개선항목만 선택하고 실험-검증-반영을 닫는다.",
            "due_date": _date_plus_days(ymd=today_kst, days=3),
            "success_criteria": [
                "weekly_priority 1건 등록",
                "성공기준 수치와 데드라인 명시",
                "다음 회의에서 결과 PASS/FAIL 평가 완료",
            ],
        },
    ]


def _roadmap_to_action_items(*, roadmap: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in roadmap:
        stage = str(step.get("stage") or "").strip()
        owner = str(step.get("owner") or "").strip() or "governance_coordinator"
        title = str(step.get("title") or "").strip()
        task = str(step.get("task") or "").strip()
        due = str(step.get("due_date") or "").strip()
        crit = [str(x).strip() for x in list(step.get("success_criteria") or []) if str(x).strip()]
        crit_txt = "; ".join(crit[:3]) if crit else "미정"
        action_txt = f"[{stage}] {title} | {task} | 완료기준: {crit_txt}"
        out.append({"owner": owner, "action": action_txt, "due_date": due})
    return out


def _render_reader_minutes(
    *,
    slot_key: str,
    activation_status: str,
    plan_symbol: str,
    target_position_pct: float,
    allowed_actions: Mapping[str, Any],
    activation_gate: Mapping[str, Any],
    rationale: Mapping[str, Any],
    playbook: Mapping[str, Any],
    roadmap: Sequence[Mapping[str, Any]],
    llm_minutes_raw: str,
) -> str:
    buy_allowed = bool(allowed_actions.get("buy"))
    sell_allowed = bool(allowed_actions.get("sell"))
    reason_code = str(activation_gate.get("reason_code") or "UNKNOWN")
    checks = [c for c in list(activation_gate.get("checks") or []) if isinstance(c, Mapping)]
    failed_checks = [c for c in checks if not bool(c.get("passed"))]
    position_plan = playbook.get("position_plan") if isinstance(playbook, Mapping) else {}

    rationale_lines = [f"- {k}: {str(v).strip()}" for k, v in dict(rationale or {}).items() if str(v).strip()][:4]
    if not rationale_lines:
        rationale_lines = ["- 미확인"]

    gate_lines = [f"- reason_code: {reason_code}"]
    for c in failed_checks[:6]:
        gate_lines.append(f"- {c.get('name')}: actual={c.get('actual')} / required={c.get('required')}")
    if len(gate_lines) == 1:
        gate_lines.append("- 주요 실패 게이트 없음")

    entry_triggers = []
    exit_triggers = []
    if isinstance(playbook.get("entry_plan"), Mapping):
        entry_triggers = [str(x) for x in list((playbook.get("entry_plan") or {}).get("when_to_buy") or []) if str(x).strip()][:3]
    if isinstance(playbook.get("exit_plan"), Mapping):
        exit_triggers = [str(x) for x in list((playbook.get("exit_plan") or {}).get("when_to_sell_or_reduce") or []) if str(x).strip()][:3]
    if not entry_triggers:
        entry_triggers = ["미확인"]
    if not exit_triggers:
        exit_triggers = ["미확인"]

    bt = activation_gate.get("selected_backtest") if isinstance(activation_gate.get("selected_backtest"), Mapping) else {}
    metric_lines = [
        f"- 백테스트(trades/win/score): {bt.get('trades', '미확인')} / {bt.get('win_rate_pct', '미확인')} / {bt.get('backtest_score', '미확인')}",
        f"- 비용반영 기대값(expectancy_after_cost_pct): {bt.get('expectancy_after_cost_pct', '미확인')}",
        f"- 계획 성향(time_horizon): {str((position_plan or {}).get('time_horizon') or '미확인')}",
        f"- 실행 허용: buy={buy_allowed}, sell={sell_allowed}, target={float(target_position_pct):.1f}%",
    ]

    roadmap_lines: list[str] = []
    for step in list(roadmap or [])[:4]:
        stage = str(step.get("stage") or "").strip()
        owner = str(step.get("owner") or "").strip()
        title = str(step.get("title") or "").strip()
        due = str(step.get("due_date") or "").strip()
        roadmap_lines.append(f"[{stage}] {title} (담당: {owner}, 기한: {due})")
        criteria = [str(x).strip() for x in list(step.get("success_criteria") or []) if str(x).strip()]
        if criteria:
            roadmap_lines.append(f"- 완료기준: {'; '.join(criteria[:3])}")

    llm_ref = _clip(str(llm_minutes_raw or "").strip(), 900)
    if not llm_ref:
        llm_ref = "미확인"

    minutes = "\n".join(
        [
            "1) 이번 슬롯 결론",
            f"- 슬롯: {slot_key}",
            f"- 상태: {activation_status}",
            f"- 실행 플랜: {plan_symbol} / target={float(target_position_pct):.1f}% / buy={buy_allowed} / sell={sell_allowed}",
            "",
            "2) 왜 이렇게 결정했는지 (핵심 근거)",
            *rationale_lines,
            "",
            "3) 현재 제약/게이트",
            *gate_lines,
            "",
            "4) 단계별 개선 플랜",
            *(roadmap_lines or ["- 미확인"]),
            "",
            "5) 다음 슬롯 전 체크 지표",
            f"- 진입 조건(요약): {' / '.join(entry_triggers)}",
            f"- 청산 조건(요약): {' / '.join(exit_triggers)}",
            *metric_lines,
            "",
            "참고) Secretary 원문 요약",
            llm_ref,
        ]
    )
    return _clip(minutes, 3200)


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

    Keep true hard blockers fail-closed here. Soft vetoes are recorded, but in live mode the
    runtime loop can re-evaluate them between meetings.
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

    raw_hint = (fact_pack.get("raw_rules_hint") or {}) if isinstance(fact_pack.get("raw_rules_hint"), Mapping) else {}
    hint_governance = (raw_hint.get("governance") or {}) if isinstance(raw_hint.get("governance"), Mapping) else {}
    hint_universe = (raw_hint.get("universe") or {}) if isinstance(raw_hint.get("universe"), Mapping) else {}
    hint_paper_mode = (raw_hint.get("paper_mode") or {}) if isinstance(raw_hint.get("paper_mode"), Mapping) else {}
    hint_dc = (hint_paper_mode.get("data_collection") or {}) if isinstance(hint_paper_mode.get("data_collection"), Mapping) else {}
    hint_activation_gate = (
        (hint_governance.get("activation_gate") or {}) if isinstance(hint_governance.get("activation_gate"), Mapping) else {}
    )
    hint_conditional_activation = (
        (hint_activation_gate.get("conditional_activation") or {})
        if isinstance(hint_activation_gate.get("conditional_activation"), Mapping)
        else {}
    )
    is_paper_mode = str(hint_universe.get("mode") or "paper").strip().lower() == "paper"
    allow_soft_plan_block_bypass = bool(hint_dc.get("allow_soft_plan_block_bypass", False))
    live_conditional_realtime_enabled = bool((not is_paper_mode) and hint_conditional_activation.get("enabled", True))
    ops_state = (fact_pack.get("ops_state") or {}) if isinstance(fact_pack.get("ops_state"), Mapping) else {}
    pause_state = bool(((ops_state.get("pause") or {}).get("paused") if isinstance(ops_state.get("pause"), Mapping) else False))
    recon_status = str(
        ((ops_state.get("latest_reconciliation") or {}).get("status") if isinstance(ops_state.get("latest_reconciliation"), Mapping) else "OK")
        or "OK"
    ).strip().upper()
    hard_ops_block = bool(pause_state or recon_status == "FAIL")

    soft_veto_present = bool(ops.veto) or bool(risk.veto) or not bool(ops.trade_window_allowed)
    # Hard gate: disable BUY when true hard blockers are present.
    buy_allowed = bool(plan.allowed_actions.buy) and bool(quant.allowed_actions.buy)
    if hard_ops_block:
        buy_allowed = False
    # In paper autonomy mode and live conditional mode, meeting-time soft vetoes stay advisory.
    elif not (is_paper_mode and allow_soft_plan_block_bypass) and not live_conditional_realtime_enabled:
        if soft_veto_present:
            buy_allowed = False

    if bool(is_paper_mode and allow_soft_plan_block_bypass and soft_veto_present):
        conflict_note = "paper 자율모드: soft veto(ops/risk/window)는 참고로 기록하고 실행차단에는 사용하지 않음"
    elif bool(live_conditional_realtime_enabled and soft_veto_present):
        conflict_note = "live 조건부모드: soft veto(ops/risk/window)는 회의 참고치로 기록하고 런타임에서 재평가"
    else:
        conflict_note = ""

    if soft_veto_present:
        if hard_ops_block:
            conflict_note = "하드 게이트: pause/recon FAIL -> buy=false, target=0"
        elif not (is_paper_mode and allow_soft_plan_block_bypass) and not live_conditional_realtime_enabled:
            conflict_note = "하드 게이트: ops/risk veto 또는 trade_window 차단 -> buy=false, target=0"

    if conflict_note:
        conflict = list(plan.conflict_resolution or [])
        conflict.append(conflict_note)
    else:
        conflict = list(plan.conflict_resolution or [])

    if hard_ops_block:
        buy_allowed = False

    # Clamp target to risk max and hard max.
    # In live conditional mode, preserve the policy cap even if meeting-time BUY stays disabled.
    max_pos = min(float(hard_max_position_pct), float(risk.max_position_pct))
    tgt = float(plan.target_position_pct)
    tgt_policy_cap = _policy_target_cap_pct(
        plan=plan,
        quant=quant,
        risk_max_position_pct=float(risk.max_position_pct),
        hard_max_position_pct=float(hard_max_position_pct),
    )
    if hard_ops_block:
        tgt2 = 0.0
    elif buy_allowed:
        tgt2 = float(tgt_policy_cap)
    elif bool(live_conditional_realtime_enabled and soft_veto_present):
        tgt2 = float(tgt_policy_cap)
    else:
        tgt2 = 0.0

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

    if sym != str(plan.symbol or "").strip():
        conflict.append(f"심볼 보정: allowed_symbols 밖 -> {sym}")
    if not buy_allowed and float(tgt2) == 0.0:
        if hard_ops_block:
            conflict.append("하드 게이트: pause/recon FAIL -> buy=false, target=0")
        elif not (is_paper_mode and allow_soft_plan_block_bypass) and not live_conditional_realtime_enabled:
            conflict.append("하드 게이트: ops/risk veto 또는 trade_window 차단 -> buy=false, target=0")
    if bool(live_conditional_realtime_enabled and soft_veto_present and not buy_allowed and float(tgt) <= 0.0 and float(tgt2) > 0.0):
        conflict.append(
            f"live 조건부모드: 회의 target 0.0%를 policy cap {tgt2:.1f}%로 복구하고 진입 타이밍은 런타임에 위임"
        )
    elif float(tgt2) != float(tgt):
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


def _governance_llm_call_timeout_sec(
    *,
    rules_raw: Mapping[str, Any],
    route: LLMRoute | None,
) -> int | None:
    gov_cfg = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    env_raw = str(os.environ.get("GOVERNANCE_LLM_CALL_TIMEOUT_SEC", "")).strip()
    env_timeout = _as_int(env_raw, default=0) if env_raw else 0
    cfg_timeout = _as_int(gov_cfg.get("llm_call_timeout_sec"), default=45)
    hard_cap = env_timeout if env_timeout > 0 else cfg_timeout
    if hard_cap <= 0:
        hard_cap = 45

    route_timeout = None
    if route is not None and route.timeout_sec is not None:
        route_timeout = max(1, int(route.timeout_sec))

    if route_timeout is None:
        return int(hard_cap)
    return int(min(route_timeout, hard_cap))


def _governance_protocol_timeout_sec(*, rules_raw: Mapping[str, Any]) -> int | None:
    gov_cfg = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    env_raw = str(os.environ.get("GOVERNANCE_PROTOCOL_TIMEOUT_SEC", "")).strip()
    if env_raw:
        env_v = _as_int(env_raw, default=0)
        if env_v > 0:
            return int(env_v)
    cfg_v = _as_int(gov_cfg.get("protocol_timeout_sec"), default=300)
    if cfg_v <= 0:
        cfg_v = 300
    return int(cfg_v)


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
    timeout_sec: int | None,
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
        res = _run_with_timeout(
            fn=lambda: Runner.run_sync(agent, text_in, max_turns=4),
            timeout_sec=timeout_sec,
            label=f"{name}_typed",
        )
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
    timeout_sec: int | None,
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
        res = _run_with_timeout(
            fn=lambda: Runner.run_sync(agent, text_in, max_turns=3),
            timeout_sec=timeout_sec,
            label=f"{name}_text",
        )
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
            "universe": {"mode": str(((rules_raw.get("universe") or {}).get("mode") or "paper"))},
            "paper_mode": {
                "data_collection": {
                    "allow_soft_plan_block_bypass": bool(
                        ((rules_raw.get("paper_mode") or {}).get("data_collection") or {}).get(
                            "allow_soft_plan_block_bypass",
                            False,
                        )
                    ),
                }
            },
        },
    }


def _current_daily_account_snapshot(*, repo: PostgresRepo, equity_krw: float, now_kst: datetime) -> dict[str, Any]:
    today_kst = str(now_kst.date().isoformat())
    realized_pnl = 0.0
    fees_paid = 0.0
    trades_count = 0
    pnl_rows = repo.fetch_pnl_daily(limit=3)
    for row in list(pnl_rows or []):
        if str(row.get("day") or "").strip() != today_kst:
            continue
        realized_pnl = float(_as_float(row.get("realized_pnl"), default=0.0))
        fees_paid = float(_as_float(row.get("fees_paid"), default=0.0))
        trades_count = int(_as_float(row.get("trades_count"), default=0.0))
        break
    daily_loss_pct = 0.0
    if float(realized_pnl) < 0.0 and float(equity_krw) > 0.0:
        daily_loss_pct = abs(float(realized_pnl)) / float(equity_krw) * 100.0
    return {
        "account_day_kst": str(today_kst),
        "daily_realized_pnl_krw": float(realized_pnl),
        "daily_fees_paid_krw": float(fees_paid),
        "daily_trades_count": int(trades_count),
        "daily_loss_pct": float(daily_loss_pct),
    }


def _evaluated_score_for_symbol(*, fact_pack: Mapping[str, Any], symbol: str) -> float | None:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    rows = [x for x in list(fact_pack.get("evaluated") or []) if isinstance(x, Mapping)]
    for row in rows:
        if str(row.get("symbol") or "").strip().upper() != sym:
            continue
        try:
            return float(row.get("score"))
        except Exception:
            return None
    return None


def _apply_plan_continuity(
    *,
    plan: FinalTradePlan,
    fact_pack: Mapping[str, Any],
    rules_raw: Mapping[str, Any],
) -> FinalTradePlan:
    """Reduce hourly symbol churn by carrying over the previous slot plan when switch edge is weak."""

    gov = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    cfg = (gov.get("plan_continuity") or {}) if isinstance(gov, Mapping) else {}
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        return plan

    prev = (
        (fact_pack.get("previous_plan_summary") or {})
        if isinstance(fact_pack.get("previous_plan_summary"), Mapping)
        else {}
    )
    prev_symbol = str(prev.get("symbol") or "").strip().upper()
    if not prev_symbol:
        return plan

    curr_symbol = str(plan.symbol or "").strip().upper()
    if not curr_symbol or prev_symbol == curr_symbol:
        return plan

    allowed = set(str(x).strip().upper() for x in list(fact_pack.get("allowed_symbols") or []) if str(x).strip())
    if prev_symbol not in allowed:
        return plan

    account_state = (fact_pack.get("account_state") or {}) if isinstance(fact_pack.get("account_state"), Mapping) else {}
    open_symbols = set(
        str(x).strip().upper()
        for x in list(account_state.get("open_symbols") or [])
        if str(x).strip()
    )

    keep_prev = False
    keep_reason = ""

    if bool(cfg.get("sticky_if_position_open", True)) and prev_symbol in open_symbols:
        keep_prev = True
        keep_reason = "open_position"

    if not keep_prev:
        min_hold_min = int(_as_float(cfg.get("min_hold_minutes"), default=120.0))
        prev_ts = _as_kst_dt(prev.get("created_at_kst") or prev.get("ts") or prev.get("valid_from_kst"))
        if prev_ts is not None:
            age_min = max(0.0, (_now_kst() - prev_ts).total_seconds() / 60.0)
            if age_min < float(min_hold_min):
                keep_prev = True
                keep_reason = f"min_hold({age_min:.0f}<{min_hold_min})"

    if not keep_prev:
        bt_delta_required = float(_as_float(cfg.get("switch_min_backtest_score_delta"), default=2.0))
        curr_bt = _extract_quant_backtest_for_symbol(fact_pack=fact_pack, symbol=curr_symbol)
        prev_bt = _extract_quant_backtest_for_symbol(fact_pack=fact_pack, symbol=prev_symbol)
        if isinstance(curr_bt, Mapping) and isinstance(prev_bt, Mapping):
            curr_score = _as_float(curr_bt.get("backtest_score"), default=-999.0)
            prev_score = _as_float(prev_bt.get("backtest_score"), default=-999.0)
            if float(curr_score - prev_score) < float(bt_delta_required):
                keep_prev = True
                keep_reason = f"bt_delta<{bt_delta_required:.2f}"
        else:
            ev_delta_required = float(_as_float(cfg.get("switch_min_candidate_score_delta"), default=0.15))
            curr_score = _as_float(_evaluated_score_for_symbol(fact_pack=fact_pack, symbol=curr_symbol), default=-999.0)
            prev_score = _as_float(_evaluated_score_for_symbol(fact_pack=fact_pack, symbol=prev_symbol), default=-999.0)
            if float(curr_score - prev_score) < float(ev_delta_required):
                keep_prev = True
                keep_reason = f"candidate_delta<{ev_delta_required:.2f}"

    if not keep_prev:
        return plan

    conflict = [str(x) for x in list(plan.conflict_resolution or []) if str(x).strip()][:15]
    conflict.append(f"plan continuity 적용: symbol {curr_symbol} -> {prev_symbol} ({keep_reason})")
    notes = _clip(
        "\n".join(
            [
                str(plan.notes or "").strip(),
                f"[plan_continuity] previous={prev_symbol}, current={curr_symbol}, reason={keep_reason}",
            ]
        ),
        1200,
    )
    return plan.model_copy(
        update={
            "symbol": str(prev_symbol),
            "conflict_resolution": conflict[:16],
            "notes": notes,
        }
    )


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
    learning_ctx = fact_pack.get("learning_context") if isinstance(fact_pack.get("learning_context"), Mapping) else {}
    recent_outcomes = (
        (learning_ctx.get("recent_outcomes") or {})
        if isinstance(learning_ctx.get("recent_outcomes"), Mapping)
        else {}
    )
    outcome_windows = (
        (learning_ctx.get("outcome_windows") or {})
        if isinstance(learning_ctx.get("outcome_windows"), Mapping)
        else {}
    )
    execution_outcomes = (
        (outcome_windows.get("execution") or {})
        if isinstance(outcome_windows.get("execution"), Mapping)
        else recent_outcomes
    )
    short_outcomes = (
        (outcome_windows.get("short") or {})
        if isinstance(outcome_windows.get("short"), Mapping)
        else recent_outcomes
    )
    recent_decision_health = (
        (learning_ctx.get("recent_decision_health") or {})
        if isinstance(learning_ctx.get("recent_decision_health"), Mapping)
        else {}
    )
    decision_windows = (
        (learning_ctx.get("decision_windows") or {})
        if isinstance(learning_ctx.get("decision_windows"), Mapping)
        else {}
    )
    execution_decision_health = (
        (decision_windows.get("execution") or {})
        if isinstance(decision_windows.get("execution"), Mapping)
        else recent_decision_health
    )
    short_decision_health = (
        (decision_windows.get("short") or {})
        if isinstance(decision_windows.get("short"), Mapping)
        else recent_decision_health
    )
    execution_quality_windows = (
        (learning_ctx.get("execution_quality_windows") or {})
        if isinstance(learning_ctx.get("execution_quality_windows"), Mapping)
        else {}
    )
    execution_quality = (
        (execution_quality_windows.get("execution") or {})
        if isinstance(execution_quality_windows.get("execution"), Mapping)
        else (learning_ctx.get("recent_execution_quality") or {})
    )
    latest_priority = (
        (learning_ctx.get("latest_weekly_priority") or {})
        if isinstance(learning_ctx.get("latest_weekly_priority"), Mapping)
        else {}
    )
    meeting_lessons = [x for x in list(learning_ctx.get("recent_meeting_lessons") or []) if isinstance(x, Mapping)]
    latest_meeting_lesson = meeting_lessons[0] if meeting_lessons else {}
    latest_meeting_summary = _clip(str((latest_meeting_lesson or {}).get("summary") or "").strip(), 220)
    recent_trades = int(_as_float(recent_outcomes.get("total_trades"), default=0.0))
    recent_win_rate = float(_as_float(recent_outcomes.get("win_rate_pct"), default=0.0))
    execution_trades = int(_as_float(execution_outcomes.get("total_trades"), default=0.0))
    execution_win_rate = float(_as_float(execution_outcomes.get("win_rate_pct"), default=0.0))
    short_trades = int(_as_float(short_outcomes.get("total_trades"), default=0.0))
    short_win_rate = float(_as_float(short_outcomes.get("win_rate_pct"), default=0.0))
    decision_execution_total = int(_as_float(execution_decision_health.get("total_decisions"), default=0.0))
    decision_short_total = int(_as_float(short_decision_health.get("total_decisions"), default=0.0))
    decision_hold_ratio = float(_as_float(recent_decision_health.get("hold_ratio_pct"), default=0.0))
    decision_buy_ratio = float(_as_float(recent_decision_health.get("buy_ratio_pct"), default=0.0))
    decision_window_label = str(recent_decision_health.get("window_label") or "")
    decision_top_reasons = [x for x in list(recent_decision_health.get("top_reason_codes") or []) if isinstance(x, Mapping)]
    decision_top_reason = (
        str((decision_top_reasons[0] if decision_top_reasons else {}).get("reason_code") or "").strip().upper()
    )
    decision_over_blocked = bool(recent_decision_health.get("over_blocked"))
    execution_avg_spread = _as_float(execution_quality.get("avg_spread_bps_at_submit"), default=-1.0)
    execution_avg_slippage = _as_float(execution_quality.get("avg_slippage_bps_vs_submit"), default=-1.0)
    execution_samples = int(_as_float(execution_quality.get("samples"), default=0.0))
    top_error_items = [x for x in list(short_outcomes.get("top_error_types") or []) if isinstance(x, Mapping)]
    top_error_txt = ", ".join(
        f"{str(x.get('error_type') or '').strip()}:{int(_as_float(x.get('count'), default=0.0))}"
        for x in top_error_items[:3]
        if str(x.get("error_type") or "").strip()
    )
    execution_window_label = str(execution_outcomes.get("window_label") or "execution")
    short_window_label = str(short_outcomes.get("window_label") or "short")

    # Research deterministic
    research_brief_map = (fact_pack.get("research_brief") or {}) if isinstance(fact_pack.get("research_brief"), Mapping) else {}
    headlines = research_brief_map.get("headlines") if isinstance(research_brief_map, Mapping) else None
    headlines = list(headlines or [])
    hl_text = summarize_headlines_text(headlines, max_items=6)
    macro_ctx = (
        dict(research_brief_map.get("macro_context") or {})
        if isinstance(research_brief_map.get("macro_context"), Mapping)
        else {}
    )
    macro_risk_mode = str(macro_ctx.get("risk_mode") or "").strip().upper()
    fg = (
        dict(macro_ctx.get("fear_greed_index") or {})
        if isinstance(macro_ctx.get("fear_greed_index"), Mapping)
        else {}
    )
    cm = (
        dict(macro_ctx.get("crypto_market") or {})
        if isinstance(macro_ctx.get("crypto_market"), Mapping)
        else {}
    )
    fg_value = _as_float(fg.get("value"), default=-1.0)
    btc_dom = _as_float(cm.get("btc_dominance_pct"), default=-1.0)
    macro_line_parts: list[str] = []
    if macro_risk_mode:
        macro_line_parts.append(f"risk_mode={macro_risk_mode}")
    if fg_value >= 0:
        macro_line_parts.append(f"fear_greed={fg_value:.0f}")
    if btc_dom >= 0:
        macro_line_parts.append(f"btc_dominance={btc_dom:.1f}%")
    macro_line = ", ".join(macro_line_parts)
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
    if short_trades >= 5:
        det_risks.append(
            f"학습요약(단기 {short_window_label}): trades={short_trades}, win_rate={short_win_rate:.1f}%"
        )
        if top_error_txt:
            det_risks.append(f"반복 실패유형(단기): {top_error_txt}")
    if execution_trades >= 3:
        det_risks.append(
            f"최근 실행창({execution_window_label}) 성과: trades={execution_trades}, win_rate={execution_win_rate:.1f}%"
        )
    if decision_execution_total >= 20 or decision_short_total >= 20:
        det_risks.append(
            f"의사결정 분포({decision_window_label or 'recent'}): hold={decision_hold_ratio:.1f}%, buy={decision_buy_ratio:.1f}%, top_reason={decision_top_reason or 'N/A'}"
        )
    if decision_over_blocked:
        det_risks.append("게이트 과차단 신호: HOLD 비중이 과도해 entry 임계/스프레드 정책 재점검 필요")
    current_spread_bps = _as_float((best.get("snapshot") or {}).get("spread_bps"), default=-1.0)
    current_atr_pct = _as_float((best.get("features") or {}).get("atr_pct"), default=-1.0)
    if current_spread_bps >= 0 and execution_samples >= 3 and execution_avg_spread >= 0:
        det_risks.append(
            f"실시간/과거 실행비교: now_spread={current_spread_bps:.2f}bps vs exec_avg_spread={execution_avg_spread:.2f}bps, now_atr={current_atr_pct:.2f}%"
        )
    if execution_avg_slippage >= 0 and execution_samples >= 3:
        det_risks.append(
            f"최근 체결품질({execution_quality.get('window_label') or 'execution'}): avg_slippage={execution_avg_slippage:.2f}bps, samples={execution_samples}"
        )
    if latest_meeting_summary:
        det_risks.append(f"최근 회의 교훈: {latest_meeting_summary}")
    if macro_line:
        det_risks.append(f"매크로/지수 컨텍스트: {macro_line}")
    if not det_risks:
        det_risks.append("특이 운영 리스크 없음(기계적 체크 기준)")
    det_research = ResearchGovOutput(
        briefing=_clip(
            "뉴스/시장 브리프: "
            + (hl_text or "주요 헤드라인 없음")
            + (f" | 매크로: {macro_line}" if macro_line else ""),
            480,
        ),
        evidence_cards=det_evidence[:8],
        risk_watchlist=det_risks[:8],
        unknowns=["헤드라인 기반이며 세부 내용(원문) 미검증"] if det_evidence else [],
    )

    inferred_horizon = _infer_time_horizon(
        fact_pack=fact_pack,
        symbol=str(best_symbol),
        fallback="1d",
    )

    # Quant deterministic
    det_quant = QuantPlanDraft(
        symbol=best_symbol if best_symbol in allowed else (next(iter(allowed), best_symbol)),
        target_position_pct=float(min(max_pos, default_target)),
        time_horizon=str(inferred_horizon),
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
        notes=(
            "deterministic 초안: 점수 상위 심볼을 기본 비중으로 채택 "
            f"(capital_tier={cap_tier}, recent_win_rate={recent_win_rate:.1f}%, top_errors={top_error_txt or '없음'})"
            + (f", latest_meeting_lesson={latest_meeting_summary}" if latest_meeting_summary else "")
        ),
    )

    det_risk = RiskDraft(
        veto=bool(recon_status == "FAIL"),
        max_position_pct=float(max_pos),
        max_loss_per_trade_pct=float(((fact_pack.get("rules") or {}).get("risk") or {}).get("max_risk_per_trade_pct") or 0.35),
        max_daily_loss_pct=float(((fact_pack.get("rules") or {}).get("risk") or {}).get("max_daily_loss_pct") or 1.5),
        required_constraints={**dict((fact_pack.get("rules") or {}).get("cost_guard") or {}), "capital_max_position_pct": float(max_pos)},
        notes=f"deterministic: 룰 상한 + 자본 티어(capital_tier={cap_tier})를 적용",
    )
    if latest_priority:
        det_risk = det_risk.model_copy(
            update={
                "notes": _clip(
                    f"{det_risk.notes}; latest_weekly_priority={str(latest_priority.get('priority_title') or '미확인')}",
                    480,
                )
            }
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
        time_horizon=str(inferred_horizon),
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
            "하드 룰: pause/recon FAIL이면 BUY 차단",
            "soft veto(ops/risk/window)는 정책 cap과 제약으로 기록하고, live에서는 런타임 재평가에 위임",
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
    det_final = _apply_plan_continuity(
        plan=det_final,
        fact_pack=fact_pack,
        rules_raw=rules_raw,
    )
    det_final = det_final.model_copy(
        update={
            "time_horizon": _normalize_time_horizon(
                det_final.time_horizon,
                default=_infer_time_horizon(
                    fact_pack=fact_pack,
                    symbol=str(det_final.symbol),
                    fallback=str(inferred_horizon),
                ),
            )
        }
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
    research_timeout = _governance_llm_call_timeout_sec(rules_raw=rules_raw, route=research_route)
    quant_timeout = _governance_llm_call_timeout_sec(rules_raw=rules_raw, route=quant_route)
    risk_timeout = _governance_llm_call_timeout_sec(rules_raw=rules_raw, route=risk_route)
    ops_timeout = _governance_llm_call_timeout_sec(rules_raw=rules_raw, route=ops_route)
    coord_timeout = _governance_llm_call_timeout_sec(rules_raw=rules_raw, route=coord_route)
    sec_timeout = _governance_llm_call_timeout_sec(rules_raw=rules_raw, route=sec_route)

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
            timeout_sec=research_timeout,
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
            timeout_sec=quant_timeout,
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
            timeout_sec=risk_timeout,
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
            timeout_sec=ops_timeout,
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
                timeout_sec=_governance_llm_call_timeout_sec(rules_raw=rules_raw, route=route),
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
            timeout_sec=coord_timeout,
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
    final_plan = _apply_plan_continuity(
        plan=final_plan,
        fact_pack=fact_pack,
        rules_raw=rules_raw,
    )
    final_plan = final_plan.model_copy(
        update={
            "time_horizon": _normalize_time_horizon(
                final_plan.time_horizon,
                default=_infer_time_horizon(
                    fact_pack=fact_pack,
                    symbol=str(final_plan.symbol),
                    fallback="1d",
                ),
            )
        }
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
            timeout_sec=sec_timeout,
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


def _median(values: Sequence[float]) -> float:
    rows = [float(v) for v in list(values or [])]
    if not rows:
        return 0.0
    rows.sort()
    n = len(rows)
    mid = n // 2
    if n % 2 == 1:
        return float(rows[mid])
    return float((rows[mid - 1] + rows[mid]) / 2.0)


def _mean(values: Sequence[float]) -> float:
    rows = [float(v) for v in list(values or [])]
    if not rows:
        return 0.0
    return float(sum(rows) / float(len(rows)))


def _clamp(value: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, value)))


def _percentile(values: Sequence[float], pct: float) -> float:
    rows = [float(v) for v in list(values or [])]
    if not rows:
        return 0.0
    rows.sort()
    if len(rows) == 1:
        return float(rows[0])
    p = _clamp(float(pct), 0.0, 100.0) / 100.0
    idx = p * float(len(rows) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(rows[lo])
    w = float(idx - lo)
    return float(rows[lo] * (1.0 - w) + rows[hi] * w)


def _summarize_recent_decision_health(
    *,
    repo: PostgresRepo,
    now_kst: datetime,
    days: int = 3,
    hours: int | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    window_name: str | None = None,
) -> dict[str, Any]:
    window_hours = int(max(1, int(hours))) if hours is not None else None
    window_days = int(max(1, int(days))) if hours is None else None
    since = now_kst - (timedelta(hours=window_hours) if window_hours is not None else timedelta(days=window_days or 3))
    if rows is None:
        source_rows = (
            repo.fetch_decisions(judge_type="SAFE", limit=2000)
            if hasattr(repo, "fetch_decisions")
            else repo.fetch_latest_decisions(limit=2000)
        )
    else:
        source_rows = [r for r in list(rows or []) if isinstance(r, Mapping)]

    action_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    gate_counts: dict[str, int] = {
        "regime_blocked": 0,
        "risk_veto": 0,
        "ops_veto": 0,
        "market_edge_blocked": 0,
        "market_cost_blocked": 0,
    }
    buy_expected_cost: list[float] = []
    buy_expected_net_edge: list[float] = []

    total = 0
    for r in source_rows:
        ts_kst = _as_kst_dt(r.get("ts"))
        if ts_kst is None or ts_kst < since:
            continue
        total += 1
        action = str(r.get("action") or "").strip().upper() or "UNKNOWN"
        action_counts[action] = int(action_counts.get(action, 0) + 1)

        reasons = [str(x).strip().upper() for x in list(r.get("selected_reasons") or []) if str(x).strip()]
        for rc in reasons:
            reason_counts[rc] = int(reason_counts.get(rc, 0) + 1)

        gates = dict(r.get("gates") or {}) if isinstance(r.get("gates"), Mapping) else {}
        if _as_bool(gates.get("regime_trade_allowed"), default=True) is False:
            gate_counts["regime_blocked"] = int(gate_counts["regime_blocked"] + 1)
        if _as_bool(gates.get("risk_veto"), default=False):
            gate_counts["risk_veto"] = int(gate_counts["risk_veto"] + 1)
        if _as_bool(gates.get("ops_veto"), default=False):
            gate_counts["ops_veto"] = int(gate_counts["ops_veto"] + 1)
        if _as_bool(gates.get("market_edge_gate_blocked"), default=False):
            gate_counts["market_edge_blocked"] = int(gate_counts["market_edge_blocked"] + 1)
        if _as_bool(gates.get("market_cost_gate_blocked"), default=False):
            gate_counts["market_cost_blocked"] = int(gate_counts["market_cost_blocked"] + 1)

        if action == "BUY":
            ec = _as_float(gates.get("market_expected_cost_bps"), default=float("nan"))
            ne = _as_float(gates.get("market_expected_net_edge_bps"), default=float("nan"))
            if math.isfinite(ec):
                buy_expected_cost.append(float(ec))
            if math.isfinite(ne):
                buy_expected_net_edge.append(float(ne))

    hold_n = int(action_counts.get("HOLD", 0))
    pause_n = int(action_counts.get("PAUSE", 0))
    buy_n = int(action_counts.get("BUY", 0))
    hold_ratio = (float(hold_n) / float(total) * 100.0) if total > 0 else 0.0
    buy_ratio = (float(buy_n) / float(total) * 100.0) if total > 0 else 0.0
    pause_ratio = (float(pause_n) / float(total) * 100.0) if total > 0 else 0.0

    top_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:8]
    top_reason_payload = [
        {
            "reason_code": str(code),
            "count": int(cnt),
            "ratio_pct": float(round((float(cnt) / float(total) * 100.0) if total > 0 else 0.0, 2)),
        }
        for code, cnt in top_reasons
    ]
    dominant_reason = str(top_reason_payload[0]["reason_code"]) if top_reason_payload else ""
    over_blocked = bool(total >= 100 and hold_ratio >= 95.0 and buy_ratio <= 1.5)

    label = str(window_name or ("execution" if window_hours is not None else "short")).strip().lower()
    window_label = f"{window_hours}h" if window_hours is not None else f"{window_days}d"
    return {
        "window_name": str(label),
        "window_label": str(window_label),
        "window_days": int(window_days or 0),
        "window_hours": int(window_hours or 0),
        "total_decisions": int(total),
        "action_counts": {str(k): int(v) for k, v in action_counts.items()},
        "buy_ratio_pct": float(round(buy_ratio, 2)),
        "hold_ratio_pct": float(round(hold_ratio, 2)),
        "pause_ratio_pct": float(round(pause_ratio, 2)),
        "top_reason_codes": top_reason_payload,
        "dominant_reason_code": str(dominant_reason),
        "gate_block_counts": {str(k): int(v) for k, v in gate_counts.items()},
        "buy_expected_cost_bps_avg": float(round(_mean(buy_expected_cost), 3)) if buy_expected_cost else None,
        "buy_expected_net_edge_bps_avg": float(round(_mean(buy_expected_net_edge), 3)) if buy_expected_net_edge else None,
        "over_blocked": bool(over_blocked),
    }


def _summarize_recent_outcomes(
    *,
    repo: PostgresRepo,
    now_kst: datetime,
    days: int = 3,
    hours: int | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    window_name: str | None = None,
) -> dict[str, Any]:
    window_hours = int(max(1, int(hours))) if hours is not None else None
    window_days = int(max(1, int(days))) if hours is None else None
    since = now_kst - (timedelta(hours=window_hours) if window_hours is not None else timedelta(days=window_days or 3))
    source_rows = [r for r in list(rows or []) if isinstance(r, Mapping)] if rows is not None else repo.fetch_decision_outcomes(limit=1200)
    total = 0
    labels: dict[str, int] = {}
    errors: dict[str, int] = {}
    for r in source_rows:
        close_kst = _as_kst_dt(r.get("ts_close")) or _as_kst_dt(r.get("reviewed_at"))
        if close_kst is None or close_kst < since:
            continue
        total += 1
        lb = str(r.get("outcome_label") or "").strip().upper() or "UNKNOWN"
        labels[lb] = labels.get(lb, 0) + 1
        err = str(r.get("error_type") or "").strip().upper()
        if err:
            errors[err] = errors.get(err, 0) + 1
    loss = int(labels.get("LOSS") or 0)
    win = int(labels.get("WIN") or 0)
    win_rate = (float(win) / float(total) * 100.0) if total > 0 else 0.0
    top_errors = sorted(errors.items(), key=lambda x: x[1], reverse=True)[:5]
    label = str(window_name or ("execution" if window_hours is not None else "short")).strip().lower()
    window_label = f"{window_hours}h" if window_hours is not None else f"{window_days}d"
    return {
        "window_name": str(label),
        "window_label": str(window_label),
        "window_days": int(window_days or 0),
        "window_hours": int(window_hours or 0),
        "total_trades": int(total),
        "win_rate_pct": float(round(win_rate, 2)),
        "labels": {k: int(v) for k, v in labels.items()},
        "top_error_types": [{"error_type": str(k), "count": int(v)} for k, v in top_errors],
        "loss_count": int(loss),
    }


def _summarize_recent_trade_performance(
    *,
    repo: PostgresRepo,
    now_kst: datetime,
    days: int = 3,
    hours: int | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    window_name: str | None = None,
) -> dict[str, Any]:
    window_hours = int(max(1, int(hours))) if hours is not None else None
    window_days = int(max(1, int(days))) if hours is None else None
    since = now_kst - (timedelta(hours=window_hours) if window_hours is not None else timedelta(days=window_days or 3))
    source_rows = [r for r in list(rows or []) if isinstance(r, Mapping)] if rows is not None else repo.fetch_realized_trades(limit=5000)

    trades = 0
    wins_after_fees = 0
    realized_sum = 0.0
    fees_sum = 0.0
    hold_minutes: list[float] = []
    for r in source_rows:
        close_kst = _as_kst_dt(r.get("ts_close"))
        if close_kst is None or close_kst < since:
            continue
        trades += 1
        realized = float(_as_float(r.get("realized_pnl"), default=0.0))
        fees = float(_as_float(r.get("fees_total"), default=0.0))
        realized_sum += realized
        fees_sum += fees
        if (realized - fees) > 0.0:
            wins_after_fees += 1
        open_kst = _as_kst_dt(r.get("ts_open"))
        if open_kst is not None:
            hold_minutes.append(max(0.0, (close_kst - open_kst).total_seconds() / 60.0))

    win_rate = (float(wins_after_fees) / float(trades) * 100.0) if trades > 0 else 0.0
    net_after_fees = float(realized_sum - fees_sum)
    avg_hold = (float(sum(hold_minutes)) / float(len(hold_minutes))) if hold_minutes else 0.0
    fee_to_realized_ratio = (float(fees_sum) / float(abs(realized_sum))) if abs(realized_sum) > 1e-9 else None
    label = str(window_name or ("execution" if window_hours is not None else "short")).strip().lower()
    window_label = f"{window_hours}h" if window_hours is not None else f"{window_days}d"

    return {
        "window_name": str(label),
        "window_label": str(window_label),
        "window_days": int(window_days or 0),
        "window_hours": int(window_hours or 0),
        "trades_count": int(trades),
        "wins_after_fees": int(wins_after_fees),
        "win_rate_pct": float(round(win_rate, 2)),
        "realized_pnl": float(round(realized_sum, 4)),
        "fees_paid": float(round(fees_sum, 4)),
        "net_pnl_after_fees": float(round(net_after_fees, 4)),
        "avg_hold_minutes": float(round(avg_hold, 2)),
        "median_hold_minutes": float(round(_median(hold_minutes), 2)),
        "fee_to_realized_ratio": (float(round(fee_to_realized_ratio, 4)) if fee_to_realized_ratio is not None else None),
    }


def _summarize_recent_execution_quality(
    *,
    repo: PostgresRepo,
    now_kst: datetime,
    days: int = 3,
    hours: int | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    window_name: str | None = None,
) -> dict[str, Any]:
    window_hours = int(max(1, int(hours))) if hours is not None else None
    window_days = int(max(1, int(days))) if hours is None else None
    since = now_kst - (timedelta(hours=window_hours) if window_hours is not None else timedelta(days=window_days or 3))
    if rows is None:
        source_rows = repo.fetch_execution_metrics(limit=5000) if hasattr(repo, "fetch_execution_metrics") else []
    else:
        source_rows = [r for r in list(rows or []) if isinstance(r, Mapping)]

    slip_vals: list[float] = []
    spread_vals: list[float] = []
    fill_vals: list[float] = []
    latency_vals: list[float] = []
    samples = 0
    for r in source_rows:
        ts_kst = _as_kst_dt(r.get("ts_submit")) or _as_kst_dt(r.get("ts_last_fill")) or _as_kst_dt(r.get("ts_first_fill"))
        if ts_kst is None or ts_kst < since:
            continue
        samples += 1
        slip = _as_float(r.get("slippage_bps_vs_submit"), default=float("nan"))
        spread = _as_float(r.get("spread_bps_at_submit"), default=float("nan"))
        fill = _as_float(r.get("filled_ratio"), default=float("nan"))
        lat = _as_float(r.get("latency_ms_submit_to_fill"), default=float("nan"))
        if math.isfinite(slip):
            slip_vals.append(float(slip))
        if math.isfinite(spread):
            spread_vals.append(float(spread))
        if math.isfinite(fill):
            fill_vals.append(float(fill))
        if math.isfinite(lat):
            latency_vals.append(float(lat))

    label = str(window_name or ("execution" if window_hours is not None else "short")).strip().lower()
    window_label = f"{window_hours}h" if window_hours is not None else f"{window_days}d"
    return {
        "window_name": str(label),
        "window_label": str(window_label),
        "window_days": int(window_days or 0),
        "window_hours": int(window_hours or 0),
        "samples": int(samples),
        "avg_slippage_bps_vs_submit": (float(round(_mean(slip_vals), 3)) if slip_vals else None),
        "p90_slippage_bps_vs_submit": (float(round(_percentile(slip_vals, 90.0), 3)) if slip_vals else None),
        "avg_spread_bps_at_submit": (float(round(_mean(spread_vals), 3)) if spread_vals else None),
        "p90_spread_bps_at_submit": (float(round(_percentile(spread_vals, 90.0), 3)) if spread_vals else None),
        "avg_fill_ratio": (float(round(_mean(fill_vals), 4)) if fill_vals else None),
        "p10_fill_ratio": (float(round(_percentile(fill_vals, 10.0), 4)) if fill_vals else None),
        "avg_latency_ms_submit_to_fill": (float(round(_mean(latency_vals), 1)) if latency_vals else None),
    }


def _latest_daily_review_snapshot(*, repo: PostgresRepo) -> dict[str, Any] | None:
    ev = repo.fetch_latest_event(event_type="DAILY_REVIEW_SENT")
    if not isinstance(ev, Mapping):
        return None
    payload = ev.get("payload") if isinstance(ev.get("payload"), Mapping) else {}
    if not isinstance(payload, Mapping):
        return None
    advice = payload.get("improvement_advice") if isinstance(payload.get("improvement_advice"), Mapping) else {}
    return {
        "day": str(payload.get("day") or ""),
        "realized_pnl": float(_as_float(payload.get("realized_pnl"), default=0.0)),
        "fees_paid": float(_as_float(payload.get("fees_paid"), default=0.0)),
        "trades_count": int(_as_float(payload.get("trades_count"), default=0.0)),
        "improvement_title": str(advice.get("improvement_title") or ""),
        "improvement_reason": str(advice.get("improvement_reason") or ""),
        "suggested_changes": [str(x) for x in list(advice.get("suggested_changes") or []) if str(x).strip()][:5],
        "diagnostics": dict(advice.get("diagnostics") or {}) if isinstance(advice, Mapping) else {},
    }


def _meeting_summary_text(summary: Any) -> str:
    if isinstance(summary, str):
        return str(summary).strip()
    if isinstance(summary, Mapping):
        for key in ("assistant_minutes", "summary", "text", "notes"):
            value = summary.get(key)
            text = str(value or "").strip()
            if text:
                return text
    return ""


def _build_recent_meeting_lessons(
    *,
    repo: PostgresRepo,
    now_kst: datetime,
    lookback_days: int,
    max_sessions: int,
    summary_max_chars: int,
) -> list[dict[str, Any]]:
    since = now_kst - timedelta(days=max(1, int(lookback_days)))
    rows = repo.fetch_meeting_sessions(limit=max(30, int(max_sessions) * 8))
    out: list[dict[str, Any]] = []
    for row in rows:
        if len(out) >= int(max_sessions):
            break
        if str(row.get("meeting_type") or "").upper() != "DAILY_STRATEGY":
            continue
        status = str(row.get("status") or "").upper()
        if status == "OPEN":
            continue
        decisions = row.get("decisions") if isinstance(row.get("decisions"), Mapping) else {}
        if bool(decisions.get("auto_closed")):
            continue
        if str(decisions.get("error") or "").strip().upper().startswith("MEETING_"):
            continue
        ended_kst = _as_kst_dt(row.get("ended_at")) or _as_kst_dt(row.get("started_at"))
        if ended_kst is None or ended_kst < since:
            continue
        summary_text = _clip(_meeting_summary_text(row.get("summary")), int(summary_max_chars))
        if not summary_text:
            continue
        final_plan = decisions.get("final_plan") if isinstance(decisions.get("final_plan"), Mapping) else {}
        symbol = str((final_plan or {}).get("symbol") or "").strip()
        target_pct = _as_float((final_plan or {}).get("target_position_pct"), default=0.0)
        activation_status = str((decisions or {}).get("activation_status") or "").strip()
        out.append(
            {
                "meeting_id": str(row.get("meeting_id") or ""),
                "slot_key": str((decisions or {}).get("slot_key") or "").strip(),
                "ended_at_kst": ended_kst.isoformat(),
                "symbol": symbol,
                "target_position_pct": float(target_pct),
                "activation_status": activation_status,
                "summary": summary_text,
            }
        )
    return out


def _build_learning_context(*, repo: PostgresRepo, now_kst: datetime, rules_raw: Mapping[str, Any]) -> dict[str, Any]:
    governance_cfg = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    learning_cfg = (governance_cfg.get("learning_context") or {}) if isinstance(governance_cfg, Mapping) else {}
    windows_cfg = (learning_cfg.get("outcome_windows") or {}) if isinstance(learning_cfg, Mapping) else {}
    meeting_cfg = (learning_cfg.get("meeting_memory") or {}) if isinstance(learning_cfg, Mapping) else {}
    execution_hours = int(_as_float(windows_cfg.get("execution_hours"), default=6.0))
    short_days = int(_as_float(windows_cfg.get("short_days"), default=14.0))
    medium_days = int(_as_float(windows_cfg.get("medium_days"), default=90.0))
    anchor_days = int(_as_float(windows_cfg.get("anchor_days"), default=270.0))
    max_rows = int(_as_float(learning_cfg.get("max_outcome_rows"), default=6000.0))
    min_recent_trades = int(_as_float(learning_cfg.get("recent_window_fallback_min_trades"), default=5.0))
    meeting_memory_enabled = bool(meeting_cfg.get("enabled", True))
    meeting_lookback_days = int(_as_float(meeting_cfg.get("lookback_days"), default=14.0))
    meeting_max_sessions = int(_as_float(meeting_cfg.get("max_sessions"), default=6.0))
    meeting_summary_max_chars = int(_as_float(meeting_cfg.get("summary_max_chars"), default=280.0))

    execution_hours = max(1, execution_hours)
    short_days = max(3, short_days)
    medium_days = max(short_days, medium_days)
    anchor_days = max(medium_days, anchor_days)
    max_rows = max(500, max_rows)
    min_recent_trades = max(1, min_recent_trades)
    meeting_lookback_days = max(1, meeting_lookback_days)
    meeting_max_sessions = max(1, meeting_max_sessions)
    meeting_summary_max_chars = max(120, meeting_summary_max_chars)

    outcome_rows = repo.fetch_decision_outcomes(limit=max_rows)
    trade_rows = repo.fetch_realized_trades(limit=max_rows)
    if hasattr(repo, "fetch_decisions"):
        decision_rows = repo.fetch_decisions(judge_type="SAFE", limit=max_rows)
    elif hasattr(repo, "fetch_latest_decisions"):
        decision_rows = repo.fetch_latest_decisions(limit=max_rows)
    else:
        decision_rows = []
    exec_metric_rows = repo.fetch_execution_metrics(limit=max_rows) if hasattr(repo, "fetch_execution_metrics") else []
    outcome_windows = {
        "execution": _summarize_recent_outcomes(
            repo=repo,
            now_kst=now_kst,
            hours=execution_hours,
            rows=outcome_rows,
            window_name="execution",
        ),
        "short": _summarize_recent_outcomes(
            repo=repo,
            now_kst=now_kst,
            days=short_days,
            rows=outcome_rows,
            window_name="short",
        ),
        "medium": _summarize_recent_outcomes(
            repo=repo,
            now_kst=now_kst,
            days=medium_days,
            rows=outcome_rows,
            window_name="medium",
        ),
        "anchor": _summarize_recent_outcomes(
            repo=repo,
            now_kst=now_kst,
            days=anchor_days,
            rows=outcome_rows,
            window_name="anchor",
        ),
    }
    decision_windows = {
        "execution": _summarize_recent_decision_health(
            repo=repo,
            now_kst=now_kst,
            hours=execution_hours,
            rows=decision_rows,
            window_name="execution",
        ),
        "short": _summarize_recent_decision_health(
            repo=repo,
            now_kst=now_kst,
            days=short_days,
            rows=decision_rows,
            window_name="short",
        ),
        "medium": _summarize_recent_decision_health(
            repo=repo,
            now_kst=now_kst,
            days=medium_days,
            rows=decision_rows,
            window_name="medium",
        ),
        "anchor": _summarize_recent_decision_health(
            repo=repo,
            now_kst=now_kst,
            days=anchor_days,
            rows=decision_rows,
            window_name="anchor",
        ),
    }
    execution_quality_windows = {
        "execution": _summarize_recent_execution_quality(
            repo=repo,
            now_kst=now_kst,
            hours=execution_hours,
            rows=exec_metric_rows,
            window_name="execution",
        ),
        "short": _summarize_recent_execution_quality(
            repo=repo,
            now_kst=now_kst,
            days=short_days,
            rows=exec_metric_rows,
            window_name="short",
        ),
        "medium": _summarize_recent_execution_quality(
            repo=repo,
            now_kst=now_kst,
            days=medium_days,
            rows=exec_metric_rows,
            window_name="medium",
        ),
        "anchor": _summarize_recent_execution_quality(
            repo=repo,
            now_kst=now_kst,
            days=anchor_days,
            rows=exec_metric_rows,
            window_name="anchor",
        ),
    }
    performance_windows = {
        "execution": _summarize_recent_trade_performance(
            repo=repo,
            now_kst=now_kst,
            hours=execution_hours,
            rows=trade_rows,
            window_name="execution",
        ),
        "short": _summarize_recent_trade_performance(
            repo=repo,
            now_kst=now_kst,
            days=short_days,
            rows=trade_rows,
            window_name="short",
        ),
        "medium": _summarize_recent_trade_performance(
            repo=repo,
            now_kst=now_kst,
            days=medium_days,
            rows=trade_rows,
            window_name="medium",
        ),
        "anchor": _summarize_recent_trade_performance(
            repo=repo,
            now_kst=now_kst,
            days=anchor_days,
            rows=trade_rows,
            window_name="anchor",
        ),
    }
    execution_outcomes = outcome_windows["execution"]
    short_outcomes = outcome_windows["short"]
    execution_decisions = decision_windows["execution"]
    short_decisions = decision_windows["short"]
    execution_performance = performance_windows["execution"]
    short_performance = performance_windows["short"]
    execution_quality = execution_quality_windows["execution"]
    short_quality = execution_quality_windows["short"]
    recent_outcomes = execution_outcomes if int(execution_outcomes.get("total_trades") or 0) >= min_recent_trades else short_outcomes
    recent_decision_health = (
        execution_decisions
        if int(execution_decisions.get("total_decisions") or 0) >= min_recent_trades
        else short_decisions
    )
    recent_performance = (
        execution_performance
        if int(execution_performance.get("trades_count") or 0) >= min_recent_trades
        else short_performance
    )
    recent_execution_quality = (
        execution_quality
        if int(execution_quality.get("samples") or 0) >= min_recent_trades
        else short_quality
    )
    recent_meeting_lessons = (
        _build_recent_meeting_lessons(
            repo=repo,
            now_kst=now_kst,
            lookback_days=meeting_lookback_days,
            max_sessions=meeting_max_sessions,
            summary_max_chars=meeting_summary_max_chars,
        )
        if meeting_memory_enabled
        else []
    )
    return {
        "recent_outcomes": dict(recent_outcomes),
        "outcome_windows": {k: dict(v) for k, v in outcome_windows.items()},
        "recent_decision_health": dict(recent_decision_health),
        "decision_windows": {k: dict(v) for k, v in decision_windows.items()},
        "recent_performance": dict(recent_performance),
        "performance_windows": {k: dict(v) for k, v in performance_windows.items()},
        "recent_execution_quality": dict(recent_execution_quality),
        "execution_quality_windows": {k: dict(v) for k, v in execution_quality_windows.items()},
        "latest_daily_review": _latest_daily_review_snapshot(repo=repo),
        "recent_meeting_lessons": list(recent_meeting_lessons),
        "latest_weekly_priority": _latest_weekly_priority_snapshot(repo=repo),
        "settings": {
            "execution_hours": int(execution_hours),
            "short_days": int(short_days),
            "medium_days": int(medium_days),
            "anchor_days": int(anchor_days),
            "max_outcome_rows": int(max_rows),
            "recent_window_fallback_min_trades": int(min_recent_trades),
            "meeting_memory": {
                "enabled": bool(meeting_memory_enabled),
                "lookback_days": int(meeting_lookback_days),
                "max_sessions": int(meeting_max_sessions),
                "summary_max_chars": int(meeting_summary_max_chars),
            },
        },
    }


def _latest_weekly_priority_snapshot(*, repo: PostgresRepo) -> dict[str, Any] | None:
    rows = repo.fetch_strategy_reviews(limit=1)
    if not rows:
        return None
    r = rows[0]
    return {
        "review_id": str(r.get("review_id") or ""),
        "week_start": str(r.get("week_start") or ""),
        "week_end": str(r.get("week_end") or ""),
        "priority_title": str(r.get("priority_title") or ""),
        "hypothesis": str(r.get("hypothesis") or ""),
        "owner": str(r.get("owner") or ""),
        "status": str(r.get("status") or ""),
        "success_criteria": dict(r.get("success_criteria") or {}),
    }


def _close_or_skip_open_meeting(
    *,
    repo: PostgresRepo,
    rules_raw: Mapping[str, Any],
    emit: Callable[[str, Mapping[str, Any]], None] | None,
    incoming_slot_key: str | None = None,
) -> bool:
    """Return True when a currently running meeting should block a new one."""

    governance_cfg = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    window_min = int(_as_float(governance_cfg.get("meeting_window_min"), default=5.0))
    default_stale_min = max(10, window_min * 3)
    stale_min = int(_as_float(governance_cfg.get("max_open_meeting_minutes"), default=float(default_stale_min)))
    now_kst = _now_kst()
    incoming_slot_key_norm = str(incoming_slot_key or "").strip()
    incoming_slot_dt = _slot_dt_from_key_kst(incoming_slot_key_norm) if incoming_slot_key_norm else None

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
        agenda = s.get("agenda")
        agenda_map = agenda if isinstance(agenda, Mapping) else {}
        session_slot_key = str(agenda_map.get("slot_key") or "").strip()
        session_slot_dt = _slot_dt_from_key_kst(session_slot_key) if session_slot_key else None

        # 이전 슬롯의 orphan OPEN 회의는 짧은 유예 후 자동 종료해 정시 슬롯을 우선한다.
        superseded_slot = False
        if incoming_slot_key_norm and session_slot_key and incoming_slot_key_norm != session_slot_key:
            if incoming_slot_dt is not None and session_slot_dt is not None:
                superseded_slot = session_slot_dt < incoming_slot_dt
            else:
                superseded_slot = True
        if superseded_slot and age_min is not None and age_min >= float(window_min):
            ended_at = _utcnow()
            repo.update_meeting_session(
                meeting_id=mid,
                status="CLOSED",
                ended_at=ended_at,
                summary=_clip(f"자동 종료: 이전 슬롯 OPEN 회의 정리({age_min:.1f}분)", 900),
                decisions={
                    "error": "MEETING_SUPERSEDED_BY_NEW_SLOT",
                    "auto_closed": True,
                    "age_min": age_min,
                    "open_slot_key": session_slot_key,
                    "incoming_slot_key": incoming_slot_key_norm,
                },
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
                    payload={
                        "meeting_id": mid,
                        "reason_code": "MEETING_SUPERSEDED_BY_NEW_SLOT",
                        "age_min": age_min,
                        "open_slot_key": session_slot_key,
                        "incoming_slot_key": incoming_slot_key_norm,
                    },
                )
            )
            if emit is not None:
                emit(
                    "run_warning",
                    {
                        "meeting_id": mid,
                        "reason": "superseded_open_auto_closed",
                        "age_min": age_min,
                        "open_slot_key": session_slot_key,
                        "incoming_slot_key": incoming_slot_key_norm,
                    },
                )
            continue

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
                {
                    "reason": "another_meeting_open",
                    "meeting_id": mid,
                    "age_min": age_min,
                    "open_slot_key": session_slot_key,
                    "incoming_slot_key": incoming_slot_key_norm or None,
                },
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

    if _close_or_skip_open_meeting(
        repo=repo,
        rules_raw=rules_raw,
        emit=emit,
        incoming_slot_key=slot_key,
    ):
        return slot_key

    # Governance meeting must consume DB prework outputs (no live universe scan here).
    symbols = [str(s).strip().upper() for s in list(rules.universe.symbols) if str(s).strip()]

    # Valid window (deterministic):
    # - scheduled slot: [slot_dt, next_slot_dt)
    # - live/ad-hoc: [now, now+8h)
    hit_slot: str | None = None
    forced_slot_dt: datetime | None = None
    if force_slot_key:
        forced_slot_dt = _slot_dt_from_key_kst(force_slot_key)
        if forced_slot_dt is not None:
            hit_slot = forced_slot_dt.strftime("%H:%M")
        else:
            parts = str(force_slot_key).split()
            if len(parts) >= 2 and ":" in parts[1]:
                hit_slot = parts[1].strip()

    if hit_slot and hit_slot in set(times):
        vf_kst = forced_slot_dt if forced_slot_dt is not None else _slot_dt_for_today_kst(now_kst, hit_slot)
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
        uv_cfg = (rules_raw.get("universe") or {}) if isinstance(rules_raw, Mapping) else {}
        dyn_cfg = (uv_cfg.get("dynamic") or {}) if isinstance(uv_cfg, Mapping) else {}
        enforce_static_allowlist = bool(dyn_cfg.get("enforce_static_allowlist", False))
        static_symbols = [str(s).strip().upper() for s in list(rules.universe.symbols) if str(s).strip()]
        prework_symbols: list[str] = []
        universe_selection_raw = (
            quant_findings.get("universe_selection")
            if isinstance(quant_findings.get("universe_selection"), Mapping)
            else {}
        )
        if isinstance(universe_selection_raw, Mapping):
            for raw_sym in list(universe_selection_raw.get("symbols") or []):
                sym = str(raw_sym or "").strip().upper()
                if sym:
                    prework_symbols.append(sym)
        evaluated_raw = quant_findings.get("candidates") if isinstance(quant_findings.get("candidates"), list) else []
        evaluated = [x for x in list(evaluated_raw or []) if isinstance(x, Mapping)]
        for row in evaluated:
            sym = str(row.get("symbol") or "").strip().upper()
            if sym:
                prework_symbols.append(sym)
        if not evaluated:
            evaluated = [{"symbol": (symbols[0] if symbols else "KRW-BTC"), "score": -9.0, "snapshot": {}, "features": {}}]

        pause = repo.fetch_pause_state()
        recon = repo.fetch_latest_reconciliation()

        suggested_plan = quant_findings.get("suggested_plan") if isinstance(quant_findings.get("suggested_plan"), Mapping) else {}
        suggested_symbol = str((suggested_plan or {}).get("symbol") or "").strip().upper()
        if suggested_symbol:
            prework_symbols.append(suggested_symbol)
        prework_symbols = list(dict.fromkeys(prework_symbols))
        if enforce_static_allowlist:
            symbols = [s for s in prework_symbols if s in set(static_symbols)] or list(static_symbols)
        else:
            symbols = prework_symbols or list(static_symbols)
        best_symbol = str((suggested_plan or {}).get("symbol") or (evaluated[0] if evaluated else {}).get("symbol") or (symbols[0] if symbols else "KRW-BTC"))
        research_report = prework_reports.get("research_agent") if isinstance(prework_reports.get("research_agent"), Mapping) else {}
        research_findings = dict((research_report or {}).get("findings") or {}) if isinstance(research_report, Mapping) else {}
        headlines_compact = [h for h in list(research_findings.get("headlines") or []) if isinstance(h, Mapping)][:10]
        research_summary = str((research_report or {}).get("summary") or "").strip()
        research_macro = (
            dict(research_findings.get("macro_context") or {})
            if isinstance(research_findings.get("macro_context"), Mapping)
            else {}
        )
        quant_macro = (
            dict(quant_findings.get("macro_context") or {})
            if isinstance(quant_findings.get("macro_context"), Mapping)
            else {}
        )
        macro_context = dict(research_macro or quant_macro)
        research_brief = {
            "headlines": headlines_compact,
            "headlines_text": summarize_headlines_text(headlines_compact, max_items=6) or research_summary,
            "macro_context": macro_context,
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
        portfolio_overview = repo.fetch_portfolio_overview(quote_currency=quote_ccy)
        open_positions = []
        for row in list(portfolio_overview.get("positions") or []):
            if not isinstance(row, Mapping):
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            qty = _as_float(row.get("qty"), default=0.0)
            if sym and qty > 0:
                open_positions.append(
                    {
                        "symbol": sym,
                        "qty": float(qty),
                        "value_krw": float(_as_float(row.get("value_krw"), default=0.0)),
                        "unrealized_pnl_krw": float(_as_float(row.get("unrealized_pnl_krw"), default=0.0)),
                    }
                )
        capital_profile = resolve_capital_policy(
            rules_raw=rules_raw,
            equity_krw=equity,
            default_target_position_pct=float(((rules_raw.get("governance") or {}).get("default_target_position_pct") or 10.0)),
            max_position_pct_per_symbol=float(rules.risk.max_position_pct_per_symbol),
            cooldown_minutes_after_trigger=int(rules.risk.cooldown_minutes_after_trigger),
        )
        daily_account_snapshot = _current_daily_account_snapshot(
            repo=repo,
            equity_krw=float(equity),
            now_kst=now_kst,
        )
        account_state = {
            "cash_krw": float(cash),
            "equity_krw": float(equity),
            "position_value_krw": float(pos_value),
            "current_qty": float(pos.qty) if pos else 0.0,
            "avg_entry_price": float(pos.avg_entry_price) if (pos and pos.avg_entry_price) else None,
            "open_positions": list(open_positions),
            "open_symbols": [str(x.get("symbol") or "") for x in open_positions if str(x.get("symbol") or "").strip()],
            "capital_profile": capital_profile.as_dict(),
            **dict(daily_account_snapshot),
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
        fact_pack["learning_context"] = _build_learning_context(repo=repo, now_kst=now_kst, rules_raw=rules_raw)
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
        prev_plan = repo.fetch_latest_trade_plan(prefer_active=False, lookback_limit=300)
        if isinstance(prev_plan, Mapping):
            prev_slot = str(prev_plan.get("slot_key") or "").strip()
            if prev_slot and prev_slot != str(slot_key):
                fact_pack["previous_plan_summary"] = {
                    "slot_key": prev_slot,
                    "ts": str(prev_plan.get("ts") or ""),
                    "symbol": str(prev_plan.get("symbol") or ""),
                    "target_position_pct": _as_float(prev_plan.get("target_position_pct"), default=0.0),
                    "allowed_actions": dict(prev_plan.get("allowed_actions") or {})
                    if isinstance(prev_plan.get("allowed_actions"), Mapping)
                    else {},
                    "valid_from_kst": str(prev_plan.get("valid_from_kst") or ""),
                    "valid_to_kst": str(prev_plan.get("valid_to_kst") or ""),
                    "activation_status": str(prev_plan.get("activation_status") or ""),
                    "created_at_kst": str(prev_plan.get("created_at_kst") or ""),
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
        # Guard total runtime so a single blocked LLM call does not freeze the scheduler loop.
        protocol_timeout_sec = _governance_protocol_timeout_sec(rules_raw=rules_raw)
        outputs = _run_with_timeout(
            fn=lambda: run_governance_protocol(fact_pack=fact_pack, rules_raw=rules_raw, on_step=on_step),
            timeout_sec=protocol_timeout_sec,
            label="governance_protocol",
        )

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
        is_paper_mode = str(((rules_raw.get("universe") or {}).get("mode") or "paper")).strip().lower() == "paper"
        universe_mode = str(((rules_raw.get("universe") or {}).get("mode") or "paper")).strip().lower()
        force_plan_buy_allowed = bool(data_collection_cfg.get("force_plan_buy_allowed", True))
        force_plan_target_pct = float(_as_float(data_collection_cfg.get("force_plan_target_pct"), default=5.0))
        allow_soft_plan_block_bypass = bool(data_collection_cfg.get("allow_soft_plan_block_bypass", False))
        hard_plan_block, hard_plan_block_reasons = _hard_plan_block_from_fact_pack(fact_pack=fact_pack)
        paper_data_collection_candidate = bool(
            activation_gate.get("paper_data_collection_mode")
            and gate_reason_code == "POLICY_GATE_INSUFFICIENT_DATA"
            and (not hard_plan_block)
        )
        soft_plan_block_reasons: list[str] = []
        if bool(outputs.ops.veto):
            soft_plan_block_reasons.append("ops.veto=true")
        if bool(outputs.risk.veto):
            soft_plan_block_reasons.append("risk.veto=true")
        if not bool(outputs.ops.trade_window_allowed):
            soft_plan_block_reasons.append("ops.trade_window_allowed=false")
        soft_plan_block_raw = bool(soft_plan_block_reasons)
        soft_plan_block = bool(soft_plan_block_raw)
        if is_paper_mode and allow_soft_plan_block_bypass:
            soft_plan_block = False
        final_plan_no_trade_raw, final_plan_no_trade_reasons = _final_plan_declares_no_trade(
            final_plan=outputs.final_plan
        )
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
        conditional_hold_target_pct = _policy_target_cap_pct(
            plan=outputs.final_plan,
            quant=outputs.quant,
            risk_max_position_pct=float(outputs.risk.max_position_pct),
            hard_max_position_pct=min(
                float(capital_profile.max_target_position_pct),
                float(rules.risk.max_position_pct_per_symbol),
            ),
        )
        recoverable_final_plan_no_trade = _should_recover_final_plan_no_trade(
            universe_mode=universe_mode,
            live_execution_enabled=bool(live_execution_enabled),
            hard_plan_block=bool(hard_plan_block),
            conditional_activation=conditional_activation_cfg,
            final_plan_no_trade_reasons=list(final_plan_no_trade_reasons),
            conditional_hold_target_pct=float(conditional_hold_target_pct),
        )
        final_plan_no_trade = bool(final_plan_no_trade_raw and not recoverable_final_plan_no_trade)
        inter_slot_realtime_mode = _should_enable_inter_slot_realtime_mode(
            universe_mode=universe_mode,
            live_execution_enabled=bool(live_execution_enabled),
            hard_plan_block=bool(hard_plan_block),
            final_plan_no_trade=bool(final_plan_no_trade),
            activation_decision_effective=str(activation_decision_effective),
            conditional_activation=conditional_activation_cfg,
        )
        if inter_slot_realtime_mode:
            soft_plan_block = False
            activation_decision_effective = "HOLD"
        plan_execution_blocked = bool(hard_plan_block or soft_plan_block)
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
        activation_gate["hard_plan_block_reasons"] = list(hard_plan_block_reasons)
        activation_gate["soft_plan_block_raw"] = bool(soft_plan_block_raw)
        activation_gate["soft_plan_block"] = bool(soft_plan_block)
        activation_gate["soft_plan_block_reasons"] = list(soft_plan_block_reasons)
        activation_gate["soft_plan_block_bypassed"] = bool(soft_plan_block_raw and not soft_plan_block)
        activation_gate["paper_soft_block_bypass_enabled"] = bool(allow_soft_plan_block_bypass)
        activation_gate["inter_slot_realtime_mode"] = bool(inter_slot_realtime_mode)
        activation_gate["plan_execution_blocked"] = bool(plan_execution_blocked)
        activation_gate["hold_mode"] = str(hold_mode)
        activation_gate["conditional_activation"] = dict(conditional_activation_cfg)
        activation_gate["cap_runtime"] = dict(cap_runtime_seed)
        if inter_slot_realtime_mode:
            activation_gate["inter_slot_realtime_reason"] = "meeting sets policy cap; realtime loop owns entry timing until next meeting"
        if final_plan_no_trade_raw:
            activation_gate["final_plan_no_trade_raw"] = True
            activation_gate["final_plan_no_trade_raw_reasons"] = list(final_plan_no_trade_reasons)
        if recoverable_final_plan_no_trade:
            activation_gate["final_plan_no_trade_recovered"] = True
            activation_gate["final_plan_no_trade_recovered_reasons"] = list(final_plan_no_trade_reasons)
            activation_gate["conditional_hold_target_pct"] = float(conditional_hold_target_pct)
        if final_plan_no_trade:
            activation_gate["final_plan_no_trade_declared"] = True
            activation_gate["final_plan_no_trade_reasons"] = list(final_plan_no_trade_reasons)
        paper_data_collection_applied = bool(paper_data_collection_candidate and (not final_plan_no_trade))
        if bool(paper_data_collection_candidate) and final_plan_no_trade:
            activation_gate["paper_data_collection_suppressed"] = True
            activation_gate["paper_data_collection_suppressed_reason"] = "FINAL_PLAN_NO_TRADE"

        resolved_allowed_actions = outputs.final_plan.allowed_actions.model_dump()
        resolved_target_position_pct = float(outputs.final_plan.target_position_pct)
        if recoverable_final_plan_no_trade:
            resolved_allowed_actions["buy"] = False
            resolved_allowed_actions["sell"] = True
            resolved_target_position_pct = float(conditional_hold_target_pct)
        if final_plan_no_trade:
            resolved_allowed_actions["buy"] = False
            resolved_target_position_pct = 0.0
        if paper_data_collection_applied:
            resolved_allowed_actions["buy"] = bool(force_plan_buy_allowed)
            # 데이터 수집 모드에서는 리스크 감축(SELL) 경로를 항상 열어 둔다.
            resolved_allowed_actions["sell"] = True
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

        if plan_execution_blocked:
            resolved_allowed_actions["buy"] = False
            resolved_target_position_pct = 0.0
            activation_gate = dict(activation_gate)
            effective_soft_reasons = list(soft_plan_block_reasons) if bool(soft_plan_block) else []
            activation_gate["resolved_execution_blocked"] = True
            activation_gate["resolved_execution_blocked_reasons"] = [
                *list(hard_plan_block_reasons),
                *list(effective_soft_reasons),
            ]

        if str(activation_decision_effective).upper() == "HOLD":
            if inter_slot_realtime_mode:
                resolved_allowed_actions["buy"] = False
                resolved_allowed_actions["sell"] = True
                activation_gate = dict(activation_gate)
                activation_gate["conditional_hold_target_pct"] = float(resolved_target_position_pct)
            else:
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
            f"[{slot_key}] {activation_status}: "
            f"{outputs.final_plan.symbol} target={float(resolved_target_position_pct):.1f}% "
            f"(gate={activation_gate.get('reason_code')})"
        )
        execution_playbook = _build_execution_playbook(
            slot_key=slot_key,
            outputs=outputs,
            activation_status=activation_status,
            resolved_target_position_pct=float(resolved_target_position_pct),
            resolved_allowed_actions=resolved_allowed_actions,
        )
        action_items = _build_playbook_action_items(today_kst=str(now_kst.date()), playbook=execution_playbook)
        improvement_roadmap = _build_improvement_roadmap(
            today_kst=str(now_kst.date()),
            symbol=str(outputs.final_plan.symbol),
            activation_gate=activation_gate,
            playbook=execution_playbook,
        )
        action_items.extend(_roadmap_to_action_items(roadmap=improvement_roadmap))
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
        resolved_execution_lines = [
            "[resolved_execution]",
            f"- activation_status={str(activation_status)}",
            f"- decision_effective={str(activation_gate.get('decision_effective') or activation_gate.get('decision') or '')}",
            f"- inter_slot_realtime_mode={bool(inter_slot_realtime_mode)}",
            f"- plan_execution_blocked={bool(plan_execution_blocked)}",
            f"- allowed_actions.buy={bool(resolved_allowed_actions.get('buy'))}",
            f"- allowed_actions.sell={bool(resolved_allowed_actions.get('sell'))}",
            f"- target_position_pct={float(resolved_target_position_pct):.1f}",
        ]
        if plan_execution_blocked:
            blocked_reasons = [str(x).strip() for x in list(hard_plan_block_reasons) + list(soft_plan_block_reasons) if str(x).strip()]
            if blocked_reasons:
                resolved_execution_lines.append(f"- blocked_reasons={','.join(blocked_reasons[:6])}")
        plan_notes = _clip(
            "\n".join([x for x in [str(plan_notes or "").strip(), *resolved_execution_lines] if str(x).strip()]),
            1200,
        )
        consistency_checks = _build_plan_consistency_checks(
            hard_plan_block=bool(hard_plan_block),
            hard_plan_block_reasons=list(hard_plan_block_reasons),
            soft_plan_block=bool(soft_plan_block),
            soft_plan_block_reasons=list(soft_plan_block_reasons),
            activation_decision_effective=str(activation_decision_effective),
            hold_mode=str(hold_mode),
            paper_data_collection_applied=bool(paper_data_collection_applied),
            allowed_actions=resolved_allowed_actions,
            target_position_pct=float(resolved_target_position_pct),
            notes=str(plan_notes),
            no_trade_declared=bool(final_plan_no_trade),
            no_trade_reasons=list(final_plan_no_trade_reasons),
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
            conditional_hold_target_allowed=bool(inter_slot_realtime_mode),
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
            "live_allowed": bool(
                bool(live_execution_enabled)
                and (
                    str(activation_decision_effective).upper() == "LIVE"
                    or bool(inter_slot_realtime_mode)
                )
            ),
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
        resolved_constraints = dict(resolved_final_plan.constraints or {})
        if resolved_constraints.get("max_spread_bps") is None:
            resolved_constraints["max_spread_bps"] = float(rules.cost_guard.max_spread_bps_entry)
        if resolved_constraints.get("max_slippage_bps") is None:
            resolved_constraints["max_slippage_bps"] = float(rules.cost_guard.max_predicted_slippage_bps)
        if resolved_constraints.get("max_position_pct") is None:
            resolved_constraints["max_position_pct"] = float(
                min(float(outputs.risk.max_position_pct), float(rules.risk.max_position_pct_per_symbol))
            )
        if resolved_constraints.get("min_expected_edge_bps") is None:
            resolved_constraints["min_expected_edge_bps"] = float(rules.cost_guard.min_expected_edge_bps)
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
            "time_horizon": str(final_plan_v2.intent.time_horizon),
            "target_position_pct": float(execution_plan.final_numbers.target_position_pct),
            "valid_from_kst": resolved_final_plan.valid_from_kst,
            "valid_to_kst": resolved_final_plan.valid_to_kst,
            "constraints": dict(resolved_constraints),
            "notes": plan_notes,
            "allowed_actions": dict(resolved_allowed_actions),
            "rebalance_band_pct": float(execution_plan.final_numbers.rebalance_band_pct),
            "cooldown_minutes": int(execution_plan.final_numbers.cooldown_minutes),
            "min_hold_seconds": int(execution_plan.final_numbers.min_hold_seconds),
            "max_hold_minutes": int(execution_plan.final_numbers.max_hold_minutes),
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
            "execution_playbook": execution_playbook,
            "improvement_roadmap": list(improvement_roadmap),
            "consistency_checks": consistency_checks,
        }
        formatted_minutes = _render_reader_minutes(
            slot_key=slot_key,
            activation_status=activation_status,
            plan_symbol=str(resolved_final_plan.symbol),
            target_position_pct=float(execution_plan.final_numbers.target_position_pct),
            allowed_actions=dict(resolved_allowed_actions),
            activation_gate=activation_gate,
            rationale=dict(resolved_final_plan.rationale or {}),
            playbook=execution_playbook,
            roadmap=improvement_roadmap,
            llm_minutes_raw=str(outputs.secretary_minutes or ""),
        )

        repo.update_meeting_session(
            meeting_id=meeting_id,
            status="CLOSED",
            ended_at=ended_at,
            summary=formatted_minutes,
            decisions={
                "trade_plan": resolved_final_plan.model_dump(),
                "trade_plan_v2": final_plan_v2.model_dump(),
                "execution_plan": execution_plan.model_dump(),
                "plan_payload": plan_payload,
                "activation_status": activation_status,
                "activation_gate": activation_gate,
                "improvement_roadmap": list(improvement_roadmap),
                "secretary_minutes_raw": str(outputs.secretary_minutes or ""),
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
                    "assistant_minutes": formatted_minutes,
                    "assistant_minutes_raw": str(outputs.secretary_minutes or ""),
                    "activation_status": activation_status,
                    "activation_gate": activation_gate,
                    "trade_plan": plan_payload,
                    "improvement_roadmap": list(improvement_roadmap),
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
                assistant_minutes=formatted_minutes,
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
        now_kst_for_plan = _now_kst()
        plan_window_active = bool(vf_kst <= now_kst_for_plan < vt_kst)
        plan_set_allowed = (
            str(activation_decision_effective).upper() in {"LIVE", "PAPER", "HOLD"}
            and plan_window_active
        )
        if not plan_window_active:
            activation_gate = dict(activation_gate)
            activation_gate["plan_window_active"] = False
            activation_gate["plan_window_now_kst"] = now_kst_for_plan.isoformat()
            activation_gate["plan_window_valid_from_kst"] = vf_kst.isoformat()
            activation_gate["plan_window_valid_to_kst"] = vt_kst.isoformat()
            plan_payload["activation_gate"] = activation_gate
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
                rationale_lines = [
                    f"activation={activation_status}",
                    f"decision={str(activation_gate.get('decision_effective') or activation_gate.get('decision') or '-')}",
                    f"reason={str(activation_gate.get('reason_code') or '-')}",
                    f"hard_block={bool(activation_gate.get('hard_plan_block'))}",
                    f"soft_block={bool(activation_gate.get('soft_plan_block'))}",
                    f"buy_intent={bool(plan_payload.get('allowed_actions', {}).get('buy'))}",
                ]
                hard_reasons = [str(x).strip() for x in list(activation_gate.get("hard_plan_block_reasons") or []) if str(x).strip()]
                soft_reasons = [str(x).strip() for x in list(activation_gate.get("soft_plan_block_reasons") or []) if str(x).strip()]
                if hard_reasons:
                    rationale_lines.append(f"hard_reasons={','.join(hard_reasons[:3])}")
                if soft_reasons:
                    rationale_lines.append(f"soft_reasons={','.join(soft_reasons[:3])}")
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
                    activation_status=str(activation_status),
                    activation_gate=dict(activation_gate or {}),
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
        assigned_tasks = _build_agent_tasks(slot_key=slot_key, outputs=outputs, rules_raw=rules_raw)
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
                    "trade_plan": resolved_final_plan.model_dump(),
                    "trade_plan_v2": final_plan_v2.model_dump(),
                    "execution_plan": execution_plan.model_dump(),
                    "plan_payload": plan_payload,
                    "policy_version": int(policy_version),
                    "activation_status": activation_status,
                    "activation_gate": activation_gate,
                    "assigned_tasks": assigned_tasks,
                    "improvement_roadmap": list(improvement_roadmap),
                    "secretary_minutes_raw": str(outputs.secretary_minutes or ""),
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
                    "assistant_minutes": formatted_minutes,
                    "assistant_minutes_raw": str(outputs.secretary_minutes or ""),
                    "trade_plan": plan_payload,
                    "policy_version": int(policy_version),
                    "activation_status": activation_status,
                    "activation_gate": activation_gate,
                    "assigned_tasks": assigned_tasks,
                    "improvement_roadmap": list(improvement_roadmap),
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
        # Run only after scheduled slot (no early starts), with a small late tolerance window.
        delta_min = (now_kst - slot_dt).total_seconds() / 60.0
        if delta_min < 0.0 or delta_min > float(window_min):
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
        # Catch-up은 "아직 유효한 슬롯"만 실행한다.
        # 과거에 이미 만료된 슬롯을 재생성하면 최신 계획 이해를 혼선시킬 수 있다.
        try:
            slot_end = next_slot_kst(slot_dt, times=times, current=slot_dt.strftime("%H:%M"))
        except Exception:
            slot_end = slot_dt + timedelta(hours=1)
        if slot_end <= now_kst:
            continue
        slot_key = _slot_key_for_dt(slot_dt)
        if repo.meeting_slot_exists(slot_key=slot_key):
            continue
        if not ensure_prework_ready_for_slot(repo=repo, rules_raw=rules_raw, slot_key=slot_key):
            return None
        run_governance_meeting_now(repo=repo, notifier=notifier, rules_raw=rules_raw, force_slot_key=slot_key, emit=None)
        return slot_key
    return None
