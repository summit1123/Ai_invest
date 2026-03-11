from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ai_invest.notifications import telegram_client
from ai_invest.ops.read_api import (
    build_no_trade_snapshot,
    build_ops_status_snapshot,
    build_pause_explanation,
    build_pnl_snapshot,
    build_state_at,
    build_state_compare,
)
from ai_invest.storage.postgres import PostgresRepo


KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class TelegramOpsBotConfig:
    token: str
    allowed_chat_ids: frozenset[str]
    status_path: Path
    poll_timeout_sec: int
    send_timeout_sec: int


@dataclass(frozen=True)
class ParsedTelegramCommand:
    name: str
    args: tuple[str, ...]


def _parse_allowed_chat_ids(raw: str) -> frozenset[str]:
    parts = [part.strip() for part in str(raw or "").split(",")]
    return frozenset(part for part in parts if part)


def load_telegram_ops_bot_config(
    *,
    status_path: Path | None = None,
    poll_timeout_sec: int | None = None,
) -> TelegramOpsBotConfig:
    token = telegram_client.get_bot_token(preferred_env="TELEGRAM_OPS_BOT_TOKEN")
    allowed = _parse_allowed_chat_ids(os.environ.get("TELEGRAM_OPS_BOT_ALLOWED_CHAT_IDS", ""))
    if not allowed:
        fallback = str(os.environ.get("TELEGRAM_CHAT_ID_OPS", "")).strip()
        allowed = frozenset({fallback}) if fallback else frozenset()
    if not allowed:
        raise telegram_client.TelegramConfigError("TELEGRAM_OPS_BOT_ALLOWED_CHAT_IDS is missing")
    timeout_raw = poll_timeout_sec if poll_timeout_sec is not None else os.environ.get("TELEGRAM_OPS_BOT_POLL_TIMEOUT_SEC", "")
    try:
        timeout = int(timeout_raw) if str(timeout_raw).strip() else 30
    except Exception:
        timeout = 30
    return TelegramOpsBotConfig(
        token=token,
        allowed_chat_ids=allowed,
        status_path=status_path or Path(os.environ.get("ORCHESTRATOR_STATUS_PATH", "runtime/orchestrator_status.json")),
        poll_timeout_sec=max(5, timeout),
        send_timeout_sec=10,
    )


def parse_telegram_command(text: str) -> ParsedTelegramCommand:
    raw = str(text or "").strip()
    if not raw:
        return ParsedTelegramCommand(name="help", args=())
    parts = raw.split()
    head = parts[0]
    if head.startswith("/"):
        head = head[1:]
    head = head.split("@", 1)[0].strip().lower().replace("-", "_")
    if not head:
        head = "help"
    return ParsedTelegramCommand(name=head, args=tuple(parts[1:]))


