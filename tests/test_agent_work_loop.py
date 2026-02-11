from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from ai_invest.work.agent_work_loop import collect_latest_work_reports


class _FakeRepo:
    def __init__(self, rows: dict[str, dict]):
        self._rows = rows

    def fetch_latest_agent_daily_report(self, *, agent_name: str):
        return self._rows.get(agent_name)


class AgentWorkLoopTests(unittest.TestCase):
    def test_collect_reports_marks_missing_and_stale(self) -> None:
        now = datetime(2026, 2, 11, 0, 0, 0, tzinfo=timezone.utc)
        rows = {
            "research_agent": {
                "report_id": "r1",
                "created_at": now - timedelta(minutes=10),
                "title": "ok",
                "summary": "fresh",
            },
            "quant_strategist": {
                "report_id": "q1",
                "created_at": now - timedelta(minutes=400),
                "title": "old",
                "summary": "stale",
            },
        }
        repo = _FakeRepo(rows=rows)
        out = collect_latest_work_reports(
            repo=repo,  # type: ignore[arg-type]
            agent_names=["research_agent", "quant_strategist", "risk_manager"],
            max_age_minutes=180,
            now_utc=now,
        )

        self.assertIn("risk_manager", out["missing"])
        self.assertIn("quant_strategist", out["stale"])
        self.assertNotIn("research_agent", out["stale"])
        self.assertIn("research_agent", out["reports"])

    def test_collect_reports_handles_no_datetime(self) -> None:
        now = datetime(2026, 2, 11, 0, 0, 0, tzinfo=timezone.utc)
        rows = {
            "ops_manager": {
                "report_id": "o1",
                "created_at": "invalid",
                "title": "ops",
                "summary": "x",
            }
        }
        repo = _FakeRepo(rows=rows)
        out = collect_latest_work_reports(
            repo=repo,  # type: ignore[arg-type]
            agent_names=["ops_manager"],
            max_age_minutes=60,
            now_utc=now,
        )
        self.assertIn("ops_manager", out["stale"])


if __name__ == "__main__":
    unittest.main()

