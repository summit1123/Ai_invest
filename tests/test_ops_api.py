from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.ops_api as ops_api


class _FakeRepo:
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
