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
from ai_invest.config.llm_router import llm_route_for_agent  # noqa: E402
from ai_invest.agents.strategy_coordinator_agent import propose_weekly_priority  # noqa: E402
from ai_invest.notifications.service import NotificationService  # noqa: E402
from ai_invest.storage.postgres import DbEvent, DbStrategyReview, PostgresRepo  # noqa: E402

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


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s else None
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


def _close_finished_weekly_priorities(*, repo: PostgresRepo, now_kst: datetime) -> int:
    """Close stale OPEN/IN_PROGRESS priorities with deterministic evidence."""

    today = now_kst.date()
    rows = repo.fetch_strategy_reviews(limit=260)
    closed = 0
    for row in rows:
        status = str(row.get("status") or "").strip().upper()
        if status not in {"OPEN", "IN_PROGRESS"}:
            continue
        ws = _parse_day(row.get("week_start"))
        we = _parse_day(row.get("week_end"))
        if ws is None or we is None:
            continue
        if we >= today:
            continue

        weekly_pnl, win_rate, trades = _weekly_trade_metrics(repo, ws=ws, we=we)
        loss_tags_top3 = _top3_loss_tags(repo, ws=ws, we=we)
        criteria = dict(row.get("success_criteria") or {})
        checks: list[bool] = []

        min_win_rate = _as_float(criteria.get("min_win_rate_pct"))
        if min_win_rate is not None:
            checks.append(float(win_rate) >= float(min_win_rate))

        min_weekly_pnl = _as_float(criteria.get("min_weekly_pnl_krw"))
        if min_weekly_pnl is not None:
            checks.append(float(weekly_pnl) >= float(min_weekly_pnl))

        min_trades = _as_float(criteria.get("min_trades_count"))
        if min_trades is not None:
            checks.append(int(trades) >= int(min_trades))

        passed = all(checks) if checks else (int(trades) > 0 and float(weekly_pnl) >= 0.0)
        next_status = "DONE" if passed else "CANCELED"
        evidence = {
            "closed_at_kst": now_kst.isoformat(),
            "weekly_pnl": float(weekly_pnl),
            "win_rate_pct": float(round(win_rate, 2)),
            "trades_count": int(trades),
            "loss_tags_top3": str(loss_tags_top3),
            "checks_used": {
                "min_win_rate_pct": min_win_rate,
                "min_weekly_pnl_krw": min_weekly_pnl,
                "min_trades_count": int(min_trades) if min_trades is not None else None,
            },
            "checks_passed": bool(passed),
        }
        review_id = str(row.get("review_id") or "").strip()
        if not review_id:
            continue

        repo.update_strategy_review(
            review_id=review_id,
            status=next_status,
            evidence=evidence,
        )
        repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=_utcnow(),
                event_type="WEEKLY_PRIORITY_CLOSED",
                entity_type="strategy_reviews",
                entity_id=review_id,
                run_id=None,
                rule_version_id=None,
                payload={
                    "review_id": review_id,
                    "status": next_status,
                    "week_start": ws.isoformat(),
                    "week_end": we.isoformat(),
                    "evidence": evidence,
                },
            )
        )
        closed += 1
    return int(closed)


def _has_weekly_priority(repo: PostgresRepo, *, week_start: str) -> bool:
    for row in repo.fetch_strategy_reviews(limit=120):
        if str(row.get("week_start") or "") == str(week_start):
            return True
    return False


def _ensure_weekly_priority(
    *,
    repo: PostgresRepo,
    notifier: NotificationService,
    rules_raw: dict[str, Any],
    week_start: str,
    week_end: str,
) -> bool:
    if _has_weekly_priority(repo, week_start=week_start):
        return False

    ws = _parse_day(week_start)
    we = _parse_day(week_end)
    if ws is None or we is None:
        return False

    pnl = repo.fetch_pnl_daily(limit=30)
    trades = repo.fetch_realized_trades(limit=500)
    execm = repo.fetch_execution_metrics(limit=500)
    recon = repo.fetch_reconciliation_checks(limit=500)
    agents_cfg = ((rules_raw.get("llm") or {}).get("agents") or {}) if isinstance(rules_raw, dict) else {}
    weekly_key_exists = isinstance(agents_cfg, dict) and ("strategy_coordinator_weekly" in agents_cfg)
    route = llm_route_for_agent(
        rules_raw=rules_raw,
        agent_name=("strategy_coordinator_weekly" if weekly_key_exists else "strategy_coordinator"),
    )
    proposal = propose_weekly_priority(
        today_kst=_now_kst().date(),
        pnl_daily=pnl,
        realized_trades=trades,
        execution_metrics=execm,
        reconciliation_checks=recon,
        llm_route=route,
    )
    success_criteria = dict(proposal.success_criteria or {})
    if proposal.deadline:
        success_criteria["deadline"] = proposal.deadline
    if proposal.llm_meta:
        success_criteria["_llm_meta"] = dict(proposal.llm_meta)
    if proposal.error:
        success_criteria["_error"] = str(proposal.error)

    review_id = uuid.uuid4()
    repo.insert_strategy_review(
        DbStrategyReview(
            review_id=review_id,
            week_start=ws,
            week_end=we,
            priority_title=str(proposal.weekly_priority),
            hypothesis=str(proposal.hypothesis),
            owner=str(proposal.owner or "strategy_coordinator"),
            success_criteria=success_criteria,
            status="OPEN",
            evidence={},
            run_id=None,
        )
    )

    event_id = uuid.uuid4()
    repo.insert_event(
        DbEvent(
            event_id=event_id,
            ts=_utcnow(),
            event_type="WEEKLY_PRIORITY_SET",
            entity_type="strategy_reviews",
            entity_id=str(review_id),
            run_id=None,
            rule_version_id=None,
            payload={
                "review_id": str(review_id),
                "week_start": str(ws.isoformat()),
                "week_end": str(we.isoformat()),
                "priority_title": str(proposal.weekly_priority),
                "hypothesis": str(proposal.hypothesis),
                "owner": str(proposal.owner or "strategy_coordinator"),
                "success_criteria": success_criteria,
                "assistant_meta": {"used_llm": proposal.used_llm, **(proposal.llm_meta or {}), "error": proposal.error},
            },
        )
    )
    try:
        notifier.notify_weekly_priority(
            event_id=event_id,
            week_label=f"{ws.isoformat()}~{we.isoformat()}",
            priority_title=str(proposal.weekly_priority),
            hypothesis=str(proposal.hypothesis),
            owner=str(proposal.owner or "strategy_coordinator"),
        )
    except Exception:
        pass
    return True


