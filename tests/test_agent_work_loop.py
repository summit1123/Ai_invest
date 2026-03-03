from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
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

    def test_run_cycle_uses_dynamic_symbols_when_allowlist_filter_disabled(self) -> None:
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
        rules_raw.setdefault("universe", {}).setdefault("dynamic", {})["enforce_static_allowlist"] = False

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

        assert captured["symbols"] == ["KRW-BTC", "KRW-FAKE"]
        findings = dict(captured.get("findings") or {})
        universe_selection = dict(findings.get("universe_selection") or {})
        assert list(universe_selection.get("excluded_not_allowed") or []) == []

    def test_run_cycle_filters_dynamic_symbols_when_allowlist_filter_enabled(self) -> None:
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
        rules_raw.setdefault("universe", {}).setdefault("dynamic", {})["enforce_static_allowlist"] = True

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

    def test_run_cycle_research_web_search_settings_applied(self) -> None:
        class Repo:
            def fetch_pause_state(self):
                return {"paused": False}

            def fetch_latest_reconciliation(self):
                return {"status": "OK"}

            def fetch_ready_agent_tasks(self, *, agent_name: str, limit: int = 10):  # noqa: ARG002
                return []

        repo = Repo()
        rules_raw = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))
        rules_raw["research"] = {
            "headline_limit": 16,
            "rss_timeout_sec": 7,
            "web_search": {
                "enabled": True,
                "provider": "wqb",
                "limit": 6,
                "timeout_sec": 9,
            },
        }

        captured: dict[str, object] = {}

        def fake_fetch_crypto_headlines(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return []

        fake_brief = SimpleNamespace(
            summary="summary",
            key_findings=[],
            llm_meta=None,
            risk_watchlist=[],
            next_actions=[],
        )

        with (
            patch.object(
                awl,
                "resolve_dynamic_universe",
                return_value=DynamicUniverseResult(
                    symbols=["KRW-BTC"],
                    source="test",
                    ranked_count=1,
                    total_krw_markets=1,
                    top24h_turnover=[],
                ),
            ),
            patch.object(
                awl,
                "_quant_candidate_rows",
                return_value=[
                    {
                        "symbol": "KRW-BTC",
                        "score": 0.7,
                        "snapshot": {"last_price": 1.0, "mid_price": 1.0, "spread_bps": 1.0},
                        "features": {"rsi_14": 55.0, "atr_pct": 1.2, "vol_zscore": 0.4},
                    }
                ],
            ),
            patch.object(awl, "fetch_crypto_headlines", side_effect=fake_fetch_crypto_headlines),
            patch.object(awl, "research_agent_daily_brief", return_value=fake_brief),
            patch.object(awl, "_store_report", return_value=uuid.uuid4()),
        ):
            awl.run_agent_work_cycle(
                repo=repo,  # type: ignore[arg-type]
                rules_raw=rules_raw,
                meeting_context="test",
                selected_agents=["research_agent"],
            )

        assert captured.get("symbol") == "KRW-BTC"
        assert captured.get("limit") == 16
        assert captured.get("include_web_search") is True
        assert captured.get("web_search_provider") == "wqb"
        assert captured.get("web_search_limit") == 6
        assert captured.get("web_search_timeout_sec") == 9
        assert captured.get("rss_timeout_sec") == 7

    def test_run_cycle_includes_macro_context_in_research_and_quant_reports(self) -> None:
        class Repo:
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

        repo = Repo()
        rules_raw = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))
        captured: dict[str, object] = {}

        def fake_store_report(**kwargs):  # noqa: ANN003
            captured[str(kwargs.get("agent_name"))] = kwargs.get("findings")
            return uuid.uuid4()

        fake_brief = SimpleNamespace(
            summary="summary",
            key_findings=[],
            llm_meta=None,
            risk_watchlist=[],
            next_actions=[],
        )
        macro = {
            "as_of_utc": "2026-03-03T00:00:00+00:00",
            "status": "OK",
            "risk_mode": "RISK_OFF",
            "fear_greed_index": {"value": 22, "classification": "Fear"},
            "crypto_market": {"btc_dominance_pct": 58.1},
            "errors": [],
        }

        with (
            patch.object(
                awl,
                "resolve_dynamic_universe",
                return_value=DynamicUniverseResult(
                    symbols=["KRW-BTC"],
                    source="test",
                    ranked_count=1,
                    total_krw_markets=1,
                    top24h_turnover=[],
                ),
            ),
            patch.object(
                awl,
                "_quant_candidate_rows",
                return_value=[
                    {
                        "symbol": "KRW-BTC",
                        "score": 0.7,
                        "snapshot": {"last_price": 1.0, "mid_price": 1.0, "spread_bps": 1.0},
                        "features": {"alpha": 0.7, "rsi_14": 55.0, "atr_pct": 1.1, "vol_zscore": 0.4},
                    }
                ],
            ),
            patch.object(
                awl,
                "_quick_backtest_candidate",
                return_value={"symbol": "KRW-BTC", "backtest_score": 1.0, "trades": 4},
            ),
            patch.object(awl, "fetch_macro_context", return_value=macro),
            patch.object(awl, "fetch_crypto_headlines", return_value=[]),
            patch.object(awl, "research_agent_daily_brief", return_value=fake_brief),
            patch.object(awl, "_store_report", side_effect=fake_store_report),
        ):
            awl.run_agent_work_cycle(
                repo=repo,  # type: ignore[arg-type]
                rules_raw=rules_raw,
                meeting_context="test",
                selected_agents=["research_agent", "quant_strategist"],
            )

        research_findings = dict(captured.get("research_agent") or {})
        quant_findings = dict(captured.get("quant_strategist") or {})
        assert dict(research_findings.get("macro_context") or {}).get("risk_mode") == "RISK_OFF"
        assert dict(quant_findings.get("macro_context") or {}).get("risk_mode") == "RISK_OFF"

    def test_quant_feedback_adjustment_uses_trade_execution_outcomes(self) -> None:
        class Repo:
            def fetch_realized_trades(self, *, limit: int = 200):  # noqa: ARG002
                rows = []
                for i in range(12):
                    rows.append({"symbol": "KRW-BTC", "realized_pnl": -1000.0, "pnl_bps": -18.0, "trade_id": f"btc-{i}"})
                for i in range(12):
                    rows.append({"symbol": "KRW-ETH", "realized_pnl": 1200.0, "pnl_bps": 22.0, "trade_id": f"eth-{i}"})
                return rows

            def fetch_execution_metrics(self, *, limit: int = 200):  # noqa: ARG002
                rows = []
                for i in range(12):
                    rows.append(
                        {
                            "symbol": "KRW-BTC",
                            "slippage_bps_vs_submit": 8.0,
                            "spread_bps_at_submit": 12.0,
                            "filled_ratio": 0.83,
                            "metric_id": f"btc-m-{i}",
                        }
                    )
                for i in range(12):
                    rows.append(
                        {
                            "symbol": "KRW-ETH",
                            "slippage_bps_vs_submit": 0.7,
                            "spread_bps_at_submit": 2.0,
                            "filled_ratio": 0.99,
                            "metric_id": f"eth-m-{i}",
                        }
                    )
                return rows

            def fetch_decision_outcomes(self, *, limit: int = 200):  # noqa: ARG002
                rows = []
                for i in range(10):
                    rows.append(
                        {
                            "symbol": "KRW-BTC",
                            "outcome_label": "LOSS",
                            "error_type": "OC_COST_UNDERESTIMATED",
                            "outcome_id": f"btc-o-{i}",
                        }
                    )
                for i in range(10):
                    rows.append(
                        {
                            "symbol": "KRW-ETH",
                            "outcome_label": "WIN",
                            "error_type": "NONE",
                            "outcome_id": f"eth-o-{i}",
                        }
                    )
                return rows

        repo = Repo()
        feedback = awl._build_quant_feedback_profiles(
            repo=repo,  # type: ignore[arg-type]
            rules_raw={},
            symbols=["KRW-BTC", "KRW-ETH"],
        )
        profiles = dict(feedback.get("profiles") or {})
        self.assertIn("KRW-BTC", profiles)
        self.assertIn("KRW-ETH", profiles)
        self.assertLess(
            float((profiles.get("KRW-BTC") or {}).get("score_adjustment") or 0.0),
            float((profiles.get("KRW-ETH") or {}).get("score_adjustment") or 0.0),
        )

        adjusted = awl._apply_feedback_to_candidates(
            candidates=[
                {"symbol": "KRW-BTC", "score": 0.70, "snapshot": {}, "features": {}},
                {"symbol": "KRW-ETH", "score": 0.70, "snapshot": {}, "features": {}},
            ],
            feedback_profiles=profiles,
        )
        self.assertEqual(adjusted[0]["symbol"], "KRW-ETH")
        self.assertEqual(adjusted[1]["symbol"], "KRW-BTC")


if __name__ == "__main__":
    unittest.main()
