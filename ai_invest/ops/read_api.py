from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ai_invest.storage.postgres import PostgresRepo


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
    workers = status.get("workers") if isinstance(status, Mapping) else {}
    workers_map = workers if isinstance(workers, Mapping) else {}
    alive_workers: list[str] = []
    dead_workers: list[str] = []
    restart_counts: dict[str, int] = {}
    for name, raw in workers_map.items():
        state = raw if isinstance(raw, Mapping) else {}
        if bool(state.get("alive")):
            alive_workers.append(str(name))
        else:
            dead_workers.append(str(name))
        restart_counts[str(name)] = int(state.get("restarts") or 0)
    summary = {
        "status_file": str(status_path),
        "exists": bool(status is not None),
        "running": bool(alive_workers),
        "ts_utc": status.get("ts_utc") if isinstance(status, Mapping) else None,
        "stopping": bool(status.get("stopping")) if isinstance(status, Mapping) else False,
        "alive_workers": alive_workers,
        "dead_workers": dead_workers,
        "worker_count": len(workers_map),
        "restart_counts": restart_counts,
        "workers": dict(workers_map),
    }
    return summary, errors


def _safe_call(label: str, fn, *args, **kwargs) -> tuple[Any, dict[str, Any] | None]:
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        return None, _error(label, exc)


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
