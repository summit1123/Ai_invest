from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from ai_invest.config.llm_router import llm_route_for_agent
from ai_invest.llm.openai_http import OpenAIConfigError, OpenAIRequestError, openai_generate_text
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
CHAT_LLM_TIMEOUT_CAP_SEC = 20


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
    if not raw.startswith("/"):
        return ParsedTelegramCommand(name="chat", args=(raw,))
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
    stop_request = orchestrator.get("last_stop_request") if isinstance(orchestrator.get("last_stop_request"), dict) else {}
    lines = [
        "대표님, 현재 운용 상태 보고드립니다.",
        f"- 운용 상태: {'일시 중지' if pause_state.get('paused') else '정상 가동'}",
        f"- 정합성 점검: {recon.get('status') or 'UNKNOWN'}",
        f"- 최신 안전판단: {((safe.get('action') or 'UNKNOWN').upper() + ' ' + (safe.get('symbol') or '')).strip()}",
        f"- 주요 사유: {', '.join(str(x) for x in reasons[:3]) if reasons else '특이사항 없음'}",
        f"- 정상 워커: {', '.join(orchestrator.get('alive_workers') or []) or '없음'}",
        f"- 중단 워커: {', '.join(orchestrator.get('dead_workers') or []) or '없음'}",
        f"- 추정 총자산: {_fmt_money(portfolio.get('equity_krw'))}",
        f"- 현금 잔고: {_fmt_money(portfolio.get('cash_krw'))}",
        f"- 금일 실현손익: {_fmt_money(pnl.get('realized_pnl') if isinstance(pnl.get('realized_pnl'), (int, float)) else pnl.get('realized_pnl_krw'))}",
    ]
    if stop_request:
        lines.append(
            f"- 최근 종료 요청: {stop_request.get('source') or 'unknown'} / {stop_request.get('reason') or 'unspecified'}"
        )
    return "\n".join(lines)


def _format_briefing(status_snapshot: dict[str, Any], no_trade_snapshot: dict[str, Any]) -> str:
    orchestrator = status_snapshot.get("orchestrator") if isinstance(status_snapshot.get("orchestrator"), dict) else {}
    pause_state = status_snapshot.get("pause_state") if isinstance(status_snapshot.get("pause_state"), dict) else {}
    recon = status_snapshot.get("latest_reconciliation") if isinstance(status_snapshot.get("latest_reconciliation"), dict) else {}
    safe = status_snapshot.get("latest_safe_decision") if isinstance(status_snapshot.get("latest_safe_decision"), dict) else {}
    portfolio = status_snapshot.get("portfolio") if isinstance(status_snapshot.get("portfolio"), dict) else {}
    pnl = status_snapshot.get("pnl_today") if isinstance(status_snapshot.get("pnl_today"), dict) else {}
    action = str(safe.get("action") or "UNKNOWN").upper()
    reasons = list(no_trade_snapshot.get("selected_reasons") or safe.get("selected_reasons") or [])
    dead_workers = list(orchestrator.get("dead_workers") or [])
    stop_request = orchestrator.get("last_stop_request") if isinstance(orchestrator.get("last_stop_request"), dict) else {}

    if pause_state.get("paused"):
        headline = "대표님, 현재 시스템은 일시 중지 상태입니다."
    elif dead_workers:
        headline = "대표님, 현재 시스템은 가동 중이지만 일부 워커 점검이 필요합니다."
    elif action == "BUY":
        headline = "대표님, 현재 시스템은 정상 가동 중이며 매수 가능 신호를 감지한 상태입니다."
    elif action == "SELL":
        headline = "대표님, 현재 시스템은 정상 가동 중이며 청산 또는 비중 축소 신호를 우선 보고 있습니다."
    else:
        headline = "대표님, 현재 시스템은 정상 가동 중이며 신중 관망 기조입니다."

    detail_lines = [
        headline,
        f"- 정합성 점검 상태는 {recon.get('status') or 'UNKNOWN'}입니다.",
        f"- 최신 안전판단은 {action}이며, 주요 사유는 {', '.join(str(x) for x in reasons[:3]) if reasons else '특이사항 없음'}입니다.",
        f"- 추정 총자산은 {_fmt_money(portfolio.get('equity_krw'))}, 현금 잔고는 {_fmt_money(portfolio.get('cash_krw'))}입니다.",
        f"- 금일 실현손익은 {_fmt_money(pnl.get('realized_pnl') if isinstance(pnl.get('realized_pnl'), (int, float)) else pnl.get('realized_pnl_krw'))}입니다.",
    ]
    if dead_workers:
        detail_lines.append(f"- 현재 추가 확인이 필요한 워커는 {', '.join(dead_workers[:5])}입니다.")
    else:
        detail_lines.append("- 현재 주요 워커는 모두 정상 응답 중입니다.")
    if stop_request:
        detail_lines.append(
            f"- 최근 종료 요청은 {stop_request.get('source') or 'unknown'} / {stop_request.get('reason') or 'unspecified'} 입니다."
        )
    return "\n".join(detail_lines)


