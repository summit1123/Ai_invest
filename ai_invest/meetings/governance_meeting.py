from __future__ import annotations

import json
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
from ai_invest.config.llm_router import LLMRoute, llm_route_for_agent
from ai_invest.config.rules_loader import RulesConfig, load_rules
from ai_invest.market_data.features import build_feature_snapshot_from_candles
from ai_invest.market_data.upbit_public import fetch_candles_minutes, fetch_market_snapshot
from ai_invest.notifications.service import NotificationService
from ai_invest.research.rss import fetch_crypto_headlines, summarize_headlines_text
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


def should_block_prework(*, require_prework_reports: bool, prework: Mapping[str, Any]) -> bool:
    if not bool(require_prework_reports):
        return False
    missing = list(prework.get("missing") or []) if isinstance(prework, Mapping) else []
    stale = list(prework.get("stale") or []) if isinstance(prework, Mapping) else []
    return bool(missing or stale)


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
    # Slight bias for BTC ticker
    if str(symbol).endswith("-BTC"):
        score += 0.02
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
    required_constraints: dict[str, float] = Field(default_factory=dict)
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
    constraints: dict[str, float] = Field(default_factory=dict)
    rationale: dict[str, str] = Field(default_factory=dict, description="agent_name -> 1~3문장 근거 요약")
    evidence_refs: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    conflict_resolution: list[str] = Field(default_factory=list)
    notes: str = Field(..., max_length=1200)


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
    merged_constraints: dict[str, float] = {
        **base_constraints,
        **dict(risk.required_constraints or {}),
        **dict(plan.constraints or {}),
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
    max_pos = float(((fact_pack.get("rules") or {}).get("risk") or {}).get("max_position_pct_per_symbol") or 20.0)
    default_target = float(((fact_pack.get("raw_rules_hint") or {}).get("governance") or {}).get("default_target_position_pct") or 10.0)

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
        cooldown_minutes=int(((fact_pack.get("rules") or {}).get("risk") or {}).get("cooldown_minutes_after_trigger") or 180),
        notes="deterministic 초안: 점수 상위 심볼을 기본 비중으로 채택",
    )

    det_risk = RiskDraft(
        veto=bool(recon_status == "FAIL"),
        max_position_pct=float(max_pos),
        max_loss_per_trade_pct=float(((fact_pack.get("rules") or {}).get("risk") or {}).get("max_risk_per_trade_pct") or 0.35),
        max_daily_loss_pct=float(((fact_pack.get("rules") or {}).get("risk") or {}).get("max_daily_loss_pct") or 1.5),
        required_constraints=dict((fact_pack.get("rules") or {}).get("cost_guard") or {}),
        notes="deterministic: 룰 상한을 그대로 적용",
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
        prework = collect_latest_work_reports(repo=repo, agent_names=prework_agents, max_age_minutes=prework_max_age_min)
        prework_cycle_info: dict[str, Any] | None = None

        if list(prework.get("missing") or []) or list(prework.get("stale") or []):
            try:
                cycle = run_agent_work_cycle(repo=repo, rules_raw=rules_raw, meeting_context=slot_key)
                prework_cycle_info = {"cycle_key": cycle.cycle_key, "report_ids": cycle.report_ids}
                prework = collect_latest_work_reports(repo=repo, agent_names=prework_agents, max_age_minutes=prework_max_age_min)
            except Exception as exc:
                prework_cycle_info = {"error": str(exc)[:200]}

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

        evaluated = evaluate_candidates(rules_raw=rules_raw, rules=rules, symbols=symbols)

        pause = repo.fetch_pause_state()
        recon = repo.fetch_latest_reconciliation()

        best_symbol = str((evaluated[0] if evaluated else {}).get("symbol") or (symbols[0] if symbols else "KRW-BTC"))
        try:
            headlines_raw = fetch_crypto_headlines(symbol=best_symbol, limit=12)
        except Exception:
            headlines_raw = []
        headlines_compact = []
        for h in list(headlines_raw)[:10]:
            if not isinstance(h, Mapping):
                continue
            headlines_compact.append(
                {
                    "source": h.get("source"),
                    "title": h.get("title"),
                    "url": h.get("url"),
                    "published_at": h.get("published_at"),
                }
            )
        research_brief = {"headlines": headlines_compact, "headlines_text": summarize_headlines_text(headlines_compact, max_items=6)}

        # Account state snapshot (paper sizing / context)
        try:
            quote_ccy = best_symbol.split("-", 1)[0] if "-" in best_symbol else "KRW"
        except Exception:
            quote_ccy = "KRW"
        cash = repo.fetch_cash_balance(currency=quote_ccy)
        pos = repo.fetch_position(best_symbol)
        account_state = {
            "cash_krw": float(cash),
            "current_qty": float(pos.qty) if pos else 0.0,
            "avg_entry_price": float(pos.avg_entry_price) if (pos and pos.avg_entry_price) else None,
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
        summary_short = f"[{slot_key}] Trade Plan: {outputs.final_plan.symbol} target={outputs.final_plan.target_position_pct:.1f}%"
        action_items = [
            {"owner": "research_agent", "action": "주요 뉴스 원문 확인 및 영향 업데이트", "due_date": str(now_kst.date())},
            {"owner": "ops_manager", "action": "recon/pause/알림 누락 여부 점검", "due_date": str(now_kst.date())},
            {"owner": "quant_strategist", "action": "과매매 방지(cooldown/rebalance band) 파라미터 점검", "due_date": str(now_kst.date())},
        ]

        repo.update_meeting_session(
            meeting_id=meeting_id,
            status="CLOSED",
            ended_at=ended_at,
            summary=outputs.secretary_minutes,
            decisions={"trade_plan": outputs.final_plan.model_dump()},
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
                    "llm_meta": {k: asdict(v) for k, v in outputs.llm_meta.items()},
                },
            )
        )
        try:
            notifier.notify_meeting_summary(
                event_id=summary_event_id,
                meeting_id=str(meeting_id),
                summary=summary_short,
                assistant_minutes=outputs.secretary_minutes,
                assistant_meta={"llm_meta": {k: asdict(v) for k, v in outputs.llm_meta.items()}},
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

        # Trade plan event (runtime consumes latest active by valid_to_kst).
        plan_payload = {
            "slot_key": slot_key,
            "meeting_id": str(meeting_id),
            "symbol": outputs.final_plan.symbol,
            "target_position_pct": float(outputs.final_plan.target_position_pct),
            "valid_from_kst": outputs.final_plan.valid_from_kst,
            "valid_to_kst": outputs.final_plan.valid_to_kst,
            "constraints": outputs.final_plan.constraints,
            "notes": outputs.final_plan.notes,
            # Extended fields (safe judge may start consuming later)
            "allowed_actions": outputs.final_plan.allowed_actions.model_dump(),
            "rebalance_band_pct": float(outputs.final_plan.rebalance_band_pct),
            "cooldown_minutes": int(outputs.final_plan.cooldown_minutes),
            "rationale": outputs.final_plan.rationale,
            "conflict_resolution": outputs.final_plan.conflict_resolution,
            "evidence_refs": outputs.final_plan.evidence_refs,
            "open_questions": outputs.final_plan.open_questions,
        }
        repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=ended_at,
                event_type="TRADE_PLAN_SET",
                entity_type="trade_plans",
                entity_id=slot_key,
                run_id=None,
                rule_version_id=None,
                payload=plan_payload,
            )
        )

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
                },
            )
            emit("done", {"ok": True})

        return slot_key
    except Exception as exc:
        ended_at = _utcnow()
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
    window_min = int(((rules_raw.get("governance") or {}).get("meeting_window_min") or 5) if isinstance(rules_raw, Mapping) else 5)

    hit_slot: str | None = None
    if force:
        hit_slot = now_kst.strftime("%H:%M")
    else:
        for t in times:
            slot_dt = _slot_dt_for_today_kst(now_kst, t)
            delta_min = abs((now_kst - slot_dt).total_seconds()) / 60.0
            if delta_min <= float(window_min):
                hit_slot = t
                break
        if not hit_slot:
            return None

    slot_key = f"{now_kst.date().isoformat()} {hit_slot}"
    if repo.meeting_slot_exists(slot_key=slot_key):
        return slot_key

    # A scheduled slot is not "live"; use deterministic slot_key.
    run_governance_meeting_now(repo=repo, notifier=notifier, rules_raw=rules_raw, force_slot_key=slot_key, emit=None)
    return slot_key
