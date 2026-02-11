from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from ai_invest.config.llm_router import LLMRoute
from ai_invest.llm.openai_http import OpenAIConfigError, OpenAIRequestError, OpenAITextResult, openai_generate_text
from ai_invest.agents.prompt_contract import strategy_trade_plan_system_prompt, strategy_weekly_priority_system_prompt


def _parse_bool(value: str, *, default: bool = False) -> bool:
    v = str(value or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "y", "on"}


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


def _as_str(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class TradePlanProposal:
    symbol: str
    target_position_pct: float
    constraints: Mapping[str, Any]
    notes: str
    used_llm: bool
    llm_meta: Mapping[str, Any] | None
    error: str | None


@dataclass(frozen=True)
class WeeklyPriorityProposal:
    weekly_priority: str
    hypothesis: str
    owner: str
    deadline: str | None
    success_criteria: Mapping[str, Any]
    used_llm: bool
    llm_meta: Mapping[str, Any] | None
    error: str | None


def propose_trade_plan(
    *,
    candidates: Sequence[Mapping[str, Any]],
    allowed_symbols: Sequence[str],
    default_target_position_pct: float,
    max_position_pct_per_symbol: float,
    cost_guard: Mapping[str, Any] | None = None,
    ops_state: Mapping[str, Any] | None = None,
    research_brief: Mapping[str, Any] | None = None,
    llm_route: LLMRoute | None = None,
) -> TradePlanProposal:
    """Strategy Coordinator (CEO 역할): daily meeting 결과로 Trade Plan 제안.

    - 실시간 실행(Safe Judge)과 분리: 여기서 나온 계획은 events(TRADE_PLAN_SET)로 저장되며,
      실시간 루프는 그 계획을 참조만 한다.
    - LLM은 선택사항이며 실패 시 deterministic fallback을 사용한다.
    """

    allowed_set = {str(s).strip() for s in allowed_symbols if str(s).strip()}
    safe_candidates = [c for c in list(candidates or []) if isinstance(c, Mapping)]
    if not safe_candidates:
        # No candidates: stay flat.
        return TradePlanProposal(
            symbol=(next(iter(allowed_set), "") or ""),
            target_position_pct=0.0,
            constraints=dict(cost_guard or {}),
            notes="후보 데이터 없음: 기본적으로 노출 0% 유지",
            used_llm=False,
            llm_meta=None,
            error="no_candidates",
        )

    def _fallback_best() -> Mapping[str, Any]:
        best = safe_candidates[0]
        for row in safe_candidates[1:]:
            try:
                if float(row.get("score") or 0.0) > float(best.get("score") or 0.0):
                    best = row
            except Exception:
                continue
        return best

    best = _fallback_best()
    best_symbol = _as_str(best.get("symbol"))
    if best_symbol not in allowed_set:
        best_symbol = next(iter(allowed_set), best_symbol)

    # Deterministic default target: if score < 0 => 0%, else min(default, max_position)
    score = _as_float(best.get("score"), default=0.0)
    fallback_target = 0.0 if score < 0 else min(float(default_target_position_pct), float(max_position_pct_per_symbol))

    if llm_route is None:
        # Default: allow LLM, but caller can pass explicit route from rules.yaml.
        llm_enabled = _parse_bool(os.environ.get("STRATEGY_COORDINATOR_LLM_ENABLED", ""), default=True)
        use_llm = llm_enabled and bool(os.environ.get("OPENAI_API_KEY", "").strip())
        llm_route2 = None
    else:
        use_llm = bool(llm_route.enabled) and bool(os.environ.get("OPENAI_API_KEY", "").strip())
        llm_route2 = llm_route

    if not use_llm:
        return TradePlanProposal(
            symbol=best_symbol,
            target_position_pct=float(fallback_target),
            constraints=dict(cost_guard or {}),
            notes=f"deterministic fallback: score 기반 선택 (score={score:.3f})",
            used_llm=False,
            llm_meta=None,
            error=None,
        )

    ctx: dict[str, Any] = {
        "allowed_symbols": list(allowed_set),
        "candidates": [
            {
                "symbol": c.get("symbol"),
                "score": c.get("score"),
                "snapshot": c.get("snapshot"),
                "features": c.get("features"),
            }
            for c in safe_candidates[:12]
        ],
        "defaults": {
            "default_target_position_pct": float(default_target_position_pct),
            "max_position_pct_per_symbol": float(max_position_pct_per_symbol),
        },
        "cost_guard": dict(cost_guard or {}),
        "ops_state": dict(ops_state or {}),
        "research_brief": dict(research_brief or {}),
        "fallback": {"symbol": best_symbol, "target_position_pct": float(fallback_target)},
    }

    system_prompt = strategy_trade_plan_system_prompt()

    user_prompt = "입력 JSON:\n" + _safe_json(ctx)

    try:
        res: OpenAITextResult = openai_generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=(llm_route2.model if llm_route2 else None),
            api_style=(llm_route2.api_style if llm_route2 else None),
            reasoning_effort=(llm_route2.reasoning_effort if llm_route2 else None),
            temperature=(float(llm_route2.temperature) if (llm_route2 and llm_route2.temperature is not None) else 0.2),
            timeout_sec=(int(llm_route2.timeout_sec) if (llm_route2 and llm_route2.timeout_sec) else 60),
        )

        raw = res.text.strip()
        data = json.loads(raw)
        if not isinstance(data, Mapping):
            raise ValueError("non-object json")

        sym = _as_str(data.get("symbol")) or best_symbol
        if sym not in allowed_set:
            sym = best_symbol

        tgt = _as_float(data.get("target_position_pct"), default=fallback_target)
        tgt = max(0.0, min(float(tgt), float(max_position_pct_per_symbol)))

        cons = data.get("constraints") if isinstance(data.get("constraints"), Mapping) else {}
        notes = _as_str(data.get("notes")) or f"LLM proposal (fallback={best_symbol}/{fallback_target:.1f}%)"

        return TradePlanProposal(
            symbol=sym,
            target_position_pct=float(tgt),
            constraints=dict(cons),
            notes=_clip(notes, 800),
            used_llm=True,
            llm_meta={
                "model": res.model,
                "endpoint": res.endpoint,
                "usage": {
                    "input_tokens": getattr(res.usage, "input_tokens", None) if res.usage else None,
                    "output_tokens": getattr(res.usage, "output_tokens", None) if res.usage else None,
                    "total_tokens": getattr(res.usage, "total_tokens", None) if res.usage else None,
                    "response_id": res.response_id,
                },
            },
            error=None,
        )
    except (OpenAIConfigError, OpenAIRequestError, Exception) as exc:
        return TradePlanProposal(
            symbol=best_symbol,
            target_position_pct=float(fallback_target),
            constraints=dict(cost_guard or {}),
            notes=f"LLM 실패로 fallback: score 기반 선택 (score={score:.3f})",
            used_llm=False,
            llm_meta=None,
            error=str(exc)[:200],
        )


