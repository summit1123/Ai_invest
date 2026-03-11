from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ai_invest.meetings import governance_meeting as gm

KST = ZoneInfo("Asia/Seoul")


class _RepoStub:
    def __init__(self, now_kst: datetime) -> None:
        self._now = now_kst

    def fetch_decision_outcomes(self, *, limit: int = 1200):  # type: ignore[no-untyped-def]
        _ = limit
        return [
            {
                "reviewed_at": self._now - timedelta(hours=2),
                "outcome_label": "WIN",
                "error_type": "",
            },
            {
                "reviewed_at": self._now - timedelta(hours=1),
                "outcome_label": "LOSS",
                "error_type": "OC_COST_UNDERESTIMATED",
            },
        ]

    def fetch_strategy_reviews(self, *, limit: int = 1):  # type: ignore[no-untyped-def]
        _ = limit
        return []

    def fetch_realized_trades(self, *, limit: int = 5000):  # type: ignore[no-untyped-def]
        _ = limit
        return [
            {
                "ts_open": self._now - timedelta(hours=2, minutes=40),
                "ts_close": self._now - timedelta(hours=2, minutes=30),
                "realized_pnl": 1200.0,
                "fees_total": 320.0,
            },
            {
                "ts_open": self._now - timedelta(hours=1, minutes=15),
                "ts_close": self._now - timedelta(hours=1),
                "realized_pnl": -300.0,
                "fees_total": 210.0,
            },
        ]

    def fetch_decisions(self, *, judge_type: str | None = None, limit: int = 200):  # type: ignore[no-untyped-def]
        _ = (judge_type, limit)
        return [
            {
                "ts": self._now - timedelta(minutes=50),
                "action": "HOLD",
                "selected_reasons": ["RG_SPREAD_TOO_WIDE"],
                "gates": {"regime_trade_allowed": True},
            },
            {
                "ts": self._now - timedelta(minutes=35),
                "action": "BUY",
                "selected_reasons": ["RG_PASS"],
                "gates": {
                    "regime_trade_allowed": True,
                    "market_expected_cost_bps": 8.0,
                    "market_expected_net_edge_bps": 36.0,
                },
            },
        ]

    def fetch_execution_metrics(self, *, limit: int = 200):  # type: ignore[no-untyped-def]
        _ = limit
        return [
            {
                "ts_submit": self._now - timedelta(minutes=45),
                "slippage_bps_vs_submit": 1.4,
                "spread_bps_at_submit": 2.2,
                "filled_ratio": 0.98,
                "latency_ms_submit_to_fill": 420,
            }
        ]

    def fetch_latest_event(self, *, event_type: str):  # type: ignore[no-untyped-def]
        if event_type != "DAILY_REVIEW_SENT":
            return None
        return {
            "payload": {
                "day": "2026-02-25",
                "realized_pnl": 900.0,
                "fees_paid": 530.0,
                "trades_count": 2,
                "improvement_advice": {
                    "improvement_title": "수수료 누수 차단이 1순위",
                    "improvement_reason": "비용이 손익을 잠식",
                    "suggested_changes": ["entry_alpha 상향", "max_spread_bps 축소"],
                    "diagnostics": {"avg_hold_minutes": 12.5},
                },
            }
        }

    def fetch_meeting_sessions(self, *, limit: int = 50):  # type: ignore[no-untyped-def]
        _ = limit
        return [
            {
                "meeting_id": "11111111-1111-1111-1111-111111111111",
                "meeting_type": "DAILY_STRATEGY",
                "status": "CLOSED",
                "started_at": self._now - timedelta(hours=3),
                "ended_at": self._now - timedelta(hours=2, minutes=30),
                "summary": "최근 회의 결론: 비용 미커버 단타를 줄이고 기대값이 양수일 때만 진입.",
                "decisions": {
                    "final_plan": {"symbol": "KRW-BTC", "target_position_pct": 2.0},
                    "activation_status": "ACTIVE_DATA_COLLECTION",
                },
            }
        ]