def _load_rules_raw() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    rules_path = root / "rules.yaml"
    if not rules_path.exists():
        return {}
    try:
        raw = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _build_llm_grounding_context(*, repo: PostgresRepo, status_path: Path) -> dict[str, Any]:
    return {
        "ops_status": build_ops_status_snapshot(repo=repo, status_path=status_path),
        "no_trade_btc": build_no_trade_snapshot(repo=repo, symbol="KRW-BTC", status_path=status_path),
        "pause_explanation": build_pause_explanation(repo=repo, status_path=status_path),
        "pnl_today": build_pnl_snapshot(repo=repo, trade_limit=5),
    }


def _resolve_chat_llm_timeout_sec(timeout_sec: int | None) -> int:
    if timeout_sec is None:
        return CHAT_LLM_TIMEOUT_CAP_SEC
    try:
        timeout = int(timeout_sec)
    except Exception:
        return CHAT_LLM_TIMEOUT_CAP_SEC
    if timeout <= 0:
        return CHAT_LLM_TIMEOUT_CAP_SEC
    return min(timeout, CHAT_LLM_TIMEOUT_CAP_SEC)


def _build_llm_ops_reply(*, repo: PostgresRepo, status_path: Path, user_text: str) -> str:
    rules_raw = _load_rules_raw()
    route = llm_route_for_agent(rules_raw=rules_raw, agent_name="telegram_ops_bot")
    if not route.enabled:
        return _format_briefing(
            build_ops_status_snapshot(repo=repo, status_path=status_path),
            build_no_trade_snapshot(repo=repo, symbol="KRW-BTC", status_path=status_path),
        )

    context = _build_llm_grounding_context(repo=repo, status_path=status_path)
    system_prompt = (
        "당신은 자동매매 운영 비서입니다. "
        "대표에게 한국어로 짧고 명확하게 보고하십시오. "
        "반드시 제공된 운영 데이터만 근거로 답하고, 근거가 없으면 확인되지 않았다고 말하십시오. "
        "추측하지 말고, 주문 실행 권한이 있는 것처럼 말하지 마십시오. "
        "답변은 4~7줄 내외로 정리하고, 필요 시 마지막 줄에 다음 확인 포인트 1개만 제안하십시오."
    )
    user_prompt = (
        f"대표 질문:\n{user_text}\n\n"
        "운영 데이터(JSON):\n"
        f"{json.dumps(context, ensure_ascii=False, default=str)}"
    )
    try:
        result = openai_generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=route.model,
            temperature=route.temperature if route.temperature is not None else 0.2,
            api_style=route.api_style,
            reasoning_effort=route.reasoning_effort,
            timeout_sec=_resolve_chat_llm_timeout_sec(route.timeout_sec),
            max_output_tokens=400,
        )
        text = str(result.text or "").strip()
        if text:
            return text
    except (OpenAIConfigError, OpenAIRequestError, Exception):
        pass
    return _format_briefing(
        build_ops_status_snapshot(repo=repo, status_path=status_path),
        build_no_trade_snapshot(repo=repo, symbol="KRW-BTC", status_path=status_path),
    )


