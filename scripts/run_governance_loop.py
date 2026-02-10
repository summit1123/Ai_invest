#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.config.llm_router import llm_route_for_agent  # noqa: E402
from ai_invest.config.rules_loader import load_rules  # noqa: E402
from ai_invest.agents.secretary_agent import generate_meeting_minutes  # noqa: E402
from ai_invest.agents.strategy_coordinator_agent import propose_trade_plan  # noqa: E402
from ai_invest.market_data.features import build_feature_snapshot_from_candles  # noqa: E402
from ai_invest.market_data.upbit_public import fetch_candles_minutes, fetch_market_snapshot  # noqa: E402
from ai_invest.notifications.service import NotificationService  # noqa: E402
from ai_invest.research.rss import fetch_crypto_headlines, summarize_headlines_text  # noqa: E402
from ai_invest.storage.postgres import DbEvent, DbMeetingMessage, DbMeetingSession, PostgresRepo  # noqa: E402


KST = ZoneInfo("Asia/Seoul")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_kst() -> datetime:
    return _utcnow().astimezone(KST)


def _timeframe_to_minutes(tf: str) -> int:
    tf = tf.strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    raise ValueError(f"Unsupported timeframe: {tf}")


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(":", 1)
    return int(hh), int(mm)


def _slot_dt_for_today_kst(now_kst: datetime, hhmm: str) -> datetime:
    hh, mm = _parse_hhmm(hhmm)
    return now_kst.replace(hour=hh, minute=mm, second=0, microsecond=0)


def _default_meeting_times() -> list[str]:
    # 기본은 하루 2회(정시 회의). 실시간 트레이딩(Trigger)은 Safe Judge가 즉시 처리하고,
    # 회의는 종목/비중/룰을 갱신하는 거버넌스 용도로만 사용한다.
    return ["09:00", "21:00"]


def _get_meeting_times_kst(raw_rules: dict[str, Any]) -> list[str]:
    gov = raw_rules.get("governance") if isinstance(raw_rules, dict) else None
    times = (gov or {}).get("daily_meeting_times_kst") if isinstance(gov, dict) else None
    if isinstance(times, list) and all(isinstance(x, str) and ":" in x for x in times):
        return [x.strip() for x in times]
    return _default_meeting_times()


def _next_slot_kst(now_kst: datetime, times: list[str], current: str) -> datetime:
    # Find next time in today list; else tomorrow first slot.
    today = now_kst.date()
    slots = [_slot_dt_for_today_kst(now_kst, t) for t in times]
    # Replace date component explicitly (slot_dt_for_today_kst already uses today)
    cur_dt = _slot_dt_for_today_kst(now_kst, current)
    for s in slots:
        if s > cur_dt:
            return s
    # tomorrow first
    first = _slot_dt_for_today_kst(now_kst, times[0])
    return (first + timedelta(days=1)).replace(tzinfo=KST)


def _score_symbol(
    *,
    symbol: str,
    snap: Any,
    feat: Any,
    rsi_min: float,
    vol_min: float,
    max_spread_bps: float,
) -> float:
    # 단순 점수(데모): rsi/볼륨이 높고, 스프레드가 낮을수록 가산.
    rsi = float(getattr(feat, "rsi_14", 50.0))
    volz = float(getattr(feat, "vol_zscore", 0.0))
    spread = float(getattr(snap, "spread_bps", 0.0))
    score = 0.0
    score += (rsi - rsi_min) / 100.0
    score += (volz - vol_min) / 10.0
    if spread > max_spread_bps:
        score -= (spread - max_spread_bps) / 100.0
    # Small bias for major symbol (if configured multi-symbol later)
    if symbol.endswith("-BTC"):
        score += 0.02
    return score