def test_learning_context_contains_recent_meeting_lessons() -> None:
    now_kst = datetime(2026, 2, 25, 10, 0, tzinfo=KST)
    repo = _RepoStub(now_kst=now_kst)
    rules_raw = {
        "governance": {
            "learning_context": {
                "max_outcome_rows": 500,
                "recent_window_fallback_min_trades": 1,
                "outcome_windows": {
                    "execution_hours": 6,
                    "short_days": 14,
                    "medium_days": 90,
                    "anchor_days": 270,
                },
                "meeting_memory": {
                    "enabled": True,
                    "lookback_days": 14,
                    "max_sessions": 4,
                    "summary_max_chars": 200,
                },
            }
        }
    }

    ctx = gm._build_learning_context(repo=repo, now_kst=now_kst, rules_raw=rules_raw)
    lessons = [x for x in list(ctx.get("recent_meeting_lessons") or []) if isinstance(x, dict)]
    assert len(lessons) == 1
    assert str(lessons[0].get("symbol")) == "KRW-BTC"
    assert "비용 미커버 단타" in str(lessons[0].get("summary") or "")

    perf = dict(ctx.get("recent_performance") or {})
    assert int(perf.get("trades_count") or 0) >= 1
    assert float(perf.get("fees_paid") or 0.0) > 0.0
    assert "performance_windows" in ctx

    latest_daily = dict(ctx.get("latest_daily_review") or {})
    assert str(latest_daily.get("improvement_title") or "") == "수수료 누수 차단이 1순위"
    assert len(list(latest_daily.get("suggested_changes") or [])) >= 1

    decision_health = dict(ctx.get("recent_decision_health") or {})
    assert int(decision_health.get("total_decisions") or 0) >= 1
    assert float(decision_health.get("hold_ratio_pct") or 0.0) >= 0.0
    assert str(decision_health.get("dominant_reason_code") or "") in {"RG_SPREAD_TOO_WIDE", "RG_PASS"}

    exec_quality = dict(ctx.get("recent_execution_quality") or {})
    assert int(exec_quality.get("samples") or 0) >= 1
    assert float(exec_quality.get("avg_spread_bps_at_submit") or 0.0) > 0.0


def test_learning_context_skips_auto_closed_meeting_lessons() -> None:
    now_kst = datetime(2026, 2, 25, 10, 0, tzinfo=KST)

    class _RepoWithAutoClosed(_RepoStub):
        def fetch_meeting_sessions(self, *, limit: int = 50):  # type: ignore[no-untyped-def]
            _ = limit
            return [
                {
                    "meeting_id": "auto-closed",
                    "meeting_type": "DAILY_STRATEGY",
                    "status": "CLOSED",
                    "started_at": self._now - timedelta(hours=2),
                    "ended_at": self._now - timedelta(hours=1, minutes=40),
                    "summary": "자동 종료: orphan OPEN 회의 정리",
                    "decisions": {
                        "error": "MEETING_PATCH_ROLLOUT_RESET",
                        "auto_closed": True,
                    },
                },
                {
                    "meeting_id": "normal-closed",
                    "meeting_type": "DAILY_STRATEGY",
                    "status": "CLOSED",
                    "started_at": self._now - timedelta(hours=3),
                    "ended_at": self._now - timedelta(hours=2, minutes=30),
                    "summary": "최근 회의 결론: 비용 미커버 단타를 줄이고 기대값이 양수일 때만 진입.",
                    "decisions": {
                        "final_plan": {"symbol": "KRW-BTC", "target_position_pct": 2.0},
                        "activation_status": "ACTIVE_DATA_COLLECTION",
                    },
                },
            ]

    repo = _RepoWithAutoClosed(now_kst=now_kst)
    rules_raw = {
        "governance": {
            "learning_context": {
                "max_outcome_rows": 500,
                "recent_window_fallback_min_trades": 1,
                "outcome_windows": {
                    "execution_hours": 6,
                    "short_days": 14,
                    "medium_days": 90,
                    "anchor_days": 270,
                },
                "meeting_memory": {
                    "enabled": True,
                    "lookback_days": 14,
                    "max_sessions": 4,
                    "summary_max_chars": 200,
                },
            }
        }
    }

    ctx = gm._build_learning_context(repo=repo, now_kst=now_kst, rules_raw=rules_raw)
    lessons = [x for x in list(ctx.get("recent_meeting_lessons") or []) if isinstance(x, dict)]
    assert len(lessons) == 1
    assert str(lessons[0].get("meeting_id") or "") == "normal-closed"
