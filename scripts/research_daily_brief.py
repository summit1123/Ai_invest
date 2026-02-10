#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.config.rules_loader import load_rules  # noqa: E402
from ai_invest.agents.research_agent import research_agent_daily_brief  # noqa: E402
from ai_invest.market_data.features import build_feature_snapshot_from_candles  # noqa: E402
from ai_invest.market_data.upbit_public import fetch_candles_minutes, fetch_market_snapshot  # noqa: E402
from ai_invest.notifications.service import NotificationService  # noqa: E402
from ai_invest.research.rss import fetch_crypto_headlines  # noqa: E402
from ai_invest.storage.postgres import DbAgentDailyReport, DbEvent, PostgresRepo  # noqa: E402


KST = ZoneInfo("Asia/Seoul")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def timeframe_to_minutes(tf: str) -> int:
    tf = tf.strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    raise ValueError(f"Unsupported timeframe: {tf}")


def build_brief(
    *,
    symbol: str,
    tf_min: int,
    rules_raw: dict[str, Any],
    repo: PostgresRepo,
) -> tuple[str, dict[str, Any], list[str], list[str]]:
    snap = fetch_market_snapshot(symbol)
    candles = fetch_candles_minutes(symbol, unit=tf_min, count=200)
    highs = [float(c["high_price"]) for c in candles]
    lows = [float(c["low_price"]) for c in candles]
    closes = [float(c["trade_price"]) for c in candles]
    volumes = [float(c["candle_acc_trade_volume"]) for c in candles]
    feat = build_feature_snapshot_from_candles(highs=highs, lows=lows, closes=closes, volumes=volumes)

    pause = repo.fetch_pause_state()
    recon = repo.fetch_latest_reconciliation(symbol=symbol)
    latest_safe = repo.fetch_latest_decision(judge_type="SAFE")

    cost_guard = (rules_raw.get("cost_guard") or {}) if isinstance(rules_raw, dict) else {}
    max_spread = float(cost_guard.get("max_spread_bps_entry") or 9999.0)

    risk_watchlist: list[str] = []
    next_actions: list[str] = []

    if float(snap.spread_bps) > max_spread:
        risk_watchlist.append(f"스프레드 과다({snap.spread_bps:.2f}bps > {max_spread:.2f}bps)")
        next_actions.append("진입 보류(HOLD) 및 유동성 정상화 대기")
    if pause.get("paused"):
        risk_watchlist.append("시스템 PAUSE 상태")
        next_actions.append("pause_log/정합성 이슈 확인 후 RESUME")
    if recon and str(recon.get('status')).upper() == "FAIL":
        risk_watchlist.append("정합성 FAIL")
        next_actions.append("reconciliation_checks diff 확인 및 원인 제거")

    headlines = fetch_crypto_headlines(symbol=symbol, limit=12)
    brief = research_agent_daily_brief(
        symbol=symbol,
        snapshot={"last_price": snap.last_price, "best_bid": snap.best_bid, "best_ask": snap.best_ask, "mid_price": snap.mid_price, "spread_bps": snap.spread_bps},
        features={"atr_pct": feat.atr_pct, "rsi_14": feat.rsi_14, "vol_zscore": feat.vol_zscore},
        ops={"pause": pause, "latest_reconciliation": recon},
        headlines=headlines,
    )

    summary = brief.summary

    findings = {
        "symbol": symbol,
        "snapshot": {
            "last_price": snap.last_price,
            "best_bid": snap.best_bid,
            "best_ask": snap.best_ask,
            "mid_price": snap.mid_price,
            "spread_bps": snap.spread_bps,
        },
        "features": {"atr_pct": feat.atr_pct, "rsi_14": feat.rsi_14, "vol_zscore": feat.vol_zscore},
        "ops": {"pause": pause, "latest_reconciliation": recon},
        "latest_safe_decision": latest_safe,
        "news_headlines": headlines,
        "key_findings": brief.key_findings,
        "llm_meta": brief.llm_meta,
    }

    # Prefer agent output (LLM or deterministic fallback).
    risk_watchlist = list(brief.risk_watchlist or risk_watchlist)
    next_actions = list(brief.next_actions or next_actions)

    return summary, findings, risk_watchlist, next_actions


def main() -> int:
    p = argparse.ArgumentParser(description="Generate and store Research Daily Brief.")
    p.add_argument("--symbol", type=str, default="")
    p.add_argument("--timeframe", type=str, default="15m")
    p.add_argument("--title", type=str, default="일일 리서치 브리프(뉴스+시장)")
    args = p.parse_args()

    load_dotenv()
    rules = load_rules("rules.yaml")
    rules_raw = __import__("yaml").safe_load(Path("rules.yaml").read_text(encoding="utf-8"))

    symbol = args.symbol.strip() or rules.universe.symbols[0]
    tf_min = timeframe_to_minutes(args.timeframe)

    repo = PostgresRepo()
    notifier = NotificationService(repo)

    now_kst = utcnow().astimezone(KST)
    brief_date = now_kst.date().isoformat()

    summary, findings, risk_watchlist, next_actions = build_brief(
        symbol=symbol,
        tf_min=tf_min,
        rules_raw=rules_raw,
        repo=repo,
    )

    report_id = uuid.uuid4()
    repo.insert_agent_daily_report(
        DbAgentDailyReport(
            report_id=report_id,
            report_date=now_kst.date(),
            agent_name="research_agent",
            team_scope="RESEARCH",
            title=args.title,
            summary=summary,
            findings=findings,
            risks={"watchlist": risk_watchlist},
            action_items={"next_actions": next_actions},
            run_id=None,
            rule_version_id=None,
        )
    )

    event_id = uuid.uuid4()
    repo.insert_event(
        DbEvent(
            event_id=event_id,
            ts=utcnow(),
            event_type="RESEARCH_DAILY_BRIEF",
            entity_type="agent_daily_reports",
            entity_id=str(report_id),
            run_id=None,
            rule_version_id=None,
            payload={
                "brief_date": brief_date,
                "report_id": str(report_id),
                "symbol": symbol,
                "summary": summary,
                "risk_watchlist": risk_watchlist,
                "next_actions": next_actions,
            },
        )
    )

    try:
        notifier.notify_research_daily_brief(
            event_id=event_id,
            brief_date=brief_date,
            summary=summary,
            risk_watchlist=risk_watchlist,
        )
    except Exception:
        pass

    print(f"[완료] RESEARCH_DAILY_BRIEF stored: report_id={report_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