def _format_pause(snapshot: dict[str, Any]) -> str:
    reasons = list(snapshot.get("reasons") or [])
    lines = [
        "대표님, 중단 상태 보고드립니다.",
        f"- 중단 여부: {'예' if snapshot.get('paused') else '아니오'}",
        f"- 요약: {snapshot.get('summary') or '확인 가능한 특이사항이 없습니다.'}",
    ]
    for reason in reasons[:5]:
        lines.append(f"- 사유: {reason}")
    return "\n".join(lines)


def _format_pnl(snapshot: dict[str, Any]) -> str:
    latest_day = snapshot.get("latest_day") if isinstance(snapshot.get("latest_day"), dict) else {}
    trades = list(snapshot.get("recent_trades") or [])
    lines = [
        "대표님, 금일 손익 보고드립니다.",
        f"- 기준일: {latest_day.get('day') or latest_day.get('day_kst') or 'n/a'}",
        f"- 실현손익: {_fmt_money(latest_day.get('realized_pnl') if 'realized_pnl' in latest_day else latest_day.get('realized_pnl_krw'))}",
        f"- 수수료: {_fmt_money(latest_day.get('fees_paid') if 'fees_paid' in latest_day else latest_day.get('fees_paid_krw'))}",
        f"- 거래 건수: {latest_day.get('trades_count') if latest_day.get('trades_count') is not None else len(trades)}",
    ]
    for trade in trades[:5]:
        lines.append(f"- 거래: {trade.get('symbol') or 'n/a'} / 손익 {_fmt_money(trade.get('realized_pnl'))}")
    return "\n".join(lines)


def _format_no_trade(snapshot: dict[str, Any]) -> str:
    gates = snapshot.get("gates") if isinstance(snapshot.get("gates"), dict) else {}
    lines = [
        f"대표님, {snapshot.get('symbol') or 'n/a'} 미체결 사유 보고드립니다.",
        f"- 차단 여부: {'예' if snapshot.get('blocked') else '아니오'}",
        f"- 요약: {snapshot.get('summary') or '확인 가능한 특이사항이 없습니다.'}",
        f"- 최신 액션: {(snapshot.get('latest_safe_decision') or {}).get('action') if isinstance(snapshot.get('latest_safe_decision'), dict) else 'n/a'}",
        f"- 주요 사유: {', '.join(str(x) for x in snapshot.get('selected_reasons') or []) or '특이사항 없음'}",
        f"- 매수 허용: {gates.get('runtime_buy_enabled') if 'runtime_buy_enabled' in gates else 'n/a'}",
        f"- 정합성 점검: {snapshot.get('reconciliation_status') or (snapshot.get('latest_reconciliation') or {}).get('status') if isinstance(snapshot.get('latest_reconciliation'), dict) else 'UNKNOWN'}",
        f"- 유효 목표 비중: {_fmt_number(gates.get('effective_target_pct'))}",
    ]
    return "\n".join(lines)


