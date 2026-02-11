from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from zoneinfo import ZoneInfo

from ai_invest.agents.research_agent import research_agent_daily_brief
from ai_invest.config.capital_policy import resolve_capital_policy
from ai_invest.config.llm_router import llm_route_for_agent
from ai_invest.config.rules_loader import RulesConfig, load_rules
from ai_invest.market_data.features import build_feature_snapshot_from_candles
from ai_invest.market_data.upbit_public import fetch_candles_minutes, fetch_market_snapshot
from ai_invest.research.rss import fetch_crypto_headlines
from ai_invest.storage.postgres import DbAgentDailyReport, DbEvent, PostgresRepo

KST = ZoneInfo("Asia/Seoul")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_kst() -> datetime:
    return _utcnow().astimezone(KST)


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s else float(default)
    except Exception:
        return float(default)


def _timeframe_to_minutes(tf: str) -> int:
    tf = str(tf or "").strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    return 15


def _quote_currency(symbol: str) -> str:
    if "-" not in str(symbol):
        return "KRW"
    return str(symbol).split("-", 1)[0].strip().upper() or "KRW"


def _candidate_score(*, rsi: float, vol_z: float, spread_bps: float, rsi_min: float, vol_min: float, max_spread: float) -> float:
    score = 0.0
    score += (rsi - rsi_min) / 100.0
    score += (vol_z - vol_min) / 10.0
    if spread_bps > max_spread:
        score -= (spread_bps - max_spread) / 100.0
    return float(score)


def _report_age_minutes(*, now_utc: datetime, created_at: Any) -> float | None:
    if not isinstance(created_at, datetime):
        return None
    ts = created_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now_utc - ts.astimezone(timezone.utc)).total_seconds() / 60.0)


