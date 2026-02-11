from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime
from pathlib import Path

from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def _load_module():
    path = Path("scripts/run_review_loop.py")
    spec = importlib.util.spec_from_file_location("run_review_loop", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ReviewLoopTests(unittest.TestCase):
    def test_should_send_daily_review_after_time(self) -> None:
        mod = _load_module()
        now = datetime(2026, 2, 11, 23, 30, tzinfo=KST)
        self.assertTrue(mod.should_send_daily_review(now_kst=now, daily_time_kst="23:10", latest_sent_day="2026-02-10"))
        self.assertFalse(mod.should_send_daily_review(now_kst=now, daily_time_kst="23:10", latest_sent_day="2026-02-11"))

    def test_should_send_weekly_review_on_sunday(self) -> None:
        mod = _load_module()
        now = datetime(2026, 2, 15, 21, 30, tzinfo=KST)  # Sunday
        do_send, ws, we = mod.should_send_weekly_review(
            now_kst=now,
            weekly_day="SUN",
            weekly_time_kst="21:00",
            latest_sent_week_start="2026-02-02",
        )
        self.assertTrue(do_send)
        self.assertEqual(ws, "2026-02-09")
        self.assertEqual(we, "2026-02-15")


if __name__ == "__main__":
    unittest.main()
