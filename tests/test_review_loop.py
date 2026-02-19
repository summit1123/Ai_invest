from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

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

    def test_run_once_bootstraps_weekly_priority_even_before_weekly_send_time(self) -> None:
        mod = _load_module()

        class _Repo:  # minimal shape for run_once in this test
            pass

        class _Notifier:
            pass

        bootstrapped: dict[str, str] = {}

        def _mark_bootstrap(**kwargs):
            bootstrapped["week_start"] = str(kwargs.get("week_start"))
            return True

        with (
            patch.object(mod, "_now_kst", return_value=datetime(2026, 2, 11, 10, 0, tzinfo=KST)),
            patch.object(mod, "_close_finished_weekly_priorities", return_value=0),
            patch.object(mod, "_ensure_weekly_priority", side_effect=_mark_bootstrap),
            patch.object(mod, "_latest_event_payload", return_value={}),
            patch.object(mod, "send_daily_review", return_value=False),
            patch.object(mod, "send_weekly_review", return_value=False),
        ):
            out = mod.run_once(
                repo=_Repo(),  # type: ignore[arg-type]
                notifier=_Notifier(),  # type: ignore[arg-type]
                rules_raw={"reporting": {"weekly_review_day": "SUN", "weekly_review_time_kst": "21:00"}},
            )

        self.assertFalse(out["daily"])
        self.assertFalse(out["weekly"])
        self.assertEqual(bootstrapped.get("week_start"), "2026-02-09")


if __name__ == "__main__":
    unittest.main()
