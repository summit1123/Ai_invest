from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if not s:
        return default
    return s in {"1", "true", "yes", "y", "on"}


def _as_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        s = str(value).strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    try:
        s = str(value).strip()
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


@dataclass(frozen=True)
class LLMRoute:
    """Agent별 LLM 라우팅 설정.

    - rules.yaml을 통해 model/effort/endpoint 등을 교체할 수 있게 한다.
    - 실행(Safe Judge/Execution) 경로는 이 라우팅을 직접 의존하지 않는다(요약/리서치/거버넌스용).
    """

    enabled: bool
    model: str
    api_style: str  # auto | responses | chat
    reasoning_effort: str | None
    temperature: float | None
    timeout_sec: int | None


def llm_route_for_agent(*, rules_raw: Mapping[str, Any] | None, agent_name: str) -> LLMRoute:
    rules_raw = rules_raw or {}

    llm = rules_raw.get("llm") if isinstance(rules_raw, Mapping) else {}
    default_cfg = llm.get("default") if isinstance(llm, Mapping) else {}
    agents_cfg = llm.get("agents") if isinstance(llm, Mapping) else {}
    agent_cfg = agents_cfg.get(agent_name) if isinstance(agents_cfg, Mapping) else {}

    if not isinstance(default_cfg, Mapping):
        default_cfg = {}
    if not isinstance(agent_cfg, Mapping):
        agent_cfg = {}

    merged: dict[str, Any] = {**dict(default_cfg), **dict(agent_cfg)}

    enabled = _as_bool(merged.get("enabled"), default=True)

    model = _as_str(merged.get("model")) or os.environ.get("OPENAI_LLM_MODEL", "").strip() or "gpt-5"

    api_style = _as_str(merged.get("api_style")) or _as_str(merged.get("endpoint")) or os.environ.get("OPENAI_API_STYLE", "").strip() or "auto"
    api_style = api_style.strip().lower()
    if api_style not in {"auto", "responses", "chat"}:
        api_style = "auto"

    reasoning_effort = _as_str(merged.get("reasoning_effort")) or None
    temperature = _as_float(merged.get("temperature"))
    timeout_sec = _as_int(merged.get("timeout_sec"))

    return LLMRoute(
        enabled=enabled,
        model=model,
        api_style=api_style,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        timeout_sec=timeout_sec,
    )

