#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.agents.strategy_coordinator_agent import propose_weekly_priority  # noqa: E402
from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.config.llm_router import llm_route_for_agent  # noqa: E402
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
    p = argparse.ArgumentParser(description="Strategy Coordinator weekly priority (LLM optional).")
    p.add_argument("--week-start", type=str, default="", help="YYYY-MM-DD (KST). default: this week's Monday")
    p.add_argument("--owner", type=str, default="strategy_coordinator")
    args = p.parse_args()

    load_dotenv()
    rules_raw = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))

    repo = PostgresRepo()
    notifier = NotificationService(repo)

    now_kst = utcnow().astimezone(KST)
    if args.week_start.strip():
        ws = date.fromisoformat(args.week_start.strip())
        we = ws + timedelta(days=6)
    else:
        ws, we = week_window_kst(now_kst.date())

    pnl = repo.fetch_pnl_daily(limit=14)
    trades = repo.fetch_realized_trades(limit=200)
    execm = repo.fetch_execution_metrics(limit=200)
    recon = repo.fetch_reconciliation_checks(limit=200)

    route = llm_route_for_agent(rules_raw=rules_raw, agent_name="strategy_coordinator")
    proposal = propose_weekly_priority(
        today_kst=now_kst.date(),
        pnl_daily=pnl,
        realized_trades=trades,
        execution_metrics=execm,
        reconciliation_checks=recon,
        llm_route=route,
    )

    sc = dict(proposal.success_criteria or {})
    if proposal.deadline:
        sc["deadline"] = proposal.deadline
    if proposal.llm_meta:
        sc["_llm_meta"] = dict(proposal.llm_meta)
    if proposal.error:
        sc["_error"] = proposal.error

    review_id = uuid.uuid4()
    repo.insert_strategy_review(
        DbStrategyReview(
            review_id=review_id,
            week_start=ws,
            week_end=we,
            priority_title=proposal.weekly_priority,
            hypothesis=proposal.hypothesis,
            owner=args.owner.strip() or proposal.owner,
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
                "priority_title": proposal.weekly_priority,
                "hypothesis": proposal.hypothesis,
                "owner": args.owner.strip() or proposal.owner,
                "success_criteria": sc,
                "assistant_meta": {"used_llm": proposal.used_llm, **(proposal.llm_meta or {}), "error": proposal.error},
            },
        )
    )

    try:
        week_label = f"{ws.isoformat()}~{we.isoformat()}"
        notifier.notify_weekly_priority(
            event_id=event_id,
            week_label=week_label,
            priority_title=proposal.weekly_priority,
            hypothesis=proposal.hypothesis,
            owner=args.owner.strip() or proposal.owner,
        )
    except Exception:
        pass

    print(f"[완료] WEEKLY_PRIORITY_SET stored: review_id={review_id}")
    print(f"- used_llm={proposal.used_llm}, error={proposal.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