def propose_weekly_priority(
    *,
    today_kst: date,
    pnl_daily: Sequence[Mapping[str, Any]],
    realized_trades: Sequence[Mapping[str, Any]],
    execution_metrics: Sequence[Mapping[str, Any]] | None = None,
    reconciliation_checks: Sequence[Mapping[str, Any]] | None = None,
    llm_route: LLMRoute | None = None,
) -> WeeklyPriorityProposal:
    """주간 개선 우선순위 1건 제안(Strategy Coordinator)."""

    # Deterministic fallback: pick the most obvious ops/execution topic if failures exist.
    fallback = WeeklyPriorityProposal(
        weekly_priority="체결/비용(슬리피지) 모니터링 정밀화",
        hypothesis="슬리피지/스프레드 급등 구간에서 진입을 보수적으로 조정하면 손실 구간을 줄일 수 있다.",
        owner="strategy_coordinator",
        deadline=None,
        success_criteria={"metric": "avg_slippage_bps_vs_submit", "target": "decrease", "window_days": 7},
        used_llm=False,
        llm_meta=None,
        error=None,
    )

    if llm_route is None:
        llm_enabled = _parse_bool(os.environ.get("STRATEGY_COORDINATOR_LLM_ENABLED", ""), default=True)
        use_llm = llm_enabled and bool(os.environ.get("OPENAI_API_KEY", "").strip())
        llm_route2 = None
    else:
        use_llm = bool(llm_route.enabled) and bool(os.environ.get("OPENAI_API_KEY", "").strip())
        llm_route2 = llm_route

    if not use_llm:
        return fallback

    ctx: dict[str, Any] = {
        "today_kst": str(today_kst),
        "pnl_daily": list(pnl_daily)[:30],
        "realized_trades": list(realized_trades)[:200],
        "execution_metrics": list(execution_metrics or [])[:200],
        "reconciliation_checks": list(reconciliation_checks or [])[:200],
        "fallback": {
            "weekly_priority": fallback.weekly_priority,
            "hypothesis": fallback.hypothesis,
            "owner": fallback.owner,
            "success_criteria": fallback.success_criteria,
        },
    }

    system_prompt = strategy_weekly_priority_system_prompt()

    user_prompt = "입력 JSON:\n" + _safe_json(ctx)

    try:
        res: OpenAITextResult = openai_generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=(llm_route2.model if llm_route2 else None),
            api_style=(llm_route2.api_style if llm_route2 else None),
            reasoning_effort=(llm_route2.reasoning_effort if llm_route2 else None),
            temperature=(float(llm_route2.temperature) if (llm_route2 and llm_route2.temperature is not None) else 0.2),
            timeout_sec=(int(llm_route2.timeout_sec) if (llm_route2 and llm_route2.timeout_sec) else 90),
        )
        raw = res.text.strip()
        data = json.loads(raw)
        if not isinstance(data, Mapping):
            raise ValueError("non-object json")

        weekly_priority = _as_str(data.get("weekly_priority")) or fallback.weekly_priority
        hypothesis = _as_str(data.get("hypothesis")) or fallback.hypothesis
        owner = _as_str(data.get("owner")) or fallback.owner
        deadline = _as_str(data.get("deadline"))
        if not deadline:
            deadline = None
        success = data.get("success_criteria") if isinstance(data.get("success_criteria"), Mapping) else fallback.success_criteria

        return WeeklyPriorityProposal(
            weekly_priority=_clip(weekly_priority, 220),
            hypothesis=_clip(hypothesis, 600),
            owner=_clip(owner, 64) or "strategy_coordinator",
            deadline=deadline,
            success_criteria=dict(success or {}),
            used_llm=True,
            llm_meta={
                "model": res.model,
                "endpoint": res.endpoint,
                "usage": {
                    "input_tokens": getattr(res.usage, "input_tokens", None) if res.usage else None,
                    "output_tokens": getattr(res.usage, "output_tokens", None) if res.usage else None,
                    "total_tokens": getattr(res.usage, "total_tokens", None) if res.usage else None,
                    "response_id": res.response_id,
                },
            },
            error=None,
        )
    except (OpenAIConfigError, OpenAIRequestError, Exception) as exc:
        return WeeklyPriorityProposal(
            weekly_priority=fallback.weekly_priority,
            hypothesis=fallback.hypothesis,
            owner=fallback.owner,
            deadline=fallback.deadline,
            success_criteria=fallback.success_criteria,
            used_llm=False,
            llm_meta=None,
            error=str(exc)[:200],
        )
