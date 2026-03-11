from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Sequence

import requests


class TelegramConfigError(RuntimeError):
    pass


class TelegramSendError(RuntimeError):
    pass


class TelegramPollError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramSendResult:
    message_id: int | None
    ok: bool
    error: str | None


def _get_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise TelegramConfigError(f"{name} is missing")
    return value


def get_bot_token(*, preferred_env: str | None = None) -> str:
    names: list[str] = []
    if preferred_env:
        names.append(str(preferred_env))
    if "TELEGRAM_BOT_TOKEN" not in names:
        names.append("TELEGRAM_BOT_TOKEN")
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise TelegramConfigError(f"{names[0]} is missing")


def send_message(*, chat_id: str, text: str, timeout_sec: int = 10, token: str | None = None) -> TelegramSendResult:
    token = str(token or "").strip() or get_bot_token()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=timeout_sec)
        data = resp.json()
        if resp.ok and data.get("ok"):
            message_id = data.get("result", {}).get("message_id")
            return TelegramSendResult(message_id=message_id, ok=True, error=None)
        return TelegramSendResult(message_id=None, ok=False, error=str(data)[:500])
    except Exception as exc:
        return TelegramSendResult(message_id=None, ok=False, error=str(exc)[:500])


def get_updates(
    *,
    offset: int | None = None,
    timeout_sec: int = 30,
    token: str | None = None,
    allowed_updates: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    bot_token = str(token or "").strip() or get_bot_token()
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params: dict[str, Any] = {"timeout": max(0, int(timeout_sec))}
    if offset is not None:
        params["offset"] = int(offset)
    if allowed_updates:
        params["allowed_updates"] = list(allowed_updates)
    try:
        resp = requests.get(url, params=params, timeout=max(5, int(timeout_sec) + 5))
        data = resp.json()
    except Exception as exc:  # pragma: no cover - network failure path
        raise TelegramPollError(str(exc)[:500]) from exc
    if not resp.ok or not data.get("ok"):
        raise TelegramPollError(str(data)[:500])
    result = data.get("result")
    return list(result) if isinstance(result, list) else []


def chat_id_ops() -> str:
    return _get_env("TELEGRAM_CHAT_ID_OPS")


def chat_id_trading() -> str:
    return _get_env("TELEGRAM_CHAT_ID_TRADING")


def chat_id_review() -> str:
    return _get_env("TELEGRAM_CHAT_ID_REVIEW")


def chat_id_research() -> str:
    return _get_env("TELEGRAM_CHAT_ID_RESEARCH")


def chat_id_meeting() -> str:
    return _get_env("TELEGRAM_CHAT_ID_MEETING")


def chat_id_engineering() -> str:
    return _get_env("TELEGRAM_CHAT_ID_ENGINEERING")