def _parse_ts(raw: str) -> datetime:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("timestamp is required")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise ValueError(f"invalid timestamp: {exc}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_money(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.0f} KRW"
    except Exception:
        return str(value)


def _fmt_number(value: Any, *, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return str(value)


def _fmt_ts(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return "n/a"
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _format_status(snapshot: dict[str, Any]) -> str:
    orchestrator = snapshot.get("orchestrator") if isinstance(snapshot.get("orchestrator"), dict) else {}
    pause_state = snapshot.get("pause_state") if isinstance(snapshot.get("pause_state"), dict) else {}
    recon = snapshot.get("latest_reconciliation") if isinstance(snapshot.get("latest_reconciliation"), dict) else {}
    safe = snapshot.get("latest_safe_decision") if isinstance(snapshot.get("latest_safe_decision"), dict) else {}
    portfolio = snapshot.get("portfolio") if isinstance(snapshot.get("portfolio"), dict) else {}
    pnl = snapshot.get("pnl_today") if isinstance(snapshot.get("pnl_today"), dict) else {}
    reasons = list(safe.get("selected_reasons") or [])
    lines = [
        "ops status",
        f"- paused: {'yes' if pause_state.get('paused') else 'no'}",
        f"- recon: {recon.get('status') or 'UNKNOWN'}",
        f"- safe: {(safe.get('action') or 'UNKNOWN').upper()} {safe.get('symbol') or ''}".strip(),
        f"- reasons: {', '.join(str(x) for x in reasons[:3]) if reasons else 'none'}",
        f"- workers up: {', '.join(orchestrator.get('alive_workers') or []) or 'none'}",
        f"- workers down: {', '.join(orchestrator.get('dead_workers') or []) or 'none'}",
        f"- equity: {_fmt_money(portfolio.get('equity_krw'))}",
        f"- cash: {_fmt_money(portfolio.get('cash_krw'))}",
        f"- pnl today: {_fmt_money(pnl.get('realized_pnl') if isinstance(pnl.get('realized_pnl'), (int, float)) else pnl.get('realized_pnl_krw'))}",
    ]
    return "\n".join(lines)


def _format_pause(snapshot: dict[str, Any]) -> str:
    reasons = list(snapshot.get("reasons") or [])
    lines = [
        "pause status",
        f"- paused: {'yes' if snapshot.get('paused') else 'no'}",
        f"- summary: {snapshot.get('summary') or 'n/a'}",
    ]
    for reason in reasons[:5]:
        lines.append(f"- reason: {reason}")
    return "\n".join(lines)


def _format_pnl(snapshot: dict[str, Any]) -> str:
    latest_day = snapshot.get("latest_day") if isinstance(snapshot.get("latest_day"), dict) else {}
    trades = list(snapshot.get("recent_trades") or [])
    lines = [
        "pnl today",
        f"- day: {latest_day.get('day') or latest_day.get('day_kst') or 'n/a'}",
        f"- realized: {_fmt_money(latest_day.get('realized_pnl') if 'realized_pnl' in latest_day else latest_day.get('realized_pnl_krw'))}",
        f"- fees: {_fmt_money(latest_day.get('fees_paid') if 'fees_paid' in latest_day else latest_day.get('fees_paid_krw'))}",
        f"- trades: {latest_day.get('trades_count') if latest_day.get('trades_count') is not None else len(trades)}",
    ]
    for trade in trades[:5]:
        lines.append(
            f"- trade: {trade.get('symbol') or 'n/a'} pnl={_fmt_money(trade.get('realized_pnl'))}"
        )
    return "\n".join(lines)


def _format_no_trade(snapshot: dict[str, Any]) -> str:
    gates = snapshot.get("gates") if isinstance(snapshot.get("gates"), dict) else {}
    lines = [
        f"why no trade {snapshot.get('symbol') or 'n/a'}",
        f"- blocked: {'yes' if snapshot.get('blocked') else 'no'}",
        f"- summary: {snapshot.get('summary') or 'n/a'}",
        f"- action: {(snapshot.get('latest_safe_decision') or {}).get('action') if isinstance(snapshot.get('latest_safe_decision'), dict) else 'n/a'}",
        f"- reasons: {', '.join(str(x) for x in snapshot.get('selected_reasons') or []) or 'none'}",
        f"- buy enabled: {gates.get('runtime_buy_enabled') if 'runtime_buy_enabled' in gates else 'n/a'}",
        f"- recon: {snapshot.get('reconciliation_status') or (snapshot.get('latest_reconciliation') or {}).get('status') if isinstance(snapshot.get('latest_reconciliation'), dict) else 'UNKNOWN'}",
        f"- target pct: {_fmt_number(gates.get('effective_target_pct'))}",
    ]
    return "\n".join(lines)


def _format_state(snapshot: dict[str, Any]) -> str:
    position = snapshot.get("position") if isinstance(snapshot.get("position"), dict) else {}
    pnl = snapshot.get("pnl_today") if isinstance(snapshot.get("pnl_today"), dict) else {}
    plan = snapshot.get("trade_plan") if isinstance(snapshot.get("trade_plan"), dict) else {}
    orchestrator = snapshot.get("orchestrator") if isinstance(snapshot.get("orchestrator"), dict) else {}
    lines = [
        f"state at {_fmt_ts(snapshot.get('ts_utc'))}",
        f"- summary: {snapshot.get('summary') or 'n/a'}",
        f"- paused: {'yes' if (snapshot.get('pause_state') or {}).get('paused') else 'no'}",
        f"- recon: {snapshot.get('reconciliation_status') or 'UNKNOWN'}",
        f"- action: {(snapshot.get('latest_safe_decision') or {}).get('action') if isinstance(snapshot.get('latest_safe_decision'), dict) else 'UNKNOWN'}",
        f"- reasons: {', '.join(str(x) for x in snapshot.get('selected_reasons') or []) or 'none'}",
        f"- workers up: {', '.join(orchestrator.get('alive_workers') or []) or 'none'}",
        f"- workers down: {', '.join(orchestrator.get('dead_workers') or []) or 'none'}",
        f"- equity: {_fmt_money((snapshot.get('portfolio') or {}).get('equity_krw') if isinstance(snapshot.get('portfolio'), dict) else None)}",
        f"- cash: {_fmt_money((snapshot.get('portfolio') or {}).get('cash_krw') if isinstance(snapshot.get('portfolio'), dict) else None)}",
        f"- position qty: {_fmt_number(position.get('qty'), digits=8)}",
        f"- position value: {_fmt_money(position.get('value_krw'))}",
        f"- pnl today: {_fmt_money(pnl.get('realized_pnl_krw'))}",
        f"- plan: {(plan.get('action') or 'n/a')} / {_fmt_number(plan.get('target_position_pct'))}%",
    ]
    return "\n".join(lines)


def _format_compare(snapshot: dict[str, Any]) -> str:
    changes = list(snapshot.get("changes") or [])
    lines = [
        f"compare {snapshot.get('symbol') or 'n/a'}",
        f"- from: {_fmt_ts(((snapshot.get('from') or {}).get('ts_utc')) if isinstance(snapshot.get('from'), dict) else None)}",
        f"- to: {_fmt_ts(((snapshot.get('to') or {}).get('ts_utc')) if isinstance(snapshot.get('to'), dict) else None)}",
        f"- summary: {snapshot.get('summary') or 'n/a'}",
    ]
    for change in changes[:10]:
        lines.append(f"- {change.get('field')}: {change.get('before')} -> {change.get('after')}")
    if len(changes) > 10:
        lines.append(f"- more: {len(changes) - 10} additional changes")
    return "\n".join(lines)


def help_text() -> str:
    return "\n".join(
        [
            "ops bot commands",
            "- /status",
            "- /why_paused",
            "- /pnl_today [limit]",
            "- /why_no_trade [symbol]",
            "- /state_at <ISO-8601 ts> [symbol]",
            "- /compare <from_ts> <to_ts> [symbol]",
        ]
    )


def build_ops_bot_reply(
    *,
    repo: PostgresRepo,
    status_path: Path,
    text: str,
) -> str:
    cmd = parse_telegram_command(text)
    if cmd.name in {"help", "start"}:
        return help_text()
    if cmd.name == "status":
        return _format_status(build_ops_status_snapshot(repo=repo, status_path=status_path))
    if cmd.name == "why_paused":
        return _format_pause(build_pause_explanation(repo=repo, status_path=status_path))
    if cmd.name == "pnl_today":
        limit = 10
        if cmd.args:
            try:
                limit = max(1, min(20, int(cmd.args[0])))
            except Exception as exc:
                raise ValueError(f"invalid trade limit: {exc}") from exc
        return _format_pnl(build_pnl_snapshot(repo=repo, trade_limit=limit))
    if cmd.name == "why_no_trade":
        symbol = cmd.args[0] if cmd.args else "KRW-BTC"
        return _format_no_trade(build_no_trade_snapshot(repo=repo, symbol=symbol, status_path=status_path))
    if cmd.name == "state_at":
        if not cmd.args:
            raise ValueError("usage: /state_at <ISO-8601 ts> [symbol]")
        ts_at = _parse_ts(cmd.args[0])
        symbol = cmd.args[1] if len(cmd.args) > 1 else "KRW-BTC"
        return _format_state(build_state_at(repo=repo, ts_at=ts_at, symbol=symbol))
    if cmd.name == "compare":
        if len(cmd.args) < 2:
            raise ValueError("usage: /compare <from_ts> <to_ts> [symbol]")
        from_ts = _parse_ts(cmd.args[0])
        to_ts = _parse_ts(cmd.args[1])
        if from_ts > to_ts:
            raise ValueError("from_ts must be earlier than or equal to to_ts")
        symbol = cmd.args[2] if len(cmd.args) > 2 else "KRW-BTC"
        return _format_compare(build_state_compare(repo=repo, from_ts=from_ts, to_ts=to_ts, symbol=symbol))
    raise ValueError(f"unknown command: {cmd.name}")


def run_telegram_ops_bot(
    *,
    config: TelegramOpsBotConfig,
    repo: PostgresRepo | None = None,
) -> None:
    repo = repo or PostgresRepo()
    offset: int | None = None
    print(
        f"[telegram-ops-bot] starting poll loop allowed_chats={len(config.allowed_chat_ids)} "
        f"status_path={config.status_path}",
        flush=True,
    )
    while True:
        try:
            updates = telegram_client.get_updates(
                offset=offset,
                timeout_sec=config.poll_timeout_sec,
                token=config.token,
                allowed_updates=["message"],
            )
        except telegram_client.TelegramPollError as exc:
            print(f"[telegram-ops-bot] poll error: {exc}", flush=True)
            time.sleep(3.0)
            continue

        for update in updates:
            update_id = int(update.get("update_id") or 0)
            offset = update_id + 1
            message = update.get("message") if isinstance(update.get("message"), dict) else {}
            chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
            chat_id = str(chat.get("id") or "").strip()
            if not chat_id or chat_id not in config.allowed_chat_ids:
                continue
            text = str(message.get("text") or "").strip()
            if not text:
                continue
            try:
                reply = build_ops_bot_reply(repo=repo, status_path=config.status_path, text=text)
            except Exception as exc:
                reply = f"request failed: {str(exc)[:300]}"
            result = telegram_client.send_message(
                chat_id=chat_id,
                text=reply,
                timeout_sec=config.send_timeout_sec,
                token=config.token,
            )
            if not result.ok:
                print(f"[telegram-ops-bot] send failed chat_id={chat_id}: {result.error}", flush=True)
