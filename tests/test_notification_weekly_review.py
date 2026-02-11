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


class NotificationWeeklyReviewTests(unittest.TestCase):
    def test_notify_weekly_review_records_pending_when_send_disabled(self) -> None:
        repo = _FakeRepo()
        svc = NotificationService(
            repo,  # type: ignore[arg-type]
            ctx=NotificationContext(
                send_telegram=False,
                notify_safe_enabled=True,
                notify_safe_hold=True,
                notify_safe_change_only=True,
                dedupe_within_sec=60,
            ),
        )

        with patch("ai_invest.notifications.service.telegram_client.chat_id_review", return_value="-100123"):
            svc.notify_weekly_review(
                event_id=uuid.uuid4(),
                week_label="2026-02-09~2026-02-15",
                weekly_pnl=12345.0,
                win_rate=58.3,
                loss_tags_top3="OC_FALSE_BREAKOUT:2",
                rule_patch_status="자동 룰패치 미연결",
            )

        self.assertEqual(len(repo.rows), 1)
        row = repo.rows[0]
        self.assertEqual(row.get("template_id"), "tpl_weekly_review")
        self.assertEqual(row.get("status"), "PENDING")


if __name__ == "__main__":
    unittest.main()
