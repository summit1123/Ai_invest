#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.agents.finance_tax_agent import finance_tax_monthly_review  # noqa: E402
from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.config.llm_router import llm_route_for_agent  # noqa: E402
from ai_invest.notifications.service import NotificationService  # noqa: E402
from ai_invest.storage.postgres import DbEvent, PostgresRepo  # noqa: E402

KST = ZoneInfo("Asia/Seoul")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_prev_month_kst() -> tuple[int, int]:
    now = _utcnow().astimezone(KST)
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev = first - timedelta(days=1)
    return prev.year, prev.month


def month_period_kst(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=KST)
    end = next_month - timedelta(seconds=1)
    return start, end


def fetch_tax_run_for_period(*, repo: PostgresRepo, year: int, month: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    ps_kst, pe_kst = month_period_kst(year, month)
    ps_utc = ps_kst.astimezone(timezone.utc)
    pe_utc = pe_kst.astimezone(timezone.utc)
    with repo.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select export_id, status, generated_at, checksum_sha256, generated_by, manifest
            from tax_export_runs
            where period_start=%s and period_end=%s
            order by generated_at desc
            limit 1
            """,
            (ps_utc, pe_utc),
        )
        row = cur.fetchone()
    if not row:
        return None, None
    export_id, status, generated_at, checksum_sha256, generated_by, manifest = row
    run = {
        "export_id": str(export_id),
        "status": str(status),
        "generated_at": generated_at,
        "checksum_sha256": checksum_sha256,
        "generated_by": generated_by,
    }
    return run, (manifest if isinstance(manifest, dict) else {})


def main() -> int:
    p = argparse.ArgumentParser(description="Finance/Tax monthly review agent (LLM optional, month-end).")
    p.add_argument("--year", type=int, default=0, help="target year (KST), default previous month")
    p.add_argument("--month", type=int, default=0, help="target month 1~12 (KST), default previous month")
    p.add_argument("--notify", action="store_true", help="send Telegram review summary")
    args = p.parse_args()

    load_dotenv()
    rules_raw = yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8"))
    repo = PostgresRepo()
    notifier = NotificationService(repo)

    y, m = (int(args.year), int(args.month))
    if y <= 0 or m <= 0:
        y, m = _default_prev_month_kst()

    tax_run, manifest = fetch_tax_run_for_period(repo=repo, year=y, month=m)
    route = llm_route_for_agent(rules_raw=rules_raw, agent_name="finance_tax_agent")
    review = finance_tax_monthly_review(
        year=y,
        month=m,
        tax_export_run=tax_run,
        manifest=manifest,
        llm_route=route,
    )

    export_id = (tax_run or {}).get("export_id")
    entity_id = str(export_id or f"{y:04d}-{m:02d}")
    payload = {
        "period": f"{y:04d}-{m:02d}",
        "manifest_ref": export_id,
        "tax_export_status": review.tax_export_status,
        "validation_report": dict(review.validation_report),
        "discrepancy_alerts": list(review.discrepancy_alerts),
        "summary": review.summary,
        "agent_meta": {"used_llm": review.used_llm, "llm_meta": dict(review.llm_meta or {}), "route_model": route.model},
    }
    event_id = uuid.uuid4()
    repo.insert_event(
        DbEvent(
            event_id=event_id,
            ts=_utcnow(),
            event_type="FINANCE_MONTHLY_REVIEW_RECORDED",
            entity_type="tax_export_runs",
            entity_id=entity_id,
            run_id=None,
            rule_version_id=None,
            payload=payload,
        )
    )

    if args.notify:
        notifier.notify_finance_monthly_review(
            event_id=event_id,
            period_label=f"{y:04d}-{m:02d}",
            tax_export_status=review.tax_export_status,
            discrepancy_alerts=list(review.discrepancy_alerts),
            summary=review.summary,
            manifest_ref=str(export_id or "-"),
            llm_used=bool(review.used_llm),
            llm_model=str((review.llm_meta or {}).get("model") or route.model),
        )

    print(
        json.dumps(
            {
                "ok": True,
                "period": f"{y:04d}-{m:02d}",
                "manifest_ref": export_id,
                "tax_export_status": review.tax_export_status,
                "discrepancy_alerts": list(review.discrepancy_alerts),
                "used_llm": review.used_llm,
                "model": (review.llm_meta or {}).get("model") if review.llm_meta else None,
                "event_id": str(event_id),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
