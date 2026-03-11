from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ai_invest.ops.read_api import (
    build_no_trade_snapshot,
    build_ops_status_snapshot,
    build_pause_explanation,
    build_pnl_snapshot,
    build_state_at,
    build_state_compare,
)


class _FakeRepo:
    _CUTOVER = datetime(2026, 3, 11, 1, 30, tzinfo=timezone.utc)

    def fetch_pause_state(self):
        return {
            "paused": True,
            "latest": {
                "reason_type": "RECON_FAIL",
                "severity": "CRITICAL",
            },
        }

    def fetch_latest_reconciliation(self, symbol: str | None = None):
        return {
            "symbol": symbol or "KRW-BTC",
            "status": "FAIL",
            "diff_summary": "position mismatch",
        }

    def fetch_latest_decision(self, *, judge_type: str = "SAFE"):
        return {
            "symbol": "KRW-BTC",
            "action": "PAUSE",
            "selected_reasons": ["RG_RECON_FAIL"],
            "gates": {"reconciliation_status": "FAIL"},
            "judge_type": judge_type,
        }

    def fetch_portfolio_overview(self, *, quote_currency: str = "KRW"):
        return {"quote_currency": quote_currency, "cash_krw": 50000.0, "equity_krw": 50000.0}

    def fetch_pnl_daily(self, *, limit: int = 1):
        return [{"day": "2026-03-11", "realized_pnl": -1200.0, "fees_paid": 50.0, "trades_count": 2, "max_drawdown": 1.2}][:limit]

    def fetch_realized_trades(self, *, limit: int = 10):
        return [
            {"symbol": "KRW-BTC", "realized_pnl": -1200.0, "pnl_bps": -24.0},
            {"symbol": "KRW-BTC", "realized_pnl": 800.0, "pnl_bps": 16.0},
        ][:limit]

    def fetch_decisions(self, *, judge_type: str | None = None, limit: int = 200):
        return [
            {
                "symbol": "KRW-BTC",
                "action": "HOLD",
                "selected_reasons": ["RG_NEWS_RISK"],
                "gates": {
                    "runtime_buy_enabled": False,
                    "runtime_reason_codes": ["RG_NEWS_RISK"],
                    "reconciliation_status": "OK",
                },
                "judge_type": judge_type or "SAFE",
            }
        ][:limit]

    def fetch_pause_state_at(self, *, ts_at: datetime):
        if ts_at < self._CUTOVER:
            return {
                "paused": True,
                "latest": {
                    "reason_type": "RECON_FAIL",
                    "severity": "CRITICAL",
                    "ts_pause": datetime(2026, 3, 11, 0, 55, tzinfo=timezone.utc),
                    "ts_resume": None,
                },
            }
        return {
            "paused": False,
            "latest": {
                "reason_type": "RECON_FAIL",
                "severity": "CRITICAL",
                "ts_pause": datetime(2026, 3, 11, 0, 55, tzinfo=timezone.utc),
                "ts_resume": datetime(2026, 3, 11, 1, 40, tzinfo=timezone.utc),
            },
        }

    def fetch_latest_reconciliation_before(self, *, ts_at: datetime, symbol: str | None = None):
        return {
            "symbol": symbol or "KRW-BTC",
            "status": "FAIL" if ts_at < self._CUTOVER else "OK",
            "diff_summary": "position mismatch" if ts_at < self._CUTOVER else None,
        }

    def fetch_latest_decision_before(self, *, ts_at: datetime, judge_type: str = "SAFE", symbol: str | None = None):
        if ts_at < self._CUTOVER:
            return {
                "symbol": symbol or "KRW-BTC",
                "action": "HOLD",
                "selected_reasons": ["RG_NEWS_RISK", "RG_RECON_FAIL"],
                "gates": {
                    "runtime_buy_enabled": False,
                    "runtime_reason_codes": ["RG_NEWS_RISK"],
                    "reconciliation_status": "FAIL",
                    "effective_target_pct": 10.0,
                    "current_position_pct": 0.0,
                },
                "judge_type": judge_type,
            }
        return {
            "symbol": symbol or "KRW-BTC",
            "action": "BUY",
            "selected_reasons": ["RG_EDGE_OK"],
            "gates": {
                "runtime_buy_enabled": True,
                "runtime_reason_codes": [],
                "reconciliation_status": "OK",
                "effective_target_pct": 24.0,
                "current_position_pct": 24.7,
            },
            "judge_type": judge_type,
        }

    def fetch_latest_trade_plan_before(self, *, ts_at: datetime, prefer_active: bool = True):
        if ts_at < self._CUTOVER:
            return {
                "event_id": "plan-before",
                "ts": datetime(2026, 3, 11, 0, 45, tzinfo=timezone.utc),
                "symbol": "KRW-BTC",
                "action": "HOLD",
                "target_position_pct": 0.0,
                "status": "ACTIVE",
            }
        return {
            "event_id": "plan-after",
            "ts": datetime(2026, 3, 11, 1, 45, tzinfo=timezone.utc),
            "symbol": "KRW-BTC",
            "action": "BUY",
            "target_position_pct": 24.0,
            "status": "ACTIVE",
        }

    def fetch_portfolio_overview_at(self, *, ts_at: datetime, quote_currency: str = "KRW"):
        if ts_at < self._CUTOVER:
            return {
                "quote_currency": quote_currency,
                "cash_krw": 50000.0,
                "position_value_krw": 0.0,
                "equity_krw": 50000.0,
                "positions_count": 0,
                "positions": [],
            }
        return {
            "quote_currency": quote_currency,
            "cash_krw": 38000.0,
            "position_value_krw": 12500.0,
            "equity_krw": 50500.0,
            "positions_count": 1,
            "positions": [
                {
                    "symbol": "KRW-BTC",
                    "qty": 0.0004,
                    "avg_entry_price": 31250000.0,
                    "value_krw": 12500.0,
                    "mark_price": 31250000.0,
                    "mid_price": 31250000.0,
                    "unrealized_pnl_krw": 0.0,
                }
            ],
        }

    def fetch_realized_trades_before(self, *, ts_at: datetime, symbol: str | None = None, limit: int = 2000):
        rows = [
            {
                "symbol": symbol or "KRW-BTC",
                "ts_close": datetime(2026, 3, 11, 1, 10, tzinfo=timezone.utc),
                "realized_pnl": -1200.0,
                "fees_total": 50.0,
            },
            {
                "symbol": symbol or "KRW-BTC",
                "ts_close": datetime(2026, 3, 11, 1, 50, tzinfo=timezone.utc),
                "realized_pnl": 800.0,
                "fees_total": 40.0,
            },
        ]
        return [row for row in rows if row["ts_close"] <= ts_at][:limit]

    def fetch_latest_event_before(
        self,
        *,
        event_type: str,
        ts_at: datetime,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ):
        assert event_type == "ORCHESTRATOR_STATUS"
        assert entity_type == "orchestrator"
        assert entity_id == "multi_orchestrator"
        if ts_at < self._CUTOVER:
            payload = {
                "ts_utc": "2026-03-11T01:00:00+00:00",
                "stopping": False,
                "workers": {
                    "paper_loop": {"alive": True, "restarts": 0},
                    "ops_work_loop": {"alive": False, "restarts": 2},
                },
            }
        else:
            payload = {
                "ts_utc": "2026-03-11T02:00:00+00:00",
                "stopping": False,
                "workers": {
                    "paper_loop": {"alive": True, "restarts": 0},
                    "ops_work_loop": {"alive": True, "restarts": 3},
                },
            }
        return {
            "event_id": "orch-status",
            "ts": ts_at,
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload,
        }