def collect_latest_work_reports(
    *,
    repo: PostgresRepo,
    agent_names: Sequence[str],
    max_age_minutes: int = 360,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now = now_utc or _utcnow()
    reports: dict[str, Any] = {}
    missing: list[str] = []
    stale: list[str] = []

    for agent_name in list(agent_names):
        name = str(agent_name).strip()
        if not name:
            continue
        row = repo.fetch_latest_agent_daily_report(agent_name=name)
        if not row:
            missing.append(name)
            continue

        age_min = _report_age_minutes(now_utc=now, created_at=row.get("created_at"))
        if age_min is None or age_min > float(max_age_minutes):
            stale.append(name)

        reports[name] = {
            "report_id": row.get("report_id"),
            "created_at": row.get("created_at").isoformat() if isinstance(row.get("created_at"), datetime) else row.get("created_at"),
            "title": row.get("title"),
            "summary": row.get("summary"),
            "age_minutes": age_min,
        }

    return {
        "reports": reports,
        "missing": sorted(set(missing)),
        "stale": sorted(set(stale)),
        "max_age_minutes": int(max_age_minutes),
        "checked_at_utc": now.isoformat(),
    }


def _store_report(
    *,
    repo: PostgresRepo,
    report_date_kst: datetime,
    cycle_key: str,
    agent_name: str,
    team_scope: str,
    title: str,
    summary: str,
    findings: Mapping[str, Any],
    risks: Mapping[str, Any],
    action_items: Mapping[str, Any],
    meeting_context: str | None = None,
) -> uuid.UUID:
    report_id = uuid.uuid4()
    repo.insert_agent_daily_report(
        DbAgentDailyReport(
            report_id=report_id,
            report_date=report_date_kst.date(),
            agent_name=str(agent_name),
            team_scope=str(team_scope),
            title=str(title),
            summary=str(summary),
            findings=dict(findings or {}),
            risks=dict(risks or {}),
            action_items=dict(action_items or {}),
            run_id=None,
            rule_version_id=None,
        )
    )

    repo.insert_event(
        DbEvent(
            event_id=uuid.uuid4(),
            ts=_utcnow(),
            event_type="AGENT_WORK_REPORT",
            entity_type="agent_daily_reports",
            entity_id=str(report_id),
            run_id=None,
            rule_version_id=None,
            payload={
                "cycle_key": cycle_key,
                "meeting_context": meeting_context,
                "report_id": str(report_id),
                "agent_name": str(agent_name),
                "team_scope": str(team_scope),
                "title": str(title),
            },
        )
    )
    return report_id


def _build_features(*, symbol: str, tf_min: int) -> tuple[dict[str, float], dict[str, float]]:
    snap = fetch_market_snapshot(symbol)
    candles = fetch_candles_minutes(symbol, unit=tf_min, count=200)
    highs = [float(c["high_price"]) for c in candles]
    lows = [float(c["low_price"]) for c in candles]
    closes = [float(c["trade_price"]) for c in candles]
    volumes = [float(c["candle_acc_trade_volume"]) for c in candles]
    feat = build_feature_snapshot_from_candles(highs=highs, lows=lows, closes=closes, volumes=volumes)
    snapshot = {
        "last_price": float(snap.last_price),
        "best_bid": float(snap.best_bid),
        "best_ask": float(snap.best_ask),
        "mid_price": float(snap.mid_price),
        "spread_bps": float(snap.spread_bps),
    }
    features = {
        "atr_pct": float(feat.atr_pct),
        "rsi_14": float(feat.rsi_14),
        "vol_zscore": float(feat.vol_zscore),
    }
    return snapshot, features


def _quant_candidate_rows(*, rules_raw: Mapping[str, Any], rules: RulesConfig, symbols: Sequence[str], tf_min: int) -> list[dict[str, Any]]:
    signal_cfg = (rules_raw.get("signal") or {}) if isinstance(rules_raw, Mapping) else {}
    cost_cfg = (rules_raw.get("cost_guard") or {}) if isinstance(rules_raw, Mapping) else {}

    rsi_min = _as_float(signal_cfg.get("rsi_min"), default=50.0)
    vol_min = _as_float(signal_cfg.get("volume_zscore_min"), default=1.2)
    max_spread = _as_float(cost_cfg.get("max_spread_bps_entry"), default=float(rules.cost_guard.max_spread_bps_entry))

    out: list[dict[str, Any]] = []
    for sym in list(symbols):
        try:
            snapshot, features = _build_features(symbol=sym, tf_min=tf_min)
            score = _candidate_score(
                rsi=_as_float(features.get("rsi_14")),
                vol_z=_as_float(features.get("vol_zscore")),
                spread_bps=_as_float(snapshot.get("spread_bps")),
                rsi_min=rsi_min,
                vol_min=vol_min,
                max_spread=max_spread,
            )
            out.append(
                {
                    "symbol": sym,
                    "score": score,
                    "snapshot": snapshot,
                    "features": features,
                }
            )
        except Exception as exc:
            out.append(
                {
                    "symbol": sym,
                    "score": -9.0,
                    "snapshot": {},
                    "features": {},
                    "error": str(exc)[:180],
                }
            )
    out.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return out


@dataclass(frozen=True)
class WorkCycleResult:
    cycle_key: str
    report_ids: dict[str, str]


def run_agent_work_cycle(
    *,
    repo: PostgresRepo,
    rules_raw: Mapping[str, Any],
    meeting_context: str | None = None,
) -> WorkCycleResult:
    """Run one pre-meeting agent work cycle and persist reports.

    This does not execute orders. It only creates report assets for meetings.
    """

    now_kst = _now_kst()
    cycle_key = now_kst.strftime("%Y-%m-%d %H:%M:%S")

    rules = load_rules("rules.yaml")
    symbols = list(rules.universe.symbols)
    default_symbol = symbols[0]
    tf_min = _timeframe_to_minutes(str((rules_raw.get("signal") or {}).get("timeframe_entry") or "15m"))
    candidates = _quant_candidate_rows(rules_raw=rules_raw, rules=rules, symbols=symbols, tf_min=tf_min)
    top = candidates[0] if candidates else {"symbol": default_symbol, "score": 0.0, "snapshot": {}, "features": {}}
    symbol = str(top.get("symbol") or default_symbol)
    snapshot = (top.get("snapshot") or {}) if isinstance(top.get("snapshot"), Mapping) else {}
    features = (top.get("features") or {}) if isinstance(top.get("features"), Mapping) else {}
    if not snapshot or not features:
        snapshot, features = _build_features(symbol=symbol, tf_min=tf_min)

    # Shared state
    pause = repo.fetch_pause_state()
    recon = repo.fetch_latest_reconciliation()

    # 1) research_agent prework
    try:
        headlines = fetch_crypto_headlines(symbol=symbol, limit=12)
    except Exception:
        headlines = []
    research_route = llm_route_for_agent(rules_raw=rules_raw, agent_name="research_agent")
    brief = research_agent_daily_brief(
        symbol=symbol,
        snapshot=snapshot,
        features=features,
        ops={"pause": pause, "latest_reconciliation": recon},
        headlines=headlines,
        llm_route=research_route,
    )
    research_id = _store_report(
        repo=repo,
        report_date_kst=now_kst,
        cycle_key=cycle_key,
        meeting_context=meeting_context,
        agent_name="research_agent",
        team_scope="RESEARCH",
        title="사전업무 리포트(Research)",
        summary=brief.summary,
        findings={"key_findings": list(brief.key_findings), "llm_meta": brief.llm_meta, "symbol": symbol},
        risks={"watchlist": list(brief.risk_watchlist)},
        action_items={"next_actions": list(brief.next_actions)},
    )

    # 2) quant_strategist prework
    top = candidates[0] if candidates else {"symbol": symbol, "score": 0.0, "snapshot": snapshot, "features": features}
    default_target = _as_float(((rules_raw.get("governance") or {}).get("default_target_position_pct")), default=10.0)
    max_pos = float(rules.risk.max_position_pct_per_symbol)
    quote_ccy = _quote_currency(str(top.get("symbol") or symbol))
    cash = float(repo.fetch_cash_balance(currency=quote_ccy))
    top_snapshot = (top.get("snapshot") or {}) if isinstance(top.get("snapshot"), Mapping) else {}
    top_mid = _as_float(top_snapshot.get("mid_price"), default=0.0)
    top_pos = repo.fetch_position(str(top.get("symbol") or symbol))
    top_qty = float(top_pos.qty) if top_pos else 0.0
    equity = float(cash) + float(top_qty) * float(top_mid)
    capital_profile = resolve_capital_policy(
        rules_raw=rules_raw,
        equity_krw=equity,
        default_target_position_pct=default_target,
        max_position_pct_per_symbol=max_pos,
        cooldown_minutes_after_trigger=int(rules.risk.cooldown_minutes_after_trigger),
    )
    target = min(
        float(capital_profile.max_target_position_pct),
        float(capital_profile.max_position_pct_per_symbol),
        max(0.0, default_target if float(top.get("score") or 0.0) > 0 else 0.0),
    )
    quant_summary = (
        f"후보 1순위 {top.get('symbol')} (score={float(top.get('score') or 0.0):.3f}), "
        f"권장 목표비중 {target:.1f}% (tier={capital_profile.tier_name}, equity={equity:.0f} KRW)"
    )
    quant_risks: list[str] = []
    if _as_float((top.get("snapshot") or {}).get("spread_bps"), default=0.0) > float(rules.cost_guard.max_spread_bps_entry):
        quant_risks.append("상위 후보의 스프레드가 제한보다 넓음")
    quant_id = _store_report(
        repo=repo,
        report_date_kst=now_kst,
        cycle_key=cycle_key,
        meeting_context=meeting_context,
        agent_name="quant_strategist",
        team_scope="STRATEGY",
        title="사전업무 리포트(Quant)",
        summary=quant_summary,
        findings={
            "candidates": candidates[:5],
            "suggested_plan": {"symbol": top.get("symbol"), "target_position_pct": target},
            "capital_profile": capital_profile.as_dict(),
        },
        risks={"watchlist": quant_risks},
        action_items={
            "next_actions": [
                "회의에서 상위 후보의 비용/리스크 충돌 여부 검증",
                "target_position_pct와 cooldown/rebalance_band 합의",
                "자본 티어(capital_policy) 상한과 플랜 비중 일치 여부 확인",
            ]
        },
    )

    # 3) risk_manager prework
    latest_pnl = (repo.fetch_pnl_daily(limit=1) or [None])[0]
    risk_watch: list[str] = []
    if pause.get("paused"):
        risk_watch.append("현재 PAUSE 상태")
    if str((recon or {}).get("status") or "OK").upper() == "FAIL":
        risk_watch.append("정합성 FAIL 상태")
    risk_summary = (
        f"리스크 한도: 일손실 {rules.risk.max_daily_loss_pct:.2f}%, "
        f"심볼 최대비중 {rules.risk.max_position_pct_per_symbol:.1f}%"
    )
    if latest_pnl:
        risk_summary += f", 최근 일손익={latest_pnl.get('realized_pnl')}"
    risk_id = _store_report(
        repo=repo,
        report_date_kst=now_kst,
        cycle_key=cycle_key,
        meeting_context=meeting_context,
        agent_name="risk_manager",
        team_scope="RISK",
        title="사전업무 리포트(Risk)",
        summary=risk_summary,
        findings={"limits": {"max_daily_loss_pct": rules.risk.max_daily_loss_pct, "max_position_pct_per_symbol": rules.risk.max_position_pct_per_symbol}},
        risks={"watchlist": risk_watch},
        action_items={"next_actions": ["회의에서 하드게이트(veto 조건) 재확인"]},
    )

    # 4) ops_manager prework
    deliveries = repo.fetch_notification_deliveries(limit=30)
    failed_n = len([d for d in deliveries if str(d.get("status") or "").upper() == "FAILED"])
    ops_summary = (
        f"운영상태 recon={str((recon or {}).get('status') or 'OK').upper()}, "
        f"paused={bool(pause.get('paused'))}, 최근 알림 실패={failed_n}건"
    )
    ops_risks: list[str] = []
    if failed_n > 0:
        ops_risks.append("최근 알림 전송 실패 존재")
    if bool(pause.get("paused")):
        ops_risks.append("시스템 PAUSE 상태")
    ops_id = _store_report(
        repo=repo,
        report_date_kst=now_kst,
        cycle_key=cycle_key,
        meeting_context=meeting_context,
        agent_name="ops_manager",
        team_scope="OPS",
        title="사전업무 리포트(Ops)",
        summary=ops_summary,
        findings={"reconciliation": recon, "pause": pause, "notification_failures_recent": failed_n},
        risks={"watchlist": ops_risks},
        action_items={"next_actions": ["회의 전 recon/pause 상태 재검증", "알림 실패 원인 점검"]},
    )

    return WorkCycleResult(
        cycle_key=cycle_key,
        report_ids={
            "research_agent": str(research_id),
            "quant_strategist": str(quant_id),
            "risk_manager": str(risk_id),
            "ops_manager": str(ops_id),
        },
    )
