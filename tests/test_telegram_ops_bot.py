from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_invest.notifications.telegram_ops_bot import build_ops_bot_reply, parse_telegram_command


class _FakeRepo:
    def fetch_pause_state(self):
        return {"paused": False, "latest": None}

    def fetch_latest_reconciliation(self, symbol: str | None = None):
        return {"symbol": symbol or "KRW-BTC", "status": "OK"}

    def fetch_latest_decision(self, *, judge_type: str = "SAFE"):
        return {
            "symbol": "KRW-BTC",
            "action": "BUY",
            "selected_reasons": ["RG_EDGE_OK"],
            "gates": {},
            "judge_type": judge_type,
        }

    def fetch_portfolio_overview(self, *, quote_currency: str = "KRW"):
        return {"quote_currency": quote_currency, "cash_krw": 38000.0, "equity_krw": 50500.0}

    def fetch_pnl_daily(self, *, limit: int = 1):
        return [{"day": "2026-03-11", "realized_pnl": 800.0, "fees_paid": 40.0, "trades_count": 1}][:limit]

    def fetch_realized_trades(self, *, limit: int = 10):
        return [{"symbol": "KRW-BTC", "realized_pnl": 800.0}][:limit]

    def fetch_decisions(self, *, judge_type: str | None = None, limit: int = 200):
        return [
            {
                "symbol": "KRW-BTC",
                "action": "HOLD",
                "selected_reasons": ["RG_NEWS_RISK"],
                "gates": {
                    "runtime_buy_enabled": False,
                    "effective_target_pct": 10.0,
                    "reconciliation_status": "OK",
                },
                "judge_type": judge_type or "SAFE",
            }
        ][:limit]

    def fetch_pause_state_at(self, *, ts_at: datetime):
        return {"paused": False, "latest": None}

    def fetch_latest_reconciliation_before(self, *, ts_at: datetime, symbol: str | None = None):
        return {"symbol": symbol or "KRW-BTC", "status": "OK"}

    def fetch_latest_decision_before(self, *, ts_at: datetime, judge_type: str = "SAFE", symbol: str | None = None):
        return {
            "symbol": symbol or "KRW-BTC",
            "action": "BUY",
            "selected_reasons": ["RG_EDGE_OK"],
            "gates": {"current_position_pct": 25.0, "effective_target_pct": 25.0},
            "judge_type": judge_type,
        }

    def fetch_latest_trade_plan_before(self, *, ts_at: datetime, prefer_active: bool = True):
        return {"symbol": "KRW-BTC", "action": "BUY", "target_position_pct": 25.0}

    def fetch_portfolio_overview_at(self, *, ts_at: datetime, quote_currency: str = "KRW"):
        return {
            "quote_currency": quote_currency,
            "cash_krw": 38000.0,
            "equity_krw": 50500.0,
            "positions": [{"symbol": "KRW-BTC", "qty": 0.0004, "value_krw": 12500.0}],
        }

    def fetch_realized_trades_before(self, *, ts_at: datetime, symbol: str | None = None, limit: int = 2000):
        return [
            {
                "symbol": symbol or "KRW-BTC",
                "ts_close": datetime(2026, 3, 11, 1, 10, tzinfo=timezone.utc),
                "realized_pnl": 800.0,
                "fees_total": 40.0,
            }
        ][:limit]

    def fetch_latest_event_before(
        self,
        *,
        event_type: str,
        ts_at: datetime,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ):
        return {
            "event_id": "orch",
            "ts": ts_at,
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": {
                "ts_utc": ts_at.isoformat(),
                "stopping": False,
                "workers": {
                    "paper_loop": {"alive": True, "restarts": 0},
                    "ops_work_loop": {"alive": True, "restarts": 1},
                },
            },
        }


def _status_file(tmp_path: Path) -> Path:
    path = tmp_path / "orchestrator_status.json"
    path.write_text(
        json.dumps(
            {
                "ts_utc": "2026-03-11T02:00:00+00:00",
                "stopping": False,
                "workers": {
                    "paper_loop": {"alive": True, "restarts": 0},
                    "ops_work_loop": {"alive": True, "restarts": 1},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_parse_telegram_command_normalizes_bot_mentions() -> None:
    cmd = parse_telegram_command("/status@mybot")

    assert cmd.name == "status"
    assert cmd.args == ()


def test_build_ops_bot_reply_formats_status(tmp_path: Path) -> None:
    reply = build_ops_bot_reply(repo=_FakeRepo(), status_path=_status_file(tmp_path), text="/status")

    assert "ops status" in reply
    assert "workers up: ops_work_loop, paper_loop" in reply
    assert "equity: 50,500 KRW" in reply


def test_build_ops_bot_reply_formats_historical_state(tmp_path: Path) -> None:
    reply = build_ops_bot_reply(
        repo=_FakeRepo(),
        status_path=_status_file(tmp_path),
        text="/state_at 2026-03-11T02:00:00Z KRW-BTC",
    )

    assert "state at" in reply
    assert "workers down: none" in reply
    assert "plan: BUY / 25.00%" in reply


def test_build_ops_bot_reply_rejects_invalid_compare(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_ops_bot_reply(
            repo=_FakeRepo(),
            status_path=_status_file(tmp_path),
            text="/compare 2026-03-11T03:00:00Z 2026-03-11T02:00:00Z KRW-BTC",
        )