def _format_state(snapshot: dict[str, Any]) -> str:
    position = snapshot.get("position") if isinstance(snapshot.get("position"), dict) else {}
    pnl = snapshot.get("pnl_today") if isinstance(snapshot.get("pnl_today"), dict) else {}
    plan = snapshot.get("trade_plan") if isinstance(snapshot.get("trade_plan"), dict) else {}
    orchestrator = snapshot.get("orchestrator") if isinstance(snapshot.get("orchestrator"), dict) else {}
    stop_request = orchestrator.get("last_stop_request") if isinstance(orchestrator.get("last_stop_request"), dict) else {}
    lines = [
        f"대표님, {_fmt_ts(snapshot.get('ts_utc'))} 시점 상태 보고드립니다.",
        f"- 요약: {snapshot.get('summary') or '확인 가능한 특이사항이 없습니다.'}",
        f"- 중단 여부: {'예' if (snapshot.get('pause_state') or {}).get('paused') else '아니오'}",
        f"- 정합성 점검: {snapshot.get('reconciliation_status') or 'UNKNOWN'}",
        f"- 최신 액션: {(snapshot.get('latest_safe_decision') or {}).get('action') if isinstance(snapshot.get('latest_safe_decision'), dict) else 'UNKNOWN'}",
        f"- 주요 사유: {', '.join(str(x) for x in snapshot.get('selected_reasons') or []) or '특이사항 없음'}",
        f"- 정상 워커: {', '.join(orchestrator.get('alive_workers') or []) or '없음'}",
        f"- 중단 워커: {', '.join(orchestrator.get('dead_workers') or []) or '없음'}",
        f"- 추정 총자산: {_fmt_money((snapshot.get('portfolio') or {}).get('equity_krw') if isinstance(snapshot.get('portfolio'), dict) else None)}",
        f"- 현금 잔고: {_fmt_money((snapshot.get('portfolio') or {}).get('cash_krw') if isinstance(snapshot.get('portfolio'), dict) else None)}",
        f"- 보유 수량: {_fmt_number(position.get('qty'), digits=8)}",
        f"- 보유 평가액: {_fmt_money(position.get('value_krw'))}",
        f"- 금일 실현손익: {_fmt_money(pnl.get('realized_pnl_krw'))}",
        f"- 당시 플랜: {(plan.get('action') or 'n/a')} / {_fmt_number(plan.get('target_position_pct'))}%",
    ]
    if stop_request:
        lines.append(
            f"- 최근 종료 요청: {stop_request.get('source') or 'unknown'} / {stop_request.get('reason') or 'unspecified'}"
        )
    return "\n".join(lines)


def _format_compare(snapshot: dict[str, Any]) -> str:
    changes = list(snapshot.get("changes") or [])
    lines = [
        f"대표님, {snapshot.get('symbol') or 'n/a'} 상태 비교 보고드립니다.",
        f"- 기준 시작: {_fmt_ts(((snapshot.get('from') or {}).get('ts_utc')) if isinstance(snapshot.get('from'), dict) else None)}",
        f"- 기준 종료: {_fmt_ts(((snapshot.get('to') or {}).get('ts_utc')) if isinstance(snapshot.get('to'), dict) else None)}",
        f"- 요약: {snapshot.get('summary') or '확인 가능한 특이사항이 없습니다.'}",
    ]
    for change in changes[:10]:
        lines.append(f"- 변경: {change.get('field')} / {change.get('before')} -> {change.get('after')}")
    if len(changes) > 10:
        lines.append(f"- 추가 변경: {len(changes) - 10}건")
    return "\n".join(lines)


def help_text() -> str:
    return "\n".join(
        [
            "대표님, 사용 가능한 조회 명령 안내드립니다.",
            "- /status",
            "- 자연어 브리핑: 요즘 어때 / 분위기 어때 / 브리핑해줘",
            "- /why_paused",
            "- /pnl_today [limit]",
            "- /why_no_trade [symbol]",
            "- /state_at <ISO-8601 ts> [symbol]",
            "- /compare <from_ts> <to_ts> [symbol]",
            "- 자연어 질의도 기본 상태 보고로 처리합니다.",
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
    if cmd.name == "chat":
        user_text = cmd.args[0] if cmd.args else text
        return _build_llm_ops_reply(repo=repo, status_path=status_path, user_text=user_text)
    if cmd.name == "briefing":
        status_snapshot = build_ops_status_snapshot(repo=repo, status_path=status_path)
        no_trade_snapshot = build_no_trade_snapshot(repo=repo, symbol="KRW-BTC", status_path=status_path)
        return _format_briefing(status_snapshot, no_trade_snapshot)
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
