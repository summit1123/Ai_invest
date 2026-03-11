from __future__ import annotations

import json
from pathlib import Path

from ai_invest.ops.read_api import (
    build_no_trade_snapshot,
    build_ops_status_snapshot,
    build_pause_explanation,
    build_pnl_snapshot,
)


class _FakeRepo:
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
