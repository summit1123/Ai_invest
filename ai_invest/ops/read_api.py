from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from ai_invest.runtime.orchestrator_state import (
    ORCHESTRATOR_ENTITY_ID,
    ORCHESTRATOR_ENTITY_TYPE,
    ORCHESTRATOR_EVENT_TYPE,
    build_orchestrator_summary,
)
from ai_invest.storage.postgres import PostgresRepo


KST = timezone(timedelta(hours=9))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(label: str, exc: Exception) -> dict[str, Any]:
    return {"source": label, "message": str(exc)[:300]}


def _load_status_file(status_path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not status_path.exists():
        return None, None
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, _error("status_file", exc)
    if not isinstance(data, Mapping):
        return None, {"source": "status_file", "message": "status file did not contain an object"}
    return dict(data), None


def _orchestrator_summary(status_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    status, err = _load_status_file(status_path)
    errors: list[dict[str, Any]] = []
    if err is not None:
        errors.append(err)
    summary = build_orchestrator_summary(status, source=str(status_path), exists=bool(status is not None))
    summary["status_file"] = str(status_path)
    return summary, errors


def _safe_call(label: str, fn, *args, **kwargs) -> tuple[Any, dict[str, Any] | None]:
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        return None, _error(label, exc)


def _safe_repo_method(repo: PostgresRepo, method_name: str, *args, **kwargs) -> tuple[Any, dict[str, Any] | None]:
    fn = getattr(repo, method_name, None)
    if fn is None:
        return None, {"source": method_name, "message": f"{method_name} is not available on repo"}
    return _safe_call(method_name, fn, *args, **kwargs)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _historical_orchestrator_summary(
    *,
    repo: PostgresRepo,
    ts_at: datetime,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    fn = getattr(repo, "fetch_latest_event_before", None)
    if fn is None:
        return None, None
    event, err = _safe_call(
        "latest_orchestrator_status_before",
        fn,
        event_type=ORCHESTRATOR_EVENT_TYPE,
        ts_at=ts_at,
        entity_type=ORCHESTRATOR_ENTITY_TYPE,
        entity_id=ORCHESTRATOR_ENTITY_ID,
    )
    if err is not None:
        return None, err
    if not isinstance(event, Mapping):
        return None, None
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    summary = build_orchestrator_summary(payload, source="db_event", exists=True)
    summary["event_id"] = event.get("event_id")
    summary["event_ts"] = event.get("ts")
    return summary, None


def _decision_for_symbol(repo: PostgresRepo, *, symbol: str, judge_type: str = "SAFE") -> dict[str, Any] | None:
    symbol_norm = str(symbol or "").strip().upper()
    if not symbol_norm:
        return None
    decisions = repo.fetch_decisions(judge_type=judge_type, limit=200)
    for decision in decisions:
        if str(decision.get("symbol") or "").strip().upper() == symbol_norm:
            return decision
    return None


def _compact_gate_view(gates: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(gates, Mapping):
        return {}
    keep = [
        "pause_state",
        "reconciliation_status",
        "rate_limit_alert",
        "daily_loss_pct",
        "spread_bps",
        "spread_limit_bps_effective",
        "regime_trade_allowed",
        "risk_veto",
        "ops_veto",
        "market_expected_net_edge_bps",
        "market_min_edge_required_bps",
        "runtime_buy_enabled",
        "runtime_reason_codes",
        "current_position_pct",
        "effective_target_pct",
    ]
    return {key: gates.get(key) for key in keep if key in gates}


def build_ops_status_snapshot(*, repo: PostgresRepo, status_path: Path) -> dict[str, Any]:
    orchestrator, errors = _orchestrator_summary(status_path)
    pause_state, err = _safe_call("pause_state", repo.fetch_pause_state)
    if err:
        errors.append(err)
    latest_recon, err = _safe_call("latest_reconciliation", repo.fetch_latest_reconciliation)
    if err:
        errors.append(err)
    latest_safe, err = _safe_call("latest_safe_decision", repo.fetch_latest_decision, judge_type="SAFE")
    if err:
        errors.append(err)
    portfolio, err = _safe_call("portfolio_overview", repo.fetch_portfolio_overview, quote_currency="KRW")
    if err:
        errors.append(err)
    pnl_daily, err = _safe_call("pnl_daily", repo.fetch_pnl_daily, limit=1)
    if err:
        errors.append(err)

    return {
        "ts_utc": utc_now_iso(),
        "orchestrator": orchestrator,
        "pause_state": pause_state,
        "latest_reconciliation": latest_recon,
        "latest_safe_decision": latest_safe,
        "portfolio": portfolio,
        "pnl_today": (pnl_daily or [None])[0] if isinstance(pnl_daily, list) and pnl_daily else None,
        "errors": errors,
    }


def build_pause_explanation(*, repo: PostgresRepo, status_path: Path) -> dict[str, Any]:
    snapshot = build_ops_status_snapshot(repo=repo, status_path=status_path)
    pause_state = snapshot.get("pause_state") if isinstance(snapshot.get("pause_state"), Mapping) else {}
    latest_recon = snapshot.get("latest_reconciliation") if isinstance(snapshot.get("latest_reconciliation"), Mapping) else {}
    latest_safe = snapshot.get("latest_safe_decision") if isinstance(snapshot.get("latest_safe_decision"), Mapping) else {}
    orchestrator = snapshot.get("orchestrator") if isinstance(snapshot.get("orchestrator"), Mapping) else {}

    reasons: list[str] = []
    if bool(pause_state.get("paused")):
        latest = pause_state.get("latest") if isinstance(pause_state.get("latest"), Mapping) else {}
        reasons.append(f"pause_log active ({latest.get('reason_type') or 'unknown'})")
    if str(latest_recon.get("status") or "").upper() == "FAIL":
        reasons.append("latest reconciliation failed")
    if str(latest_safe.get("action") or "").upper() == "PAUSE":
        safe_reasons = list(latest_safe.get("selected_reasons") or [])
        if safe_reasons:
            reasons.append(f"latest safe decision paused trading ({', '.join(str(x) for x in safe_reasons[:3])})")
        else:
            reasons.append("latest safe decision paused trading")
    dead_workers = list(orchestrator.get("dead_workers") or [])
    if dead_workers:
        reasons.append(f"workers down: {', '.join(dead_workers[:5])}")
    stop_request = orchestrator.get("last_stop_request") if isinstance(orchestrator.get("last_stop_request"), Mapping) else {}
    if stop_request:
        reasons.append(
            "latest stop request: "
            f"{stop_request.get('source') or 'unknown'} / {stop_request.get('reason') or 'unspecified'}"
        )
    if not reasons:
        reasons.append("system is not currently paused")

    return {
        "ts_utc": utc_now_iso(),
        "paused": bool(pause_state.get("paused")),
        "summary": reasons[0],
        "reasons": reasons,
        "pause_state": pause_state,
        "latest_reconciliation": latest_recon,
        "latest_safe_decision": latest_safe,
        "orchestrator": orchestrator,
        "errors": list(snapshot.get("errors") or []),
    }


def build_pnl_snapshot(*, repo: PostgresRepo, trade_limit: int = 10) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    pnl_daily, err = _safe_call("pnl_daily", repo.fetch_pnl_daily, limit=1)
    if err:
        errors.append(err)
    trades, err = _safe_call("realized_trades", repo.fetch_realized_trades, limit=max(1, int(trade_limit)))
    if err:
        errors.append(err)
    portfolio, err = _safe_call("portfolio_overview", repo.fetch_portfolio_overview, quote_currency="KRW")
    if err:
        errors.append(err)

    latest_day = (pnl_daily or [None])[0] if isinstance(pnl_daily, list) and pnl_daily else None
    return {
        "ts_utc": utc_now_iso(),
        "latest_day": latest_day,
        "recent_trades": list(trades or []),
        "portfolio": portfolio,
        "errors": errors,
    }


def build_no_trade_snapshot(*, repo: PostgresRepo, symbol: str, status_path: Path) -> dict[str, Any]:
    symbol_norm = str(symbol or "").strip().upper()
    errors: list[dict[str, Any]] = []
    orchestrator, status_errors = _orchestrator_summary(status_path)
    errors.extend(status_errors)
    latest_safe, err = _safe_call("latest_safe_decision_for_symbol", _decision_for_symbol, repo, symbol=symbol_norm, judge_type="SAFE")
    if err:
        errors.append(err)
    latest_recon, err = _safe_call("latest_reconciliation", repo.fetch_latest_reconciliation, symbol=symbol_norm)
    if err:
        errors.append(err)
    pause_state, err = _safe_call("pause_state", repo.fetch_pause_state)
    if err:
        errors.append(err)

    action = str((latest_safe or {}).get("action") or "UNKNOWN").upper()
    selected_reasons = list((latest_safe or {}).get("selected_reasons") or [])
    blocked = action not in {"BUY", "SELL"}

    summary: str
    if latest_safe is None:
        summary = f"no recent SAFE decision found for {symbol_norm}"
    elif blocked and selected_reasons:
        summary = f"{symbol_norm} blocked by {', '.join(str(x) for x in selected_reasons[:3])}"
    elif blocked:
        summary = f"{symbol_norm} not trading because latest action is {action}"
    else:
        summary = f"{symbol_norm} latest action is {action}"

    return {
        "ts_utc": utc_now_iso(),
        "symbol": symbol_norm,
        "blocked": blocked,
        "summary": summary,
        "latest_safe_decision": latest_safe,
        "selected_reasons": selected_reasons,
        "gates": _compact_gate_view((latest_safe or {}).get("gates")),
        "pause_state": pause_state,
        "latest_reconciliation": latest_recon,
        "orchestrator": orchestrator,
        "errors": errors,
    }


def _compact_trade_plan(plan: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(plan, Mapping):
        return None
    keep = [
        "event_id",
        "ts",
        "symbol",
        "action",
        "target_position_pct",
        "valid_from",
        "valid_to",
        "valid_from_kst",
        "valid_to_kst",
        "status",
        "thesis",
    ]
    return {key: plan.get(key) for key in keep if key in plan}


def _position_for_symbol(portfolio: Mapping[str, Any] | None, symbol: str) -> dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    positions = portfolio.get("positions") if isinstance(portfolio, Mapping) else []
    for row in positions if isinstance(positions, list) else []:
        if str((row or {}).get("symbol") or "").strip().upper() == sym:
            out = dict(row)
            out["present"] = True
            return out
    return {
        "symbol": sym,
        "present": False,
        "qty": 0.0,
        "avg_entry_price": None,
        "entry_value_krw": 0.0,
        "mark_price": None,
        "mid_price": None,
        "value_krw": 0.0,
        "unrealized_pnl_krw": 0.0,
        "unrealized_pnl_pct": None,
        "pnl_direction": "FLAT",
        "ts_updated": None,
        "quote_ts": None,
    }


def _pnl_snapshot_until(*, ts_at: datetime, trades: list[dict[str, Any]] | None) -> dict[str, Any]:
    ts_utc = _as_utc(ts_at)
    target_day = ts_utc.astimezone(KST).date()
    realized = 0.0
    fees = 0.0
    count = 0
    for row in trades or []:
        close_ts = row.get("ts_close")
        if not isinstance(close_ts, datetime):
            continue
        close_utc = _as_utc(close_ts)
        if close_utc > ts_utc:
            continue
        if close_utc.astimezone(KST).date() != target_day:
            continue
        realized += float(row.get("realized_pnl") or 0.0)
        fees += float(row.get("fees_total") or 0.0)
        count += 1
    return {
        "day_kst": target_day.isoformat(),
        "realized_pnl_krw": float(realized),
        "fees_paid_krw": float(fees),
        "trades_count": int(count),
    }


def build_state_at(
    *,
    repo: PostgresRepo,
    ts_at: datetime,
    symbol: str = "KRW-BTC",
    quote_currency: str = "KRW",
) -> dict[str, Any]:
    ts_utc = _as_utc(ts_at)
    symbol_norm = str(symbol or "").strip().upper() or "KRW-BTC"
    errors: list[dict[str, Any]] = []

    orchestrator, err = _historical_orchestrator_summary(repo=repo, ts_at=ts_utc)
    if err:
        errors.append(err)
    pause_state, err = _safe_repo_method(repo, "fetch_pause_state_at", ts_at=ts_utc)
    if err:
        errors.append(err)
    latest_recon, err = _safe_repo_method(repo, "fetch_latest_reconciliation_before", ts_at=ts_utc, symbol=symbol_norm)
    if err:
        errors.append(err)
    latest_safe, err = _safe_repo_method(
        repo,
        "fetch_latest_decision_before",
        ts_at=ts_utc,
        judge_type="SAFE",
        symbol=symbol_norm,
    )
    if err:
        errors.append(err)
    trade_plan, err = _safe_repo_method(repo, "fetch_latest_trade_plan_before", ts_at=ts_utc, prefer_active=True)
    if err:
        errors.append(err)
    portfolio, err = _safe_repo_method(repo, "fetch_portfolio_overview_at", ts_at=ts_utc, quote_currency=quote_currency)
    if err:
        errors.append(err)
    trades, err = _safe_repo_method(repo, "fetch_realized_trades_before", ts_at=ts_utc, symbol=symbol_norm, limit=2000)
    if err:
        errors.append(err)

    position = _position_for_symbol(portfolio if isinstance(portfolio, Mapping) else {}, symbol_norm)
    pnl_today = _pnl_snapshot_until(ts_at=ts_utc, trades=list(trades or []))
    action = str((latest_safe or {}).get("action") or "UNKNOWN").upper()
    selected_reasons = list((latest_safe or {}).get("selected_reasons") or [])
    paused = bool((pause_state or {}).get("paused"))
    recon_status = str((latest_recon or {}).get("status") or "UNKNOWN").upper()
    blocked = action not in {"BUY", "SELL"}

    summary: str
    if paused:
        latest_pause = (pause_state or {}).get("latest") if isinstance((pause_state or {}).get("latest"), Mapping) else {}
        summary = f"trading paused at {ts_utc.isoformat()} ({latest_pause.get('reason_type') or 'unknown'})"
    elif latest_safe is None:
        summary = f"no SAFE decision found for {symbol_norm} before {ts_utc.isoformat()}"
    elif blocked and selected_reasons:
        summary = f"{symbol_norm} blocked at {ts_utc.isoformat()} by {', '.join(str(x) for x in selected_reasons[:3])}"
    elif blocked:
        summary = f"{symbol_norm} not trading at {ts_utc.isoformat()} because latest action was {action}"
    else:
        summary = f"{symbol_norm} latest action at {ts_utc.isoformat()} was {action}"

    return {
        "ts_utc": ts_utc.isoformat(),
        "symbol": symbol_norm,
        "summary": summary,
        "blocked": blocked,
        "orchestrator": orchestrator,
        "pause_state": pause_state,
        "latest_reconciliation": latest_recon,
        "reconciliation_status": recon_status,
        "latest_safe_decision": latest_safe,
        "selected_reasons": selected_reasons,
        "gates": _compact_gate_view((latest_safe or {}).get("gates")),
        "trade_plan": _compact_trade_plan(trade_plan if isinstance(trade_plan, Mapping) else None),
        "portfolio": portfolio,
        "position": position,
        "pnl_today": pnl_today,
        "reconstruction_limits": (
            ["portfolio is reconstructed from ledger_entries, fills, orders, and market_quotes"]
            if orchestrator
            else [
                "historical orchestrator worker state is not persisted in the database",
                "portfolio is reconstructed from ledger_entries, fills, orders, and market_quotes",
            ]
        ),
        "errors": errors,
    }


def _compare_scalar_changes(*, from_state: Mapping[str, Any], to_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []

    def add(field: str, before: Any, after: Any) -> None:
        if before == after:
            return
        changes.append({"field": field, "before": before, "after": after})

    add("paused", bool(((from_state.get("pause_state") or {}).get("paused"))), bool(((to_state.get("pause_state") or {}).get("paused"))))
    add(
        "pause_reason",
        (((from_state.get("pause_state") or {}).get("latest") or {}).get("reason_type")),
        (((to_state.get("pause_state") or {}).get("latest") or {}).get("reason_type")),
    )
    add("reconciliation_status", from_state.get("reconciliation_status"), to_state.get("reconciliation_status"))
    add(
        "action",
        ((from_state.get("latest_safe_decision") or {}).get("action")),
        ((to_state.get("latest_safe_decision") or {}).get("action")),
    )
    add("selected_reasons", list(from_state.get("selected_reasons") or []), list(to_state.get("selected_reasons") or []))
    add("runtime_buy_enabled", ((from_state.get("gates") or {}).get("runtime_buy_enabled")), ((to_state.get("gates") or {}).get("runtime_buy_enabled")))
    add(
        "runtime_reason_codes",
        list((from_state.get("gates") or {}).get("runtime_reason_codes") or []),
        list((to_state.get("gates") or {}).get("runtime_reason_codes") or []),
    )
    add("effective_target_pct", ((from_state.get("gates") or {}).get("effective_target_pct")), ((to_state.get("gates") or {}).get("effective_target_pct")))
    add("current_position_pct", ((from_state.get("gates") or {}).get("current_position_pct")), ((to_state.get("gates") or {}).get("current_position_pct")))
    add("cash_krw", ((from_state.get("portfolio") or {}).get("cash_krw")), ((to_state.get("portfolio") or {}).get("cash_krw")))
    add("equity_krw", ((from_state.get("portfolio") or {}).get("equity_krw")), ((to_state.get("portfolio") or {}).get("equity_krw")))
    add("position_qty", ((from_state.get("position") or {}).get("qty")), ((to_state.get("position") or {}).get("qty")))
    add("position_value_krw", ((from_state.get("position") or {}).get("value_krw")), ((to_state.get("position") or {}).get("value_krw")))
    add("realized_pnl_today_krw", ((from_state.get("pnl_today") or {}).get("realized_pnl_krw")), ((to_state.get("pnl_today") or {}).get("realized_pnl_krw")))
    add("trade_plan_symbol", ((from_state.get("trade_plan") or {}).get("symbol")), ((to_state.get("trade_plan") or {}).get("symbol")))
    add(
        "trade_plan_target_position_pct",
        ((from_state.get("trade_plan") or {}).get("target_position_pct")),
        ((to_state.get("trade_plan") or {}).get("target_position_pct")),
    )
    add("orchestrator_stopping", ((from_state.get("orchestrator") or {}).get("stopping")), ((to_state.get("orchestrator") or {}).get("stopping")))
    add(
        "orchestrator_alive_workers",
        list((from_state.get("orchestrator") or {}).get("alive_workers") or []),
        list((to_state.get("orchestrator") or {}).get("alive_workers") or []),
    )
    add(
        "orchestrator_dead_workers",
        list((from_state.get("orchestrator") or {}).get("dead_workers") or []),
        list((to_state.get("orchestrator") or {}).get("dead_workers") or []),
    )
    add(
        "orchestrator_restart_counts",
        dict((from_state.get("orchestrator") or {}).get("restart_counts") or {}),
        dict((to_state.get("orchestrator") or {}).get("restart_counts") or {}),
    )
    add(
        "orchestrator_last_stop_request",
        dict((from_state.get("orchestrator") or {}).get("last_stop_request") or {}),
        dict((to_state.get("orchestrator") or {}).get("last_stop_request") or {}),
    )
    return changes


def build_state_compare(
    *,
    repo: PostgresRepo,
    from_ts: datetime,
    to_ts: datetime,
    symbol: str = "KRW-BTC",
    quote_currency: str = "KRW",
) -> dict[str, Any]:
    from_state = build_state_at(repo=repo, ts_at=from_ts, symbol=symbol, quote_currency=quote_currency)
    to_state = build_state_at(repo=repo, ts_at=to_ts, symbol=symbol, quote_currency=quote_currency)
    changes = _compare_scalar_changes(from_state=from_state, to_state=to_state)
    summary = (
        "no material changes detected between the two timestamps"
        if not changes
        else f"{len(changes)} material changes detected between the two timestamps"
    )
    limits: list[str] = []
    for item in list(from_state.get("reconstruction_limits") or []) + list(to_state.get("reconstruction_limits") or []):
        if item not in limits:
            limits.append(str(item))
    return {
        "symbol": str(symbol or "").strip().upper() or "KRW-BTC",
        "from": from_state,
        "to": to_state,
        "summary": summary,
        "changes": changes,
        "reconstruction_limits": limits,
        "errors": list(from_state.get("errors") or []) + list(to_state.get("errors") or []),
    }
