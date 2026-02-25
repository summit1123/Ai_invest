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
