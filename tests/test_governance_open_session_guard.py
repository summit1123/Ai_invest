from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ai_invest.meetings import governance_meeting as gm
from ai_invest.storage.postgres import DbEvent


KST = ZoneInfo("Asia/Seoul")


class _RepoStub:
    def __init__(self, sessions: list[dict]) -> None:
        self._sessions = list(sessions)
        self.updated: list[dict] = []
        self.events: list[DbEvent] = []

    def fetch_meeting_sessions(self, *, limit: int = 30) -> list[dict]:
        _ = limit
        return list(self._sessions)

    def update_meeting_session(self, **kwargs):  # type: ignore[no-untyped-def]
        self.updated.append(dict(kwargs))

    def insert_event(self, event: DbEvent) -> None:
        self.events.append(event)


def test_close_or_skip_open_meeting_closes_superseded_slot(monkeypatch):
    fixed_now_kst = datetime(2026, 2, 24, 11, 0, 0, tzinfo=KST)
    fixed_now_utc = fixed_now_kst.astimezone(timezone.utc)
    repo = _RepoStub(
        sessions=[
            {
                "meeting_id": "11111111-1111-1111-1111-111111111111",
                "meeting_type": "DAILY_STRATEGY",
                "status": "OPEN",
                "started_at": datetime(2026, 2, 24, 10, 50, 0, tzinfo=KST),
                "agenda": {"slot_key": "2026-02-24 10:00"},
            }
        ]
    )

    monkeypatch.setattr(gm, "_now_kst", lambda: fixed_now_kst)
    monkeypatch.setattr(gm, "_utcnow", lambda: fixed_now_utc)

    blocked = gm._close_or_skip_open_meeting(
        repo=repo,  # type: ignore[arg-type]
        rules_raw={"governance": {"meeting_window_min": 5, "max_open_meeting_minutes": 30}},
        emit=None,
        incoming_slot_key="2026-02-24 11:00",
    )
    assert blocked is False
    assert len(repo.updated) == 1
    assert repo.updated[0]["status"] == "CLOSED"
    assert "이전 슬롯 OPEN 회의 정리" in str(repo.updated[0]["summary"])
    assert len(repo.events) == 1
    assert repo.events[0].event_type == "MEETING_AUTO_CLOSED"
    assert repo.events[0].payload.get("reason_code") == "MEETING_SUPERSEDED_BY_NEW_SLOT"


def test_close_or_skip_open_meeting_blocks_same_slot_recent_session(monkeypatch):
    fixed_now_kst = datetime(2026, 2, 24, 11, 2, 0, tzinfo=KST)
    fixed_now_utc = fixed_now_kst.astimezone(timezone.utc)
    repo = _RepoStub(
        sessions=[
            {
                "meeting_id": "22222222-2222-2222-2222-222222222222",
                "meeting_type": "DAILY_STRATEGY",
                "status": "OPEN",
                "started_at": datetime(2026, 2, 24, 11, 1, 0, tzinfo=KST),
                "agenda": {"slot_key": "2026-02-24 11:00"},
            }
        ]
    )

    monkeypatch.setattr(gm, "_now_kst", lambda: fixed_now_kst)
    monkeypatch.setattr(gm, "_utcnow", lambda: fixed_now_utc)

    blocked = gm._close_or_skip_open_meeting(
        repo=repo,  # type: ignore[arg-type]
        rules_raw={"governance": {"meeting_window_min": 5, "max_open_meeting_minutes": 30}},
        emit=None,
        incoming_slot_key="2026-02-24 11:00",
    )
    assert blocked is True
    assert repo.updated == []
    assert repo.events == []
