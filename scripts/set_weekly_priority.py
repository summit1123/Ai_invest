#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.notifications.service import NotificationService  # noqa: E402
from ai_invest.storage.postgres import DbEvent, DbStrategyReview, PostgresRepo  # noqa: E402


KST = ZoneInfo("Asia/Seoul")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def week_window_kst(today_kst: date) -> tuple[date, date]:
    # Monday start, Sunday end.
    start = today_kst - timedelta(days=today_kst.weekday())
    end = start + timedelta(days=6)
    return start, end


def main() -> int:
    p = argparse.ArgumentParser(description="Insert weekly priority (Strategy Coordinator) into strategy_reviews.")
    p.add_argument("--week-start", type=str, default="", help="YYYY-MM-DD (KST). default: this week's Monday")
    p.add_argument("--title", type=str, required=True)
    p.add_argument("--hypothesis", type=str, required=True)
    p.add_argument("--owner", type=str, required=True)
    p.add_argument("--deadline", type=str, default="", help="YYYY-MM-DD (optional, stored in success_criteria)")
    p.add_argument("--success-criteria-json", type=str, default="{}", help='JSON string, e.g. {"metric":"...", "target":123}')
    args = p.parse_args()

    load_dotenv()
    repo = PostgresRepo()
    notifier = NotificationService(repo)

    now_kst = utcnow().astimezone(KST)
    if args.week_start.strip():
        ws = date.fromisoformat(args.week_start.strip())
        we = ws + timedelta(days=6)
    else:
        ws, we = week_window_kst(now_kst.date())

    try:
        sc = json.loads(args.success_criteria_json)
    except Exception:
        sc = {"raw": args.success_criteria_json}
    if args.deadline.strip():
        sc = dict(sc or {})
        sc["deadline"] = args.deadline.strip()

    review_id = uuid.uuid4()
    repo.insert_strategy_review(
        DbStrategyReview(
            review_id=review_id,
            week_start=ws,
            week_end=we,
            priority_title=args.title,
            hypothesis=args.hypothesis,
            owner=args.owner,
            success_criteria=sc,
            status="OPEN",
            evidence={},
            run_id=None,
        )
    )

    event_id = uuid.uuid4()
    repo.insert_event(
        DbEvent(
            event_id=event_id,
            ts=utcnow(),
            event_type="WEEKLY_PRIORITY_SET",
            entity_type="strategy_reviews",
            entity_id=str(review_id),
            run_id=None,
            rule_version_id=None,
            payload={
                "review_id": str(review_id),
                "week_start": ws.isoformat(),
                "week_end": we.isoformat(),
                "priority_title": args.title,
                "hypothesis": args.hypothesis,
                "owner": args.owner,
                "success_criteria": sc,
            },
        )
    )

    try:
        week_label = f"{ws.isoformat()}~{we.isoformat()}"
        notifier.notify_weekly_priority(
            event_id=event_id,
            week_label=week_label,
            priority_title=args.title,
            hypothesis=args.hypothesis,
            owner=args.owner,
        )
    except Exception:
        pass

    print(f"[완료] WEEKLY_PRIORITY_SET stored: review_id={review_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

