from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

from ai_invest.market_data.universe_selector import DynamicUniverseResult
from ai_invest.work import agent_work_loop as awl
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

    def test_run_cycle_filters_candidates_to_allowed_symbols(self) -> None:
        class Repo:
            def __init__(self) -> None:
                self.completed: list[str] = []

            def fetch_pause_state(self):
                return {"paused": False}

            def fetch_latest_reconciliation(self):
                return {"status": "OK"}

            def fetch_ready_agent_tasks(self, *, agent_name: str, limit: int = 10):  # noqa: ARG002
                return []

            def fetch_cash_balance(self, *, currency: str):  # noqa: ARG002
                return 10_000_000.0

            def fetch_position(self, symbol: str):  # noqa: ARG002
                return None

            def mark_agent_task_completed(self, *, task_id: str, agent_name: str, result: dict):  # noqa: ARG002
                self.completed.append(task_id)

        repo = Repo()
        rules_raw = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))

        captured: dict[str, object] = {}

        def fake_quant_rows(*, rules_raw, rules, symbols, lookback_minutes, alpha_cfg):  # noqa: ANN001, ARG001
            captured["symbols"] = list(symbols)
            return [
                {
                    "symbol": "KRW-BTC",
                    "score": 0.7,
                    "snapshot": {"mid_price": 100.0, "spread_bps": 1.0},
                    "features": {"alpha": 0.7},
                }
            ]

        def fake_store_report(**kwargs):  # noqa: ANN003
            if kwargs.get("agent_name") == "quant_strategist":
                captured["findings"] = kwargs.get("findings")
            return uuid.uuid4()

        with (
            patch.object(
                awl,
                "resolve_dynamic_universe",
                return_value=DynamicUniverseResult(
                    symbols=["KRW-BTC", "KRW-FAKE"],
                    source="test",
                    ranked_count=2,
                    total_krw_markets=2,
                    top24h_turnover=[],
                ),
            ),
            patch.object(awl, "_quant_candidate_rows", side_effect=fake_quant_rows),
            patch.object(awl, "_quick_backtest_candidate", return_value={"symbol": "KRW-BTC", "backtest_score": 1.0, "trades": 3}),
            patch.object(awl, "_store_report", side_effect=fake_store_report),
        ):
            awl.run_agent_work_cycle(
                repo=repo,  # type: ignore[arg-type]
                rules_raw=rules_raw,
                meeting_context="test",
                selected_agents=["quant_strategist"],
            )

        assert captured["symbols"] == ["KRW-BTC"]
        findings = dict(captured.get("findings") or {})
        universe_selection = dict(findings.get("universe_selection") or {})
        assert "KRW-FAKE" in list(universe_selection.get("excluded_not_allowed") or [])


if __name__ == "__main__":
    unittest.main()
