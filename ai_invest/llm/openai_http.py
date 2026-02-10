from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

import requests


class OpenAIConfigError(RuntimeError):
    pass


class OpenAIRequestError(RuntimeError):
    def __init__(self, msg: str, *, status_code: int | None = None, body: str | None = None) -> None:
        super().__init__(msg)
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class OpenAIUsage:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True)
class OpenAITextResult:
    text: str
    model: str
    endpoint: str  # responses / chat.completions
    usage: OpenAIUsage | None
    response_id: str | None


def _get_env(name: str, *, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _base_url() -> str:
    # Keep user override compatible with common forms:
    # - https://api.openai.com/v1
    # - https://proxy.example.com/v1
    # - https://proxy.example.com
    base = _get_env("OPENAI_BASE_URL", default="https://api.openai.com/v1").rstrip("/")
    return base


def _headers() -> dict[str, str]:
    api_key = _get_env("OPENAI_API_KEY")
    if not api_key:
        raise OpenAIConfigError("OPENAI_API_KEY is missing")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    org = _get_env("OPENAI_ORG_ID")
    if org:
        headers["OpenAI-Organization"] = org
    return headers


def _join_url(base: str, path: str) -> str:
    base = base.rstrip("/")
    if path.startswith("/"):
        return base + path
    return base + "/" + path


def _parse_usage(obj: Any) -> OpenAIUsage | None:
    if not isinstance(obj, Mapping):
        return None
    # Responses API style
    usage = obj.get("usage")
    if isinstance(usage, Mapping):
        it = usage.get("input_tokens")
        ot = usage.get("output_tokens")
        tt = usage.get("total_tokens")
        return OpenAIUsage(
            input_tokens=int(it) if isinstance(it, (int, float)) else None,
            output_tokens=int(ot) if isinstance(ot, (int, float)) else None,
            total_tokens=int(tt) if isinstance(tt, (int, float)) else None,
        )
    return None


def _extract_responses_text(data: Mapping[str, Any]) -> str:
    # Expected shape (typical):
    # {"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"..."}]}]}
    out = data.get("output")
    if not isinstance(out, list):
        return ""
    parts: list[str] = []
    for item in out:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, Mapping):
                continue
            t = c.get("type")
            if t in {"output_text", "text"} and isinstance(c.get("text"), str):
                parts.append(str(c.get("text")))
            elif isinstance(c.get("content"), str):
                parts.append(str(c.get("content")))
    return "\n".join([p for p in parts if p.strip()]).strip()


