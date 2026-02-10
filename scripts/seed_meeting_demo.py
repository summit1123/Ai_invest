#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.agents.secretary_agent import generate_meeting_minutes  # noqa: E402
from ai_invest.notifications.service import NotificationService  # noqa: E402
from ai_invest.storage.postgres import (  # noqa: E402
    DbEvent,
    DbMeetingMessage,
    DbMeetingSession,
    PostgresRepo,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_str(x: Any) -> str:
    try:
        return str(x)
    except Exception:
        return "N/A"


def main() -> int:
    p = argparse.ArgumentParser(description="Seed a demo meeting session + messages (and send Telegram notifications).")
    p.add_argument("--meeting-type", type=str, default="DAILY_RESEARCH")
    p.add_argument("--facilitator", type=str, default="strategy_coordinator")
    p.add_argument("--dry-run", action="store_true", help="Store to DB but do not attempt Telegram send.")
    args = p.parse_args()

    load_dotenv()
    repo = PostgresRepo()

    if args.dry_run:
        from ai_invest.notifications.service import NotificationContext  # noqa: E402

        notifier = NotificationService(repo, ctx=NotificationContext(send_telegram=False, notify_safe_hold=False, dedupe_within_sec=0))
    else:
        notifier = NotificationService(repo)

    meeting_id = uuid.uuid4()
    started_at = _utcnow() - timedelta(minutes=7)
    ended_at = _utcnow()

    latest_report = (repo.fetch_agent_daily_reports(limit=1) or [None])[0]
    latest_safe = repo.fetch_latest_decision(judge_type="SAFE")
    symbol = (latest_safe or {}).get("symbol") or (latest_report or {}).get("findings", {}).get("symbol") or "KRW-BTC"

    agent_inputs: dict[str, Any] | None = None
    if latest_safe:
        ev = repo.fetch_event_by_entity(event_type="SAFE_DECISION", entity_type="decisions", entity_id=latest_safe["decision_id"])
        agent_inputs = (ev or {}).get("payload", {}).get("agent_inputs") if ev else None

    participants = [
        "research_agent",
        "market_agent",
        "regime_agent",
        "risk_agent",
        "ops_agent",
        "strategy_coordinator",
    ]

    agenda = {
        "symbol": symbol,
        "inputs": {
            "latest_safe_decision_id": (latest_safe or {}).get("decision_id"),
            "latest_research_report_id": (latest_report or {}).get("report_id"),
        },
    }

    summary = (
        f"{symbol} 기준 일일 점검: "
        f"Safe={_safe_str((latest_safe or {}).get('action'))}, "
        f"리서치={'있음' if latest_report else '없음'}, "
        f"에이전트입력={'있음' if agent_inputs else '없음'}"
    )

    action_items = [
        {"owner": "ops_agent", "action": "정합성 체크(WARN/FAIL) 발생 시 원인 분류 카드 생성", "due_date": str(ended_at.date())},
        {"owner": "research_agent", "action": "스프레드/변동성 급등 시 watchlist 강화", "due_date": str(ended_at.date())},
        {"owner": "strategy_coordinator", "action": "주간 우선순위 1건 업데이트 및 성공 기준 명확화", "due_date": str(ended_at.date())},
    ]

    draft_messages: list[dict[str, Any]] = []
    if latest_report:
        draft_messages.append(
            {
                "sender_agent": "research_agent",
                "message_type": "EVIDENCE",
                "content": _safe_str(latest_report.get("summary")),
                "payload": {"report_id": latest_report.get("report_id"), "risks": latest_report.get("risks")},
                "confidence": 0.75,
            }
        )
    if agent_inputs:
        market = agent_inputs.get("market") or {}
        regime = agent_inputs.get("regime") or {}
        risk = agent_inputs.get("risk") or {}
        ops = agent_inputs.get("ops") or {}
        draft_messages.extend(
            [
                {
                    "sender_agent": "market_agent",
                    "message_type": "CLAIM",
                    "content": f"시장신호={market.get('signal')} conf={market.get('confidence')} target%={market.get('target_position_pct')}",
                    "payload": {"reason_codes": market.get("reason_codes"), "reason": market.get("reason")},
                    "confidence": float(market.get("confidence") or 0.6),
                },
                {
                    "sender_agent": "regime_agent",
                    "message_type": "CLAIM",
                    "content": f"레짐={regime.get('regime')} trade_allowed={regime.get('trade_allowed')}",
                    "payload": {"reason_codes": regime.get("reason_codes"), "reason": regime.get("reason")},
                    "confidence": 0.7,
                },
                {
                    "sender_agent": "risk_agent",
                    "message_type": "CLAIM",
                    "content": f"veto={risk.get('veto')} max_pos%={risk.get('max_position_pct')} max_loss%={risk.get('max_loss_per_trade_pct')}",
                    "payload": {"reason_codes": risk.get("reason_codes"), "reason": risk.get("reason")},
                    "confidence": 0.7,
                },
                {
                    "sender_agent": "ops_agent",
                    "message_type": "CLAIM",
                    "content": f"state={ops.get('system_state')} veto={ops.get('veto')} recon={ops.get('reconciliation_status')}",
                    "payload": {"reason_codes": ops.get("reason_codes"), "alerts": ops.get("alerts")},
                    "confidence": 0.7,
                },
            ]
        )
    draft_messages.append(
        {
            "sender_agent": "strategy_coordinator",
            "message_type": "PROPOSAL",
            "content": "오늘은 paper loop 기준으로 운영/비용/레짐 게이트가 정상인지 확인하고, 다음 주 개선 우선순위를 1건만 고정합니다.",
            "payload": {"action_items": action_items},
            "confidence": 0.65,
        }
    )

    session_map: dict[str, Any] = {
        "meeting_id": str(meeting_id),
        "meeting_type": str(args.meeting_type).upper(),
        "status": "CLOSED",
        "started_at": started_at,
        "ended_at": ended_at,
        "facilitator": str(args.facilitator),
        "participants": participants,
        "agenda": agenda,
        "summary": summary,
        "decisions": {"paper": True},
        "action_items": {"items": action_items},
    }
    assistant = generate_meeting_minutes(session=session_map, messages=draft_messages)
    assistant_minutes = assistant.text

    repo.insert_meeting_session(
        DbMeetingSession(
            meeting_id=meeting_id,
            meeting_type=str(args.meeting_type).upper(),
            status="CLOSED",
            started_at=started_at,
            ended_at=ended_at,
            facilitator=str(args.facilitator),
            participants=participants,
            agenda=agenda,
            summary=assistant_minutes,
            decisions={"paper": True},
            action_items={"items": action_items},
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
            payload={"meeting_id": str(meeting_id), "meeting_type": str(args.meeting_type).upper(), "symbol": symbol},
        )
    )

    for dm in draft_messages:
        sender = str(dm.get("sender_agent") or "")
        msg_type = str(dm.get("message_type") or "")
        content = str(dm.get("content") or "")
        payload = dm.get("payload")
        conf = dm.get("confidence")
        msg_id = uuid.uuid4()
        ts = _utcnow()
        repo.insert_meeting_message(
            DbMeetingMessage(
                message_id=msg_id,
                meeting_id=meeting_id,
                ts=ts,
                sender_agent=sender,
                message_type=msg_type,
                content=content,
                payload=payload,
                confidence=conf,
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
                    "sender_agent": sender,
                    "message_type": msg_type,
                    "content": content,
                },
            )
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
                "summary_short": summary,
                "assistant_minutes": assistant_minutes,
                "assistant_meta": {
                    "used_llm": assistant.used_llm,
                    "model": assistant.model,
                    "endpoint": assistant.endpoint,
                    "usage": assistant.usage,
                    "error": assistant.error,
                },
                "symbol": symbol,
            },
        )
    )
    try:
        notifier.notify_meeting_summary(
            event_id=summary_event_id,
            meeting_id=str(meeting_id),
            summary=summary,
            assistant_minutes=assistant_minutes,
            assistant_meta={
                "used_llm": assistant.used_llm,
                "model": assistant.model,
                "endpoint": assistant.endpoint,
                "usage": assistant.usage,
                "error": assistant.error,
            },
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
            payload={"meeting_id": str(meeting_id), "items": action_items, "symbol": symbol},
        )
    )
    try:
        notifier.notify_meeting_action_items(event_id=action_event_id, meeting_id=str(meeting_id), items=action_items)
    except Exception:
        pass

    print(f"[완료] meeting seeded: meeting_id={meeting_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
