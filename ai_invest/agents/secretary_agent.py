from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ai_invest.config.llm_router import LLMRoute
from ai_invest.llm.openai_http import OpenAIConfigError, OpenAIRequestError, OpenAITextResult, openai_generate_text
from ai_invest.agents.prompt_contract import secretary_minutes_system_prompt


def _parse_bool(value: str, *, default: bool = False) -> bool:
    v = str(value or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "y", "on"}


def _clip(s: str, n: int) -> str:
    s = str(s or "")
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(obj)


@dataclass(frozen=True)
class SecretaryMinutes:
    text: str
    used_llm: bool
    model: str | None
    endpoint: str | None
    usage: Mapping[str, Any] | None
    error: str | None


def _deterministic_meeting_minutes(
    *,
    session: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    max_messages: int = 16,
) -> str:
    meeting_type = str(session.get("meeting_type") or "")
    facilitator = str(session.get("facilitator") or "")
    agenda = session.get("agenda") if isinstance(session.get("agenda"), Mapping) else {}
    decisions = session.get("decisions") if isinstance(session.get("decisions"), Mapping) else {}
    action_items = session.get("action_items") if isinstance(session.get("action_items"), Mapping) else {}

    slot_key = ""
    if isinstance(agenda, Mapping) and isinstance(agenda.get("slot_key"), str):
        slot_key = str(agenda.get("slot_key") or "")

    plan = decisions.get("trade_plan") if isinstance(decisions.get("trade_plan"), Mapping) else None
    plan_symbol = str((plan or {}).get("symbol") or "")
    plan_target = (plan or {}).get("target_position_pct")

    lines: list[str] = []
    title = "일일 회의록" if "DAILY" in meeting_type else "회의록"
    lines.append(f"{title} (v1, deterministic)")
    if slot_key:
        lines.append(f"- 슬롯: {slot_key}")
    if meeting_type:
        lines.append(f"- 종류: {meeting_type}")
    if facilitator:
        lines.append(f"- 진행: {facilitator}")

    if plan_symbol:
        try:
            tgt = float(plan_target) if plan_target is not None else None
        except Exception:
            tgt = None
        if tgt is None:
            lines.append(f"- 결론: {plan_symbol} (비중 미기재)")
        else:
            lines.append(f"- 결론: {plan_symbol} 목표비중 {tgt:.1f}%")

    # Discussion highlights (agent-by-agent)
    lines.append("")
    lines.append("핵심 발언(요약)")
    for m in list(messages)[:max_messages]:
        sender = str(m.get("sender_agent") or "")
        mtype = str(m.get("message_type") or "")
        content = _clip(str(m.get("content") or ""), 220)
        if not (sender or content):
            continue
        tag = f"{sender}/{mtype}".strip("/")
        lines.append(f"- {tag}: {content}")

    # Action items (structured)
    items = action_items.get("items") if isinstance(action_items, Mapping) else None
    if isinstance(items, list) and items:
        lines.append("")
        lines.append("액션 아이템")
        for it in items[:6]:
            if not isinstance(it, Mapping):
                continue
            owner = str(it.get("owner") or "")
            action = _clip(str(it.get("action") or ""), 160)
            due = str(it.get("due_date") or "")
            lines.append(f"- {owner}: {action} (기한 {due})")

    lines.append("")
    lines.append("메모")
    lines.append("- LLM 비서 요약이 비활성/실패 시, 이 템플릿이 대신 전송됩니다.")
    return "\n".join(lines).strip()


def generate_meeting_minutes(
    *,
    session: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    llm_route: LLMRoute | None = None,
    max_output_chars: int = 3200,
) -> SecretaryMinutes:
    """Secretary Agent: meeting minutes for Telegram/UI.

    - Always returns something (deterministic fallback).
    - LLM is optional and safe to fail (no execution dependence).
    """

    if llm_route is not None:
        use_llm = bool(llm_route.enabled) and bool(os.environ.get("OPENAI_API_KEY", "").strip())
    else:
        llm_enabled = _parse_bool(os.environ.get("SECRETARY_LLM_ENABLED", ""), default=True)
        use_llm = llm_enabled and bool(os.environ.get("OPENAI_API_KEY", "").strip())

    # Always build a deterministic fallback first.
    fallback = _deterministic_meeting_minutes(session=session, messages=messages)

    if not use_llm:
        return SecretaryMinutes(
            text=_clip(fallback, max_output_chars),
            used_llm=False,
            model=None,
            endpoint=None,
            usage=None,
            error=None,
        )

    # Prepare compact context for the model.
    meeting_ctx: dict[str, Any] = {
        "meeting": {
            "meeting_id": session.get("meeting_id"),
            "meeting_type": session.get("meeting_type"),
            "status": session.get("status"),
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at"),
            "facilitator": session.get("facilitator"),
            "participants": session.get("participants"),
            "agenda": session.get("agenda"),
            "decisions": session.get("decisions"),
            "action_items": session.get("action_items"),
            "summary_short": session.get("summary"),
        },
        "messages": [
            {
                "ts": m.get("ts"),
                "sender_agent": m.get("sender_agent"),
                "message_type": m.get("message_type"),
                "confidence": m.get("confidence"),
                "content": _clip(str(m.get("content") or ""), 380),
            }
            for m in list(messages)[:80]
            if isinstance(m, Mapping)
        ],
        "fallback_minutes": _clip(fallback, 1200),
    }

    system_prompt = secretary_minutes_system_prompt()

    user_prompt = (
        "아래 JSON은 1회 회의 세션과 메시지 트랜스크립트다.\n"
        "다음 형식으로 회의록을 작성해라:\n"
        "1) 결론(Trade Plan/중요 결정)\n"
        "2) 왜 그렇게 결론났는지(에이전트별 핵심 근거 1~2개)\n"
        "3) 정리된 규칙/제약(비중 상한, 스프레드/레짐/리스크 게이트 등)\n"
        "4) 리스크/관찰 포인트\n"
        "5) 액션 아이템\n"
        "\n"
        f"JSON:\n{_safe_json(meeting_ctx)}\n"
    )

    try:
        res: OpenAITextResult = openai_generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=(llm_route.model if llm_route else None),
            api_style=(llm_route.api_style if llm_route else None),
            reasoning_effort=(llm_route.reasoning_effort if llm_route else None),
            temperature=(float(llm_route.temperature) if (llm_route and llm_route.temperature is not None) else 0.2),
            timeout_sec=(int(llm_route.timeout_sec) if (llm_route and llm_route.timeout_sec) else 40),
        )
        text = res.text.strip()
        if not text:
            raise OpenAIRequestError("empty secretary output")
        return SecretaryMinutes(
            text=_clip(text, max_output_chars),
            used_llm=True,
            model=res.model,
            endpoint=res.endpoint,
            usage={
                "input_tokens": getattr(res.usage, "input_tokens", None) if res.usage else None,
                "output_tokens": getattr(res.usage, "output_tokens", None) if res.usage else None,
                "total_tokens": getattr(res.usage, "total_tokens", None) if res.usage else None,
                "response_id": res.response_id,
            },
            error=None,
        )
    except (OpenAIConfigError, OpenAIRequestError, Exception) as exc:
        # Safe fallback: never block governance loop.
        return SecretaryMinutes(
            text=_clip(fallback, max_output_chars),
            used_llm=False,
            model=None,
            endpoint=None,
            usage=None,
            error=str(exc)[:200],
        )
