from __future__ import annotations

import json
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_invest.notifications import telegram_ops_bot
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


def test_parse_telegram_command_maps_free_text_to_status() -> None:
    cmd = parse_telegram_command("잘 돌아가고 있나")

    assert cmd.name == "chat"
    assert cmd.args == ("잘 돌아가고 있나",)


def test_build_ops_bot_reply_formats_status(tmp_path: Path) -> None:
    reply = build_ops_bot_reply(repo=_FakeRepo(), status_path=_status_file(tmp_path), text="/status")

    assert "대표님, 현재 운용 상태 보고드립니다." in reply
    assert "정상 워커: ops_work_loop, paper_loop" in reply
    assert "추정 총자산: 50,500 KRW" in reply


def test_build_ops_bot_reply_formats_free_text_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        telegram_ops_bot,
        "llm_route_for_agent",
        lambda **kwargs: SimpleNamespace(
            enabled=False,
            model="gpt-5-mini",
            api_style="auto",
            reasoning_effort=None,
            temperature=0.2,
            timeout_sec=3600,
        ),
    )
    reply = build_ops_bot_reply(repo=_FakeRepo(), status_path=_status_file(tmp_path), text="잘 돌아가고 있나")

    assert "대표님, 현재 시스템은 정상 가동 중이며 매수 가능 신호를 감지한 상태입니다." in reply


def test_build_ops_bot_reply_uses_llm_for_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_llm_reply(*, repo, status_path, user_text):
        return f"대표님, LLM 응답입니다. 질문: {user_text}"

    monkeypatch.setattr(telegram_ops_bot, "_build_llm_ops_reply", _fake_llm_reply)

    reply = build_ops_bot_reply(repo=_FakeRepo(), status_path=_status_file(tmp_path), text="요즘 어때")

    assert reply == "대표님, LLM 응답입니다. 질문: 요즘 어때"


def test_build_ops_bot_reply_caps_llm_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    monkeypatch.setattr(
        telegram_ops_bot,
        "llm_route_for_agent",
        lambda **kwargs: SimpleNamespace(
            enabled=True,
            model="gpt-5-mini",
            api_style="auto",
            reasoning_effort=None,
            temperature=0.2,
            timeout_sec=3600,
        ),
    )

    def _fake_openai_generate_text(**kwargs):
        captured["timeout_sec"] = kwargs["timeout_sec"]
        return SimpleNamespace(text="대표님, LLM 응답입니다.")

    monkeypatch.setattr(telegram_ops_bot, "openai_generate_text", _fake_openai_generate_text)

    reply = build_ops_bot_reply(repo=_FakeRepo(), status_path=_status_file(tmp_path), text="요즘 어때")

    assert reply == "대표님, LLM 응답입니다."
    assert captured["timeout_sec"] == telegram_ops_bot.CHAT_LLM_TIMEOUT_CAP_SEC


def test_build_ops_bot_reply_formats_historical_state(tmp_path: Path) -> None:
    reply = build_ops_bot_reply(
        repo=_FakeRepo(),
        status_path=_status_file(tmp_path),
        text="/state_at 2026-03-11T02:00:00Z KRW-BTC",
    )

    assert "시점 상태 보고드립니다." in reply
    assert "중단 워커: 없음" in reply
    assert "당시 플랜: BUY / 25.00%" in reply


def test_build_ops_bot_reply_rejects_invalid_compare(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_ops_bot_reply(
            repo=_FakeRepo(),
            status_path=_status_file(tmp_path),
            text="/compare 2026-03-11T03:00:00Z 2026-03-11T02:00:00Z KRW-BTC",
        )