def _extract_chat_text(data: Mapping[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    c0 = choices[0]
    if not isinstance(c0, Mapping):
        return ""
    msg = c0.get("message")
    if isinstance(msg, Mapping) and isinstance(msg.get("content"), str):
        return str(msg.get("content")).strip()
    # Some proxies return {"choices":[{"text":"..."}]}
    if isinstance(c0.get("text"), str):
        return str(c0.get("text")).strip()
    return ""


def openai_generate_text(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float = 0.2,
    api_style: str | None = None,
    reasoning_effort: str | None = None,
    max_output_tokens: int | None = None,
    timeout_sec: int = 40,
) -> OpenAITextResult:
    """Generate text via OpenAI HTTP.

    Strategy:
    - default: try Responses API first, then fall back to Chat Completions.
    - override by setting OPENAI_API_STYLE=responses|chat|auto (default auto)

    Important:
    - This must never be used for trade execution decisions in v1 (summaries only).
    """

    style = (api_style or _get_env("OPENAI_API_STYLE", default="auto")).lower().strip() or "auto"
    if style not in {"auto", "responses", "chat"}:
        style = "auto"
    base = _base_url()
    headers = _headers()

    chosen_model = (model or _get_env("OPENAI_LLM_MODEL") or "gpt-5").strip()
    if not chosen_model:
        raise OpenAIConfigError("OPENAI_LLM_MODEL is missing")

    def _default_reasoning_effort_for(model_name: str) -> str | None:
        m = str(model_name or "").strip().lower()
        if m.startswith("gpt-5.2-pro"):
            return "medium"
        if m.startswith("gpt-5"):
            return "low"
        return None

    # Reasoning models (e.g., gpt-5) may spend large hidden reasoning tokens by default.
    # Keep the default conservative for summaries/briefs; allow caller override per-agent.
    reasoning_effort = (reasoning_effort or _get_env("OPENAI_REASONING_EFFORT", default="")).lower().strip()
    if not reasoning_effort:
        de = _default_reasoning_effort_for(chosen_model)
        reasoning_effort = str(de or "").strip()

    last_err: OpenAIRequestError | None = None

    if style in {"auto", "responses"}:
        url = _join_url(base, "responses" if base.endswith("/v1") else "v1/responses")
        payload: dict[str, Any] = {
            "model": chosen_model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if max_output_tokens is not None and int(max_output_tokens) > 0:
            payload["max_output_tokens"] = int(max_output_tokens)
        if reasoning_effort in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            payload["reasoning"] = {"effort": reasoning_effort}
        # Some reasoning models reject temperature; we'll retry without if needed.
        if temperature is not None:
            payload["temperature"] = float(temperature)
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout_sec)
            if not resp.ok and resp.status_code == 400:
                # Retry by stripping commonly unsupported params (temperature etc.).
                try:
                    err = resp.json().get("error")  # type: ignore[union-attr]
                except Exception:
                    err = None
                param = (err or {}).get("param") if isinstance(err, Mapping) else None
                msg = (err or {}).get("message") if isinstance(err, Mapping) else None
                if (param == "temperature" or (isinstance(msg, str) and "temperature" in msg)) and "temperature" in payload:
                    payload2 = dict(payload)
                    payload2.pop("temperature", None)
                    resp = requests.post(url, headers=headers, json=payload2, timeout=timeout_sec)
                elif (param == "reasoning" or (isinstance(msg, str) and "reasoning" in msg)) and "reasoning" in payload:
                    payload2 = dict(payload)
                    payload2.pop("reasoning", None)
                    resp = requests.post(url, headers=headers, json=payload2, timeout=timeout_sec)

            if resp.ok:
                data = resp.json()
                if isinstance(data, Mapping):
                    text = _extract_responses_text(data)
                    if text:
                        usage = _parse_usage(data)
                        return OpenAITextResult(
                            text=text,
                            model=str(data.get("model") or chosen_model),
                            endpoint="responses",
                            usage=usage,
                            response_id=str(data.get("id")) if data.get("id") else None,
                        )
                last_err = OpenAIRequestError(
                    "OpenAI responses: empty output (model may have returned reasoning-only or incomplete output)",
                    status_code=resp.status_code,
                    body=resp.text[:500],
                )
            else:
                last_err = OpenAIRequestError(
                    "OpenAI responses request failed",
                    status_code=resp.status_code,
                    body=resp.text[:500],
                )
        except OpenAIConfigError:
            raise
        except Exception as exc:
            last_err = OpenAIRequestError(f"OpenAI responses exception: {exc}")

        if style == "responses":
            raise last_err or OpenAIRequestError("OpenAI responses failed (unknown)")

    if style in {"auto", "chat"}:
        url = _join_url(base, "chat/completions" if base.endswith("/v1") else "v1/chat/completions")
        payload: dict[str, Any] = {
            "model": chosen_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # Prefer max_completion_tokens; retry with max_tokens if a model rejects it.
        }
        if max_output_tokens is not None and int(max_output_tokens) > 0:
            payload["max_completion_tokens"] = int(max_output_tokens)
        if temperature is not None:
            payload["temperature"] = float(temperature)
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout_sec)
            if not resp.ok and resp.status_code == 400:
                # Retry removing temperature or swapping token parameter.
                try:
                    err = resp.json().get("error")  # type: ignore[union-attr]
                except Exception:
                    err = None
                param = (err or {}).get("param") if isinstance(err, Mapping) else None
                msg = (err or {}).get("message") if isinstance(err, Mapping) else None
                if (param == "temperature" or (isinstance(msg, str) and "temperature" in msg)) and "temperature" in payload:
                    payload2 = dict(payload)
                    payload2.pop("temperature", None)
                    resp = requests.post(url, headers=headers, json=payload2, timeout=timeout_sec)
                elif (param in {"max_tokens", "max_completion_tokens"} or (isinstance(msg, str) and "max_" in msg)):
                    # Swap parameter name.
                    payload2 = dict(payload)
                    if "max_completion_tokens" in payload2:
                        payload2["max_tokens"] = int(payload2.pop("max_completion_tokens") or int(max_output_tokens))
                    else:
                        payload2["max_completion_tokens"] = int(payload2.pop("max_tokens") or int(max_output_tokens))
                    resp = requests.post(url, headers=headers, json=payload2, timeout=timeout_sec)

            if resp.ok:
                data = resp.json()
                if isinstance(data, Mapping):
                    text = _extract_chat_text(data)
                    if text:
                        usage = _parse_usage(data)
                        # Chat-completions usage might be {"prompt_tokens":..., "completion_tokens":..., "total_tokens":...}
                        if usage is None and isinstance(data.get("usage"), Mapping):
                            u = data["usage"]
                            usage = OpenAIUsage(
                                input_tokens=int(u.get("prompt_tokens")) if isinstance(u.get("prompt_tokens"), (int, float)) else None,
                                output_tokens=int(u.get("completion_tokens")) if isinstance(u.get("completion_tokens"), (int, float)) else None,
                                total_tokens=int(u.get("total_tokens")) if isinstance(u.get("total_tokens"), (int, float)) else None,
                            )
                        return OpenAITextResult(
                            text=text,
                            model=str(data.get("model") or chosen_model),
                            endpoint="chat.completions",
                            usage=usage,
                            response_id=str(data.get("id")) if data.get("id") else None,
                        )
                raise OpenAIRequestError("OpenAI chat.completions: empty output", status_code=resp.status_code, body=resp.text[:500])
            raise OpenAIRequestError("OpenAI chat.completions request failed", status_code=resp.status_code, body=resp.text[:500])
        except OpenAIConfigError:
            raise
        except OpenAIRequestError:
            raise
        except Exception as exc:
            raise OpenAIRequestError(f"OpenAI chat.completions exception: {exc}") from exc

    raise last_err or OpenAIRequestError(f"Unsupported OPENAI_API_STYLE={style}")