def run_once(
    *,
    repo: PostgresRepo,
    notifier: NotificationService,
    rules_raw: dict[str, Any],
    force: bool = False,
) -> str | None:
    now_kst = _now_kst()
    times = _get_meeting_times_kst(rules_raw)

    # Within +/- window minutes of slot => trigger.
    window_min = int(((rules_raw.get("governance") or {}).get("meeting_window_min") or 5) if isinstance(rules_raw, dict) else 5)

    hit_slot: str | None = None
    if force:
        hit_slot = now_kst.strftime("%H:%M")
    else:
        for t in times:
            slot_dt = _slot_dt_for_today_kst(now_kst, t)
            delta_min = abs((now_kst - slot_dt).total_seconds()) / 60.0
            if delta_min <= window_min:
                hit_slot = t
                break

        if not hit_slot:
            return None

    slot_key = f"{now_kst.date().isoformat()} {hit_slot}"
    if repo.meeting_slot_exists(slot_key=slot_key):
        return slot_key

    rules = load_rules("rules.yaml")
    timeframe_entry = str(rules_raw.get("signal", {}).get("timeframe_entry", "15m"))
    tf_min = _timeframe_to_minutes(timeframe_entry)

    symbols = list(rules.universe.symbols)
    if not symbols:
        return slot_key

    rsi_min = float(rules_raw.get("signal", {}).get("rsi_min", 50.0))
    vol_min = float(rules_raw.get("signal", {}).get("volume_zscore_min", 1.2))
    max_spread = float((rules_raw.get("cost_guard") or {}).get("max_spread_bps_entry", 9999.0))

    evaluated: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    for sym in symbols:
        snap = fetch_market_snapshot(sym)
        candles = fetch_candles_minutes(sym, unit=tf_min, count=200)
        highs = [float(c["high_price"]) for c in candles]
        lows = [float(c["low_price"]) for c in candles]
        closes = [float(c["trade_price"]) for c in candles]
        volumes = [float(c["candle_acc_trade_volume"]) for c in candles]
        feat = build_feature_snapshot_from_candles(highs=highs, lows=lows, closes=closes, volumes=volumes)
        score = _score_symbol(
            symbol=sym,
            snap=snap,
            feat=feat,
            rsi_min=rsi_min,
            vol_min=vol_min,
            max_spread_bps=max_spread,
        )
        row = {
            "symbol": sym,
            "score": score,
            "snapshot": {"last_price": snap.last_price, "spread_bps": snap.spread_bps},
            "features": {"rsi_14": feat.rsi_14, "atr_pct": feat.atr_pct, "vol_zscore": feat.vol_zscore},
        }
        evaluated.append(row)
        if not best or score > float(best["score"]):
            best = row

    best = best or evaluated[0]

    # Coordinator input: ops snapshot + headline context (lightweight RSS).
    pause = repo.fetch_pause_state()
    recon = repo.fetch_latest_reconciliation()
    headlines = fetch_crypto_headlines(symbol=str(best.get("symbol") or symbols[0]), limit=10)
    research_brief = {
        "headlines": headlines,
        "headlines_text": summarize_headlines_text(headlines, max_items=6),
    }

    default_target = float((rules_raw.get("governance") or {}).get("default_target_position_pct", 10.0))
    plan = propose_trade_plan(
        candidates=evaluated,
        allowed_symbols=symbols,
        default_target_position_pct=default_target,
        max_position_pct_per_symbol=float(rules.risk.max_position_pct_per_symbol),
        cost_guard=dict(rules_raw.get("cost_guard") or {}),
        ops_state={"pause": pause, "latest_reconciliation": recon},
        research_brief=research_brief,
        llm_route=llm_route_for_agent(rules_raw=rules_raw, agent_name="strategy_coordinator"),
    )
    target_pct = float(plan.target_position_pct)
    best = next((row for row in evaluated if str(row.get("symbol") or "") == plan.symbol), best)

    meeting_id = uuid.uuid4()
    started_at = _utcnow()
    ended_at = started_at + timedelta(seconds=2)

    summary_short = (
        f"[{slot_key}] Trade Plan: {plan.symbol} target={target_pct:.1f}% "
        f"(score={float(best.get('score') or 0.0):.3f})"
    )
    action_items = [
        {"owner": "research_agent", "action": "다음 슬롯까지 리스크(스프레드/ATR) 모니터링", "due_date": str(now_kst.date())},
        {"owner": "ops_agent", "action": "정합성 WARN/FAIL 여부 점검 및 알림 누락 확인", "due_date": str(now_kst.date())},
    ]

    # Draft transcript (stored to DB after session creation).
    draft_messages: list[dict[str, Any]] = [
        {
            "sender_agent": "research_agent",
            "message_type": "EVIDENCE",
            "content": (
                f"후보 평가 {len(evaluated)}개. 상위: {best['symbol']} score={best['score']:.3f}\n"
                + (f"뉴스(요약): {research_brief.get('headlines_text')}" if research_brief.get("headlines_text") else "뉴스: (없음)")
            ),
            "payload": {"evaluated": evaluated[:5], "headlines": headlines[:8]},
            "confidence": 0.75,
        },
        {
            "sender_agent": "market_agent",
            "message_type": "CLAIM",
            "content": f"{best['symbol']} 선택 근거: RSI14={best['features']['rsi_14']:.1f}, VolZ={best['features']['vol_zscore']:.2f}, spread={best['snapshot']['spread_bps']:.2f}bps",
            "payload": {"best": best},
            "confidence": 0.7,
        },
        {
            "sender_agent": "risk_agent",
            "message_type": "CLAIM",
            "content": f"리스크 상한: max_position_pct_per_symbol={rules.risk.max_position_pct_per_symbol}%, max_daily_loss_pct={rules.risk.max_daily_loss_pct}%",
            "payload": {"risk": asdict(rules.risk)},
            "confidence": 0.7,
        },
        {
            "sender_agent": "ops_agent",
            "message_type": "CLAIM",
            "content": "운영 체크: recon/pause 상태는 Safe Judge 게이트로 최우선 차단",
            "payload": None,
            "confidence": 0.65,
        },
        {
            "sender_agent": "strategy_coordinator",
            "message_type": "PROPOSAL",
            "content": (
                f"Trade Plan 제안: {plan.symbol} target_position_pct={target_pct:.1f}% (다음 슬롯까지 유지)\n"
                f"notes: {plan.notes}"
            ),
            "payload": {
                "trade_plan": {"symbol": plan.symbol, "target_position_pct": target_pct, "constraints": dict(plan.constraints or {})},
                "llm_meta": plan.llm_meta,
                "error": plan.error,
                "used_llm": bool(plan.used_llm),
            },
            "confidence": 0.7,
        },
    ]

    session_map: dict[str, Any] = {
        "meeting_id": str(meeting_id),
        "meeting_type": "DAILY_STRATEGY",
        "status": "CLOSED",
        "started_at": started_at,
        "ended_at": ended_at,
        "facilitator": "strategy_coordinator",
        "participants": ["research_agent", "market_agent", "risk_agent", "ops_agent", "strategy_coordinator"],
        "agenda": {"slot_key": slot_key, "symbols": symbols, "timeframe_entry": timeframe_entry},
        "summary": summary_short,
        "decisions": {
            "trade_plan": {
                "symbol": plan.symbol,
                "target_position_pct": target_pct,
                "constraints": dict(plan.constraints or {}),
                "notes": plan.notes,
            }
        },
        "action_items": {"items": action_items},
    }
    assistant = generate_meeting_minutes(
        session=session_map,
        messages=draft_messages,
        llm_route=llm_route_for_agent(rules_raw=rules_raw, agent_name="secretary_agent"),
    )
    assistant_minutes = assistant.text

    repo.insert_meeting_session(
        DbMeetingSession(
            meeting_id=meeting_id,
            meeting_type="DAILY_STRATEGY",
            status="CLOSED",
            started_at=started_at,
            ended_at=ended_at,
            facilitator="strategy_coordinator",
            participants=session_map["participants"],
            agenda=session_map["agenda"],
            summary=assistant_minutes,
            decisions=session_map["decisions"],
            action_items=session_map["action_items"],
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
            payload={"meeting_id": str(meeting_id), "meeting_type": "DAILY_STRATEGY", "slot_key": slot_key},
        )
    )

    # Store transcript to DB/events.
    for dm in draft_messages:
        msg_id = uuid.uuid4()
        ts = _utcnow()
        sender = str(dm.get("sender_agent") or "")
        msg_type = str(dm.get("message_type") or "")
        content = str(dm.get("content") or "")
        payload = dm.get("payload")
        conf = dm.get("confidence")
        repo.insert_meeting_message(
            DbMeetingMessage(
                message_id=msg_id,
                meeting_id=meeting_id,
                ts=ts,
                sender_agent=sender,
                message_type=msg_type,
                content=content,
                payload=payload,
                confidence=float(conf) if conf is not None else None,
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
                "slot_key": slot_key,
                "summary_short": summary_short,
                "assistant_minutes": assistant_minutes,
                "assistant_meta": {
                    "used_llm": assistant.used_llm,
                    "model": assistant.model,
                    "endpoint": assistant.endpoint,
                    "usage": assistant.usage,
                    "error": assistant.error,
                },
            },
        )
    )
    try:
        notifier.notify_meeting_summary(
            event_id=summary_event_id,
            meeting_id=str(meeting_id),
            summary=summary_short,
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
            payload={"meeting_id": str(meeting_id), "slot_key": slot_key, "items": action_items},
        )
    )
    try:
        notifier.notify_meeting_action_items(event_id=action_event_id, meeting_id=str(meeting_id), items=action_items)
    except Exception:
        pass

    # Trade plan event (used by UI and runtime selection later).
    next_slot = _next_slot_kst(now_kst, times, hit_slot) if hit_slot in times else (now_kst + timedelta(hours=8))
    constraints = {
        "max_spread_bps_entry": max_spread,
        "rsi_min": rsi_min,
        "volume_zscore_min": vol_min,
        **(dict(plan.constraints or {})),
    }
    plan_payload = {
        "slot_key": slot_key,
        "meeting_id": str(meeting_id),
        "symbol": plan.symbol,
        "target_position_pct": float(target_pct),
        "valid_from_kst": (_slot_dt_for_today_kst(now_kst, hit_slot) if hit_slot in times else now_kst).isoformat(),
        "valid_to_kst": next_slot.isoformat(),
        "constraints": constraints,
        "notes": plan.notes,
    }
    repo.insert_event(
        DbEvent(
            event_id=uuid.uuid4(),
            ts=ended_at,
            event_type="TRADE_PLAN_SET",
            entity_type="trade_plans",
            entity_id=slot_key,
            run_id=None,
            rule_version_id=None,
            payload=plan_payload,
        )
    )

    return slot_key


def main() -> int:
    p = argparse.ArgumentParser(description="Run governance loop (3x/day meeting + trade plan event).")
    p.add_argument("--sleep-sec", type=float, default=30.0)
    p.add_argument("--once", action="store_true", help="Run a single check cycle and exit.")
    p.add_argument("--force", action="store_true", help="Ignore schedule window and create a meeting/plan now.")
    args = p.parse_args()

    load_dotenv()
    rules_raw = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))

    repo = PostgresRepo()
    notifier = NotificationService(repo)

    if args.once:
        slot = run_once(repo=repo, notifier=notifier, rules_raw=rules_raw, force=bool(args.force))
        print(f"[완료] once done. slot={slot}")
        return 0

    print("[시작] governance loop running (meeting schedule + trade plan)")
    while True:
        try:
            slot = run_once(repo=repo, notifier=notifier, rules_raw=rules_raw, force=bool(args.force))
            if slot:
                print(f"[트리거] meeting slot processed: {slot}")
        except Exception as exc:
            # Fail-open for the scheduler loop (should not kill the process).
            print(f"[경고] governance loop error: {exc}")
        time.sleep(float(args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())
