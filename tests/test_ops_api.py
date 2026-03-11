from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import app.ops_api as ops_api


class _FakeRepo:
    _CUTOVER = datetime(2026, 3, 11, 1, 30, tzinfo=timezone.utc)

    def fetch_pause_state(self):
        return {"paused": False, "latest": None}

    def fetch_latest_reconciliation(self, symbol: str | None = None):
        return {"symbol": symbol or "KRW-BTC", "status": "OK"}

    def fetch_latest_decision(self, *, judge_type: str = "SAFE"):
        return {"symbol": "KRW-BTC", "action": "HOLD", "selected_reasons": ["RG_EDGE_TOO_LOW"], "gates": {}, "judge_type": judge_type}

    def fetch_portfolio_overview(self, *, quote_currency: str = "KRW"):
        return {"quote_currency": quote_currency, "cash_krw": 50000.0, "equity_krw": 50000.0}

    def fetch_pnl_daily(self, *, limit: int = 1):
        return [{"day": "2026-03-11", "realized_pnl": 0.0, "fees_paid": 0.0, "trades_count": 0, "max_drawdown": 0.0}][:limit]

    def fetch_realized_trades(self, *, limit: int = 10):
        return [{"symbol": "KRW-BTC", "realized_pnl": 100.0}][:limit]

    def fetch_decisions(self, *, judge_type: str | None = None, limit: int = 200):
        return [
            {
                "symbol": "KRW-BTC",
                "action": "HOLD",
                "selected_reasons": ["RG_EDGE_TOO_LOW"],
                "gates": {"reconciliation_status": "OK"},
                "judge_type": judge_type or "SAFE",
            }
        ][:limit]

    def fetch_pause_state_at(self, *, ts_at: datetime):
        if ts_at < self._CUTOVER:
            return {"paused": True, "latest": {"reason_type": "RECON_FAIL"}}
        return {"paused": False, "latest": {"reason_type": "RECON_FAIL", "ts_resume": datetime(2026, 3, 11, 1, 40, tzinfo=timezone.utc)}}

    def fetch_latest_reconciliation_before(self, *, ts_at: datetime, symbol: str | None = None):
        return {"symbol": symbol or "KRW-BTC", "status": "FAIL" if ts_at < self._CUTOVER else "OK"}

    def fetch_latest_decision_before(self, *, ts_at: datetime, judge_type: str = "SAFE", symbol: str | None = None):
        if ts_at < self._CUTOVER:
            return {
                "symbol": symbol or "KRW-BTC",
                "action": "HOLD",
                "selected_reasons": ["RG_EDGE_TOO_LOW"],
                "gates": {"runtime_buy_enabled": False, "current_position_pct": 0.0},
                "judge_type": judge_type,
            }
        return {
            "symbol": symbol or "KRW-BTC",
            "action": "BUY",
            "selected_reasons": ["RG_EDGE_OK"],
            "gates": {"runtime_buy_enabled": True, "current_position_pct": 25.0},
            "judge_type": judge_type,
        }

    def fetch_latest_trade_plan_before(self, *, ts_at: datetime, prefer_active: bool = True):
        return {
            "event_id": "plan-before" if ts_at < self._CUTOVER else "plan-after",
            "ts": ts_at,
            "symbol": "KRW-BTC",
            "action": "HOLD" if ts_at < self._CUTOVER else "BUY",
            "target_position_pct": 0.0 if ts_at < self._CUTOVER else 25.0,
        }

    def fetch_portfolio_overview_at(self, *, ts_at: datetime, quote_currency: str = "KRW"):
        if ts_at < self._CUTOVER:
            return {"quote_currency": quote_currency, "cash_krw": 50000.0, "equity_krw": 50000.0, "positions": []}
        return {
            "quote_currency": quote_currency,
            "cash_krw": 37500.0,
            "equity_krw": 50500.0,
            "positions": [{"symbol": "KRW-BTC", "qty": 0.0004, "value_krw": 13000.0}],
        }

    def fetch_realized_trades_before(self, *, ts_at: datetime, symbol: str | None = None, limit: int = 2000):
        rows = [{"symbol": symbol or "KRW-BTC", "ts_close": datetime(2026, 3, 11, 1, 10, tzinfo=timezone.utc), "realized_pnl": -900.0, "fees_total": 30.0}]
        return [row for row in rows if row["ts_close"] <= ts_at][:limit]

    def fetch_latest_event_before(
        self,
        *,
        event_type: str,
        ts_at: datetime,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ):
        if ts_at < self._CUTOVER:
            payload = {
                "ts_utc": "2026-03-11T01:00:00+00:00",
                "stopping": False,
                "workers": {
                    "paper_loop": {"alive": True, "restarts": 0},
                    "ops_work_loop": {"alive": False, "restarts": 1},
                },
            }
        else:
            payload = {
                "ts_utc": "2026-03-11T02:00:00+00:00",
                "stopping": False,
                "workers": {
                    "paper_loop": {"alive": True, "restarts": 0},
                    "ops_work_loop": {"alive": True, "restarts": 2},
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


def _status_file(tmp_path: Path) -> Path:
    path = tmp_path / "orchestrator_status.json"
    path.write_text(
        json.dumps(
            {
                "ts_utc": "2026-03-11T01:00:00+00:00",
                "workers": {"paper_loop": {"alive": True, "restarts": 0}},
                "stopping": False,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_ops_status_endpoint_returns_snapshot(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ops_api, "PostgresRepo", _FakeRepo)
    monkeypatch.setenv("ORCHESTRATOR_STATUS_PATH", str(_status_file(tmp_path)))

    with TestClient(ops_api.app) as client:
        resp = client.get("/api/v1/ops/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["orchestrator"]["running"] is True
    assert body["data"]["portfolio"]["cash_krw"] == 50000.0


def test_why_no_trade_endpoint_returns_reason(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ops_api, "PostgresRepo", _FakeRepo)
    monkeypatch.setenv("ORCHESTRATOR_STATUS_PATH", str(_status_file(tmp_path)))

    with TestClient(ops_api.app) as client:
        resp = client.get("/api/v1/ops/why-no-trade?symbol=KRW-BTC")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["blocked"] is True
    assert body["data"]["selected_reasons"] == ["RG_EDGE_TOO_LOW"]


def test_ops_api_key_is_optional_until_configured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ops_api, "PostgresRepo", _FakeRepo)
    monkeypatch.setenv("ORCHESTRATOR_STATUS_PATH", str(_status_file(tmp_path)))
    monkeypatch.delenv("OPS_READ_API_KEY", raising=False)

    with TestClient(ops_api.app) as client:
        resp = client.get("/healthz")

    assert resp.status_code == 200


def test_ops_api_key_blocks_when_configured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ops_api, "PostgresRepo", _FakeRepo)
    monkeypatch.setenv("ORCHESTRATOR_STATUS_PATH", str(_status_file(tmp_path)))
    monkeypatch.setenv("OPS_READ_API_KEY", "secret")

    with TestClient(ops_api.app) as client:
        denied = client.get("/healthz")
        allowed = client.get("/healthz", headers={"x-ops-api-key": "secret"})

    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_state_at_endpoint_returns_historical_state(monkeypatch) -> None:
    monkeypatch.setattr(ops_api, "PostgresRepo", _FakeRepo)

    with TestClient(ops_api.app) as client:
        resp = client.get("/api/v1/ops/state-at?ts=2026-03-11T01:00:00Z&symbol=KRW-BTC")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["pause_state"]["paused"] is True
    assert body["data"]["latest_safe_decision"]["action"] == "HOLD"
    assert body["data"]["orchestrator"]["dead_workers"] == ["ops_work_loop"]


def test_compare_endpoint_returns_changes(monkeypatch) -> None:
    monkeypatch.setattr(ops_api, "PostgresRepo", _FakeRepo)

    with TestClient(ops_api.app) as client:
        resp = client.get(
            "/api/v1/ops/compare?from_ts=2026-03-11T01:00:00Z&to_ts=2026-03-11T02:00:00Z&symbol=KRW-BTC"
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    fields = {row["field"] for row in body["data"]["changes"]}
    assert "paused" in fields
    assert "action" in fields
    assert "orchestrator_dead_workers" in fields