def _write_status(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "ts_utc": "2026-03-11T01:00:00+00:00",
                "stopping": False,
                "workers": {
                    "paper_loop": {"alive": True, "restarts": 1},
                    "ops_work_loop": {"alive": False, "restarts": 3},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_build_ops_status_snapshot_includes_orchestrator_and_portfolio(tmp_path: Path) -> None:
    status_path = _write_status(tmp_path / "orchestrator_status.json")
    out = build_ops_status_snapshot(repo=_FakeRepo(), status_path=status_path)

    assert out["orchestrator"]["running"] is True
    assert "paper_loop" in out["orchestrator"]["alive_workers"]
    assert out["portfolio"]["cash_krw"] == 50000.0
    assert out["pause_state"]["paused"] is True


def test_build_pause_explanation_collects_pause_and_recon_reasons(tmp_path: Path) -> None:
    status_path = _write_status(tmp_path / "orchestrator_status.json")
    out = build_pause_explanation(repo=_FakeRepo(), status_path=status_path)

    assert out["paused"] is True
    assert any("pause_log active" in reason for reason in out["reasons"])
    assert any("reconciliation failed" in reason for reason in out["reasons"])


def test_build_pnl_snapshot_returns_latest_day_and_trades() -> None:
    out = build_pnl_snapshot(repo=_FakeRepo(), trade_limit=2)

    assert out["latest_day"]["day"] == "2026-03-11"
    assert len(out["recent_trades"]) == 2


def test_build_no_trade_snapshot_uses_latest_safe_decision_for_symbol(tmp_path: Path) -> None:
    status_path = _write_status(tmp_path / "orchestrator_status.json")
    out = build_no_trade_snapshot(repo=_FakeRepo(), symbol="KRW-BTC", status_path=status_path)

    assert out["symbol"] == "KRW-BTC"
    assert out["blocked"] is True
    assert out["selected_reasons"] == ["RG_NEWS_RISK"]


def test_build_state_at_reconstructs_historical_state() -> None:
    repo = _FakeRepo()
    out = build_state_at(
        repo=repo,
        ts_at=datetime(2026, 3, 11, 1, 0, tzinfo=timezone.utc),
        symbol="KRW-BTC",
    )

    assert out["pause_state"]["paused"] is True
    assert out["reconciliation_status"] == "FAIL"
    assert out["latest_safe_decision"]["action"] == "HOLD"
    assert out["trade_plan"]["action"] == "HOLD"
    assert out["portfolio"]["cash_krw"] == 50000.0
    assert out["orchestrator"]["dead_workers"] == ["ops_work_loop"]


def test_build_state_compare_detects_material_changes() -> None:
    repo = _FakeRepo()
    out = build_state_compare(
        repo=repo,
        from_ts=datetime(2026, 3, 11, 1, 0, tzinfo=timezone.utc),
        to_ts=datetime(2026, 3, 11, 2, 0, tzinfo=timezone.utc),
        symbol="KRW-BTC",
    )

    fields = {row["field"] for row in out["changes"]}
    assert "paused" in fields
    assert "reconciliation_status" in fields
    assert "action" in fields
    assert "cash_krw" in fields
    assert "orchestrator_dead_workers" in fields
