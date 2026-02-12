#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.notifications.service import NotificationService  # noqa: E402
from ai_invest.storage.postgres import DbEvent, PostgresRepo  # noqa: E402


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def main() -> int:
    p = argparse.ArgumentParser(description="Send one-time engineering improvement announcement to Telegram.")
    p.add_argument("--change-id", type=str, default="", help="Unique change id for dedupe. default: auto uuid")
    p.add_argument(
        "--summary",
        type=str,
        action="append",
        default=[],
        help="Summary line (can be repeated up to 3 times)",
    )
    p.add_argument("--activation-mode", type=str, default="PAPER/HOLD", help="Activation mode summary")
    p.add_argument(
        "--rollback-hint",
        type=str,
        default="문제 발생 시 직전 커밋 revert 후 오케스트레이터 재시작",
        help="Rollback hint line",
    )
    args = p.parse_args()

    load_dotenv()
    repo = PostgresRepo()
    notifier = NotificationService(repo)

    change_id = args.change_id.strip() or str(uuid.uuid4())
    summary_lines = [str(x).strip() for x in list(args.summary or []) if str(x).strip()][:3]
    if not summary_lines:
        summary_lines = [
            "TradePlan V2 구조(LLM 정책/결정적 실행) 반영",
            "activation_gate decision(LIVE/PAPER/HOLD) 도입",
            "execution_plan 기반 목표비중 집행으로 호환 확장",
        ]

    event_id = uuid.uuid4()
    repo.insert_event(
        DbEvent(
            event_id=event_id,
            ts=utcnow(),
            event_type="ENGINEERING_CHANGE_ANNOUNCED",
            entity_type="engineering_changes",
            entity_id=str(change_id),
            run_id=None,
            rule_version_id=None,
            payload={
                "change_id": str(change_id),
                "summary_lines": summary_lines,
                "activation_mode": str(args.activation_mode or "PAPER/HOLD"),
                "rollback_hint": str(args.rollback_hint or ""),
            },
        )
    )

    notifier.notify_engineering_change_announced(
        event_id=event_id,
        change_id=str(change_id),
        summary_lines=summary_lines,
        activation_mode=str(args.activation_mode or "PAPER/HOLD"),
        rollback_hint=str(args.rollback_hint or ""),
    )
    print(f"[완료] ENGINEERING_CHANGE_ANNOUNCED: change_id={change_id}, event_id={event_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
