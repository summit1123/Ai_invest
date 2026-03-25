from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class DiscordConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscordSendResult:
    ok: bool
    error: str | None
    status_code: int | None = None


def get_webhook_url(*, preferred_env: str | None = None) -> str:
    names: list[str] = []
    if preferred_env:
        names.append(str(preferred_env))
    if "DISCORD_WEBHOOK_URL" not in names:
        names.append("DISCORD_WEBHOOK_URL")
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise DiscordConfigError(f"{names[0]} is missing")


def send_message(
    *,
    webhook_url: str,
    text: str,
    timeout_sec: int = 10,
    username: str | None = None,
) -> DiscordSendResult:
    payload: dict[str, object] = {"content": text}
    if str(username or "").strip():
        payload["username"] = str(username).strip()
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            return DiscordSendResult(ok=True, error=None, status_code=getattr(resp, "status", 200))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")[:500]
        return DiscordSendResult(ok=False, error=body or str(exc), status_code=exc.code)
    except URLError as exc:
        return DiscordSendResult(ok=False, error=str(exc.reason)[:500], status_code=None)
    except Exception as exc:
        return DiscordSendResult(ok=False, error=str(exc)[:500], status_code=None)
