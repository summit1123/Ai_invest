from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ai_invest.meetings import governance_meeting as gm


KST = ZoneInfo("Asia/Seoul")


class _RepoStub:
    def __init__(self, existing_slots: set[str] | None = None) -> None:
        self.existing_slots = set(existing_slots or set())

    def meeting_slot_exists(self, *, slot_key: str) -> bool:
        return str(slot_key) in self.existing_slots


class _NotifierStub:
    pass


def test_catchup_picks_missed_slot_when_outside_window(monkeypatch):
    repo = _RepoStub(existing_slots={"2026-02-11 00:00"})
    notifier = _NotifierStub()
    fixed_now = datetime(2026, 2, 11, 10, 0, 0, tzinfo=KST)
    called: list[str] = []

    def _fake_run(*, repo, notifier, rules_raw, force_slot_key=None, emit=None):  # type: ignore[no-untyped-def]
        called.append(str(force_slot_key))
        return str(force_slot_key)

    monkeypatch.setattr(gm, "_now_kst", lambda: fixed_now)
    monkeypatch.setattr(gm, "run_governance_meeting_now", _fake_run)

    slot = gm.maybe_run_scheduled_governance_meeting(
        repo=repo,  # type: ignore[arg-type]
        notifier=notifier,  # type: ignore[arg-type]
        rules_raw={
            "governance": {
                "daily_meeting_times_kst": ["00:00", "08:00", "16:00"],
                "meeting_window_min": 5,
                "catchup_enabled": True,
                "catchup_lookback_hours": 12,
            }
        },
    )
    assert slot == "2026-02-11 08:00"
    assert called == ["2026-02-11 08:00"]


def test_window_slot_has_priority_over_older_catchup(monkeypatch):
    repo = _RepoStub(existing_slots=set())
    notifier = _NotifierStub()
    fixed_now = datetime(2026, 2, 11, 8, 2, 0, tzinfo=KST)
    called: list[str] = []

    def _fake_run(*, repo, notifier, rules_raw, force_slot_key=None, emit=None):  # type: ignore[no-untyped-def]
        called.append(str(force_slot_key))
        return str(force_slot_key)

    monkeypatch.setattr(gm, "_now_kst", lambda: fixed_now)
    monkeypatch.setattr(gm, "run_governance_meeting_now", _fake_run)

    slot = gm.maybe_run_scheduled_governance_meeting(
        repo=repo,  # type: ignore[arg-type]
        notifier=notifier,  # type: ignore[arg-type]
        rules_raw={
            "governance": {
                "daily_meeting_times_kst": ["00:00", "08:00", "16:00"],
                "meeting_window_min": 5,
                "catchup_enabled": True,
                "catchup_lookback_hours": 24,
            }
        },
    )
    assert slot == "2026-02-11 08:00"
    assert called == ["2026-02-11 08:00"]
