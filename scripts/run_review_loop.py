#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.notifications.service import NotificationService  # noqa: E402
from ai_invest.storage.postgres import DbEvent, PostgresRepo  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
WEEK_DAYS = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_kst() -> datetime:
    return _utcnow().astimezone(KST)


def _parse_hhmm(value: str, *, default: str) -> tuple[int, int]:
    v = str(value or default).strip()
    try:
        hh, mm = v.split(":", 1)
        h = int(hh)
        m = int(mm)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(v)
        return h, m
    except Exception:
        hh, mm = default.split(":", 1)
        return int(hh), int(mm)


def _past_time(now_kst: datetime, hhmm: str, *, default: str) -> bool:
    hh, mm = _parse_hhmm(hhmm, default=default)
    trigger = now_kst.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return now_kst >= trigger


def _week_window(today: date) -> tuple[date, date]:
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    return start, end


def _latest_event_payload(repo: PostgresRepo, *, event_type: str) -> dict[str, Any]:
    ev = repo.fetch_latest_event(event_type=event_type)
    payload = (ev or {}).get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _parse_day(s: str) -> date | None:
    try:
        return date.fromisoformat(str(s).strip())
    except Exception:
        return None


def _parse_ts(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def should_send_daily_review(*, now_kst: datetime, daily_time_kst: str, latest_sent_day: str | None) -> bool:
    if not _past_time(now_kst, daily_time_kst, default="23:10"):
        return False
    today = now_kst.date().isoformat()
    return str(latest_sent_day or "") != today


def should_send_weekly_review(
    *,
    now_kst: datetime,
    weekly_day: str,
    weekly_time_kst: str,
    latest_sent_week_start: str | None,
) -> tuple[bool, str, str]:
    wd = WEEK_DAYS.get(str(weekly_day or "SUN").strip().upper(), 6)
    ws, we = _week_window(now_kst.date())
    week_start = ws.isoformat()
    week_end = we.isoformat()
    if now_kst.weekday() != wd:
        return False, week_start, week_end
    if not _past_time(now_kst, weekly_time_kst, default="21:00"):
        return False, week_start, week_end
    return str(latest_sent_week_start or "") != week_start, week_start, week_end


def _top3_loss_tags(repo: PostgresRepo, *, ws: date, we: date) -> str:
    rows = repo.fetch_decision_outcomes(limit=2000)
    counter: dict[str, int] = {}
    for r in rows:
        close_ts = _parse_ts(r.get("ts_close"))
        if close_ts is None:
            continue
        d = close_ts.astimezone(KST).date()
        if d < ws or d > we:
            continue
        tag = str(r.get("error_type") or "").strip()
        if not tag:
            continue
        counter[tag] = counter.get(tag, 0) + 1
    if not counter:
        return "(없음)"
    top = sorted(counter.items(), key=lambda x: x[1], reverse=True)[:3]
    return ", ".join(f"{k}:{v}" for k, v in top)


def _weekly_trade_metrics(repo: PostgresRepo, *, ws: date, we: date) -> tuple[float, float, int]:
    rows = repo.fetch_realized_trades(limit=5000)
    pnl = 0.0
    wins = 0
    total = 0
    for r in rows:
        close_ts = _parse_ts(r.get("ts_close"))
        if close_ts is None:
            continue
        d = close_ts.astimezone(KST).date()
        if d < ws or d > we:
            continue
        total += 1
        rpnl = float(r.get("realized_pnl") or 0.0)
        pnl += rpnl
        if rpnl > 0:
            wins += 1
    win_rate = (wins / total * 100.0) if total > 0 else 0.0
    return pnl, win_rate, total


def send_daily_review(*, repo: PostgresRepo, notifier: NotificationService, now_kst: datetime) -> bool:
    day = now_kst.date().isoformat()
    latest_payload = _latest_event_payload(repo, event_type="DAILY_REVIEW_SENT")
    if str(latest_payload.get("day") or "") == day:
        return False

    row = None
    for r in repo.fetch_pnl_daily(limit=14):
        if str(r.get("day") or "") == day:
            row = r
            break
    realized = float((row or {}).get("realized_pnl") or 0.0)
    fees = float((row or {}).get("fees_paid") or 0.0)
    trades = int((row or {}).get("trades_count") or 0)
    mdd = (row or {}).get("max_drawdown")

    event_id = uuid.uuid4()
    repo.insert_event(
        DbEvent(
            event_id=event_id,
            ts=_utcnow(),
            event_type="DAILY_REVIEW_SENT",
            entity_type="pnl_daily",
            entity_id=day,
            run_id=None,
            rule_version_id=None,
            payload={
                "day": day,
                "realized_pnl": realized,
                "fees_paid": fees,
                "trades_count": trades,
                "max_drawdown": mdd,
            },
        )
    )
    notifier.notify_daily_review(
        event_id=event_id,
        day=day,
        realized_pnl=realized,
        fees_paid=fees,
        trades_count=trades,
        max_drawdown=float(mdd) if mdd is not None else None,
    )
    return True


def send_weekly_review(*, repo: PostgresRepo, notifier: NotificationService, now_kst: datetime) -> bool:
    ws, we = _week_window(now_kst.date())
    week_start = ws.isoformat()
    week_label = f"{week_start}~{we.isoformat()}"

    latest_payload = _latest_event_payload(repo, event_type="WEEKLY_REVIEW_SENT")
    if str(latest_payload.get("week_start") or "") == week_start:
        return False

    weekly_pnl, win_rate, total = _weekly_trade_metrics(repo, ws=ws, we=we)
    loss_tags_top3 = _top3_loss_tags(repo, ws=ws, we=we)

    event_id = uuid.uuid4()
    repo.insert_event(
        DbEvent(
            event_id=event_id,
            ts=_utcnow(),
            event_type="WEEKLY_REVIEW_SENT",
            entity_type="strategy_reviews",
            entity_id=week_start,
            run_id=None,
            rule_version_id=None,
            payload={
                "week_start": week_start,
                "week_end": we.isoformat(),
                "week_label": week_label,
                "weekly_pnl": weekly_pnl,
                "win_rate": win_rate,
                "trades_count": total,
                "loss_tags_top3": loss_tags_top3,
                "rule_patch_status": "자동 룰패치 미연결",
            },
        )
    )
    notifier.notify_weekly_review(
        event_id=event_id,
        week_label=week_label,
        weekly_pnl=weekly_pnl,
        win_rate=win_rate,
        loss_tags_top3=loss_tags_top3,
        rule_patch_status="자동 룰패치 미연결",
    )
    return True


def run_once(*, repo: PostgresRepo, notifier: NotificationService, rules_raw: dict[str, Any]) -> dict[str, bool]:
    now = _now_kst()
    reporting = (rules_raw.get("reporting") or {}) if isinstance(rules_raw, dict) else {}

    latest_daily = _latest_event_payload(repo, event_type="DAILY_REVIEW_SENT")
    latest_weekly = _latest_event_payload(repo, event_type="WEEKLY_REVIEW_SENT")

    daily_time = str(reporting.get("daily_review_time_kst") or "23:10")
    weekly_day = str(reporting.get("weekly_review_day") or "SUN")
    weekly_time = str(reporting.get("weekly_review_time_kst") or "21:00")

    sent_daily = False
    if should_send_daily_review(
        now_kst=now,
        daily_time_kst=daily_time,
        latest_sent_day=str(latest_daily.get("day") or "") or None,
    ):
        sent_daily = send_daily_review(repo=repo, notifier=notifier, now_kst=now)

    do_weekly, _ws, _we = should_send_weekly_review(
        now_kst=now,
        weekly_day=weekly_day,
        weekly_time_kst=weekly_time,
        latest_sent_week_start=str(latest_weekly.get("week_start") or "") or None,
    )
    sent_weekly = send_weekly_review(repo=repo, notifier=notifier, now_kst=now) if do_weekly else False

    return {"daily": bool(sent_daily), "weekly": bool(sent_weekly)}


def main() -> int:
    p = argparse.ArgumentParser(description="Run review loop (daily/weekly report notifications).")
    p.add_argument("--sleep-sec", type=float, default=60.0)
    p.add_argument("--once", action="store_true")
    args = p.parse_args()

    load_dotenv()
    rules_raw: dict[str, Any] = yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8")) or {}
    repo = PostgresRepo()
    notifier = NotificationService(repo)

    if args.once:
        out = run_once(repo=repo, notifier=notifier, rules_raw=rules_raw)
        print(f"[완료] review once: daily={out['daily']}, weekly={out['weekly']}")
        return 0

    print("[시작] review loop running")
    while True:
        try:
            out = run_once(repo=repo, notifier=notifier, rules_raw=rules_raw)
            if out["daily"] or out["weekly"]:
                print(f"[전송] daily={out['daily']}, weekly={out['weekly']}")
        except Exception as exc:
            print(f"[경고] review loop error: {exc}")
        time.sleep(float(args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())
