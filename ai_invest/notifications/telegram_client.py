from __future__ import annotations

import os
from dataclasses import dataclass

import requests


class TelegramConfigError(RuntimeError):
    pass


class TelegramSendError(RuntimeError):
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


def send_message(*, chat_id: str, text: str, timeout_sec: int = 10) -> TelegramSendResult:
    token = _get_env("TELEGRAM_BOT_TOKEN")
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