def send_daily_review(*, repo: PostgresRepo, notifier: NotificationService, now_kst: datetime, force: bool = False) -> bool:
    day = now_kst.date().isoformat()
    latest_payload = _latest_event_payload(repo, event_type="DAILY_REVIEW_SENT")
    if (not force) and str(latest_payload.get("day") or "") == day:
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


def send_weekly_review(
    *,
    repo: PostgresRepo,
    notifier: NotificationService,
    now_kst: datetime,
    rules_raw: dict[str, Any],
    force: bool = False,
) -> bool:
    ws, we = _week_window(now_kst.date())
    week_start = ws.isoformat()
    week_label = f"{week_start}~{we.isoformat()}"

    latest_payload = _latest_event_payload(repo, event_type="WEEKLY_REVIEW_SENT")
    if (not force) and str(latest_payload.get("week_start") or "") == week_start:
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
    _ensure_weekly_priority(
        repo=repo,
        notifier=notifier,
        rules_raw=rules_raw,
        week_start=week_start,
        week_end=we.isoformat(),
    )
    return True


def run_once(
    *,
    repo: PostgresRepo,
    notifier: NotificationService,
    rules_raw: dict[str, Any],
    force_daily: bool = False,
    force_weekly: bool = False,
) -> dict[str, bool]:
    now = _now_kst()
    _close_finished_weekly_priorities(repo=repo, now_kst=now)
    reporting = (rules_raw.get("reporting") or {}) if isinstance(rules_raw, dict) else {}
    ws_curr, we_curr = _week_window(now.date())
    # Weekly review 전송 시점과 무관하게, 현재 주 priority 레코드는 항상 1건 유지한다.
    _ensure_weekly_priority(
        repo=repo,
        notifier=notifier,
        rules_raw=rules_raw,
        week_start=ws_curr.isoformat(),
        week_end=we_curr.isoformat(),
    )

    latest_daily = _latest_event_payload(repo, event_type="DAILY_REVIEW_SENT")
    latest_weekly = _latest_event_payload(repo, event_type="WEEKLY_REVIEW_SENT")

    daily_time = str(reporting.get("daily_review_time_kst") or "23:10")
    weekly_day = str(reporting.get("weekly_review_day") or "SUN")
    weekly_time = str(reporting.get("weekly_review_time_kst") or "21:00")

    sent_daily = False
    if force_daily or should_send_daily_review(
        now_kst=now,
        daily_time_kst=daily_time,
        latest_sent_day=str(latest_daily.get("day") or "") or None,
    ):
        sent_daily = send_daily_review(repo=repo, notifier=notifier, now_kst=now, force=bool(force_daily))

    do_weekly, _ws, _we = should_send_weekly_review(
        now_kst=now,
        weekly_day=weekly_day,
        weekly_time_kst=weekly_time,
        latest_sent_week_start=str(latest_weekly.get("week_start") or "") or None,
    )
    sent_weekly = (
        send_weekly_review(
            repo=repo,
            notifier=notifier,
            now_kst=now,
            rules_raw=rules_raw,
            force=bool(force_weekly),
        )
        if (force_weekly or do_weekly)
        else False
    )

    return {"daily": bool(sent_daily), "weekly": bool(sent_weekly)}


def main() -> int:
    p = argparse.ArgumentParser(description="Run review loop (daily/weekly report notifications).")
    p.add_argument("--sleep-sec", type=float, default=60.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--force-daily", action="store_true", help="send daily review regardless of schedule")
    p.add_argument("--force-weekly", action="store_true", help="send weekly review regardless of schedule")
    args = p.parse_args()

    load_dotenv()
    rules_raw: dict[str, Any] = yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8")) or {}
    repo = PostgresRepo()
    notifier = NotificationService(repo)

    if args.once:
        out = run_once(
            repo=repo,
            notifier=notifier,
            rules_raw=rules_raw,
            force_daily=bool(args.force_daily),
            force_weekly=bool(args.force_weekly),
        )
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
