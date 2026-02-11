from __future__ import annotations

import uuid
import unittest
from typing import Any
from unittest.mock import patch

from ai_invest.notifications.service import NotificationContext, NotificationService


class _FakeRepo:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def insert_notification_delivery(self, **kwargs: Any) -> None:
        self.rows.append(dict(kwargs))

    def was_notification_sent_recently(self, *, dedupe_key: str, within_sec: int = 60) -> bool:  # noqa: ARG002
        return False


class NotificationServiceTests(unittest.TestCase):
    def test_notify_trade_plan_set_records_pending_when_send_disabled(self) -> None:
        repo = _FakeRepo()
        svc = NotificationService(
            repo,  # type: ignore[arg-type]
            ctx=NotificationContext(
                send_telegram=False,
                notify_safe_hold=True,
                notify_safe_change_only=True,
                dedupe_within_sec=60,
            ),
        )

        with patch("ai_invest.notifications.service.telegram_client.chat_id_meeting", return_value="-100123"):
            svc.notify_trade_plan_set(
                event_id=uuid.uuid4(),
                meeting_id="m-1",
                slot_key="2026-02-11 08:00",
                symbol="KRW-BTC",
                target_position_pct=10.0,
                valid_from_kst="2026-02-11T08:00:00+09:00",
                valid_to_kst="2026-02-11T16:00:00+09:00",
                allowed_actions={"buy": True, "sell": True},
                rebalance_band_pct=2.0,
                cooldown_minutes=30,
                constraints={"max_spread_bps": 8.0, "max_slippage_bps": 10.0, "max_position_pct": 20.0},
                rationale_summary="요약",
            )

        self.assertEqual(len(repo.rows), 1)
        row = repo.rows[0]
        self.assertEqual(row.get("template_id"), "tpl_trade_plan_set")
        self.assertEqual(row.get("status"), "PENDING")
        payload = row.get("payload") or {}
        event_payload = payload.get("event") or {}
        self.assertEqual(event_payload.get("symbol"), "KRW-BTC")
        self.assertEqual(event_payload.get("slot_key"), "2026-02-11 08:00")

    def test_notify_safe_decision_change_only_skips_unchanged_state(self) -> None:
        repo = _FakeRepo()
        svc = NotificationService(
            repo,  # type: ignore[arg-type]
            ctx=NotificationContext(
                send_telegram=False,
                notify_safe_hold=True,
                notify_safe_change_only=True,
                dedupe_within_sec=60,
            ),
        )

        with patch("ai_invest.notifications.service.telegram_client.chat_id_trading", return_value="-100123"):
            svc.notify_safe_decision(
                event_id=uuid.uuid4(),
                symbol="KRW-BTC",
                action="SELL",
                reasons=["RG_PASS"],
                run_id=uuid.uuid4(),
                context={
                    "market_signal": "SELL",
                    "regime_trade_allowed": True,
                    "risk_veto": False,
                    "ops_veto": False,
                    "reconciliation_status": "OK",
                    "pause_state": False,
                    "trade_plan_slot_key": "2026-02-11 16:00",
                    "trade_plan_target_pct": 10.0,
                },
            )
            svc.notify_safe_decision(
                event_id=uuid.uuid4(),
                symbol="KRW-BTC",
                action="SELL",
                reasons=["RG_PASS"],
                run_id=uuid.uuid4(),
                context={
                    "market_signal": "SELL",
                    "regime_trade_allowed": True,
                    "risk_veto": False,
                    "ops_veto": False,
                    "reconciliation_status": "OK",
                    "pause_state": False,
                    "trade_plan_slot_key": "2026-02-11 16:00",
                    "trade_plan_target_pct": 10.0,
                },
            )

        self.assertEqual(len(repo.rows), 2)
        self.assertEqual(repo.rows[0].get("status"), "PENDING")
        self.assertEqual(repo.rows[1].get("status"), "SKIPPED")
        self.assertIn("unchanged", str(repo.rows[1].get("last_error") or ""))

    def test_notify_safe_decision_change_only_sends_on_state_change(self) -> None:
        repo = _FakeRepo()
        svc = NotificationService(
            repo,  # type: ignore[arg-type]
            ctx=NotificationContext(
                send_telegram=False,
                notify_safe_hold=True,
                notify_safe_change_only=True,
                dedupe_within_sec=60,
            ),
        )

        with patch("ai_invest.notifications.service.telegram_client.chat_id_trading", return_value="-100123"):
            svc.notify_safe_decision(
                event_id=uuid.uuid4(),
                symbol="KRW-BTC",
                action="SELL",
                reasons=["RG_PASS"],
                run_id=uuid.uuid4(),
                context={"market_signal": "SELL", "trade_plan_slot_key": "2026-02-11 16:00"},
            )
            svc.notify_safe_decision(
                event_id=uuid.uuid4(),
                symbol="KRW-BTC",
                action="BUY",
                reasons=["RG_PASS"],
                run_id=uuid.uuid4(),
                context={"market_signal": "BUY", "trade_plan_slot_key": "2026-02-11 16:00"},
            )

        self.assertEqual(len(repo.rows), 2)
        self.assertEqual(repo.rows[0].get("status"), "PENDING")
        self.assertEqual(repo.rows[1].get("status"), "PENDING")


if __name__ == "__main__":
    unittest.main()
