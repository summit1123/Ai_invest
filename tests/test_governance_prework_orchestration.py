from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ai_invest.meetings import governance_meeting as gm


class _RepoStub:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def fetch_event_by_entity(self, *, event_type: str, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        for ev in reversed(self.events):
            if (
                str(ev.event_type) == str(event_type)
                and str(ev.entity_type) == str(entity_type)
                and str(ev.entity_id) == str(entity_id)
            ):
                return {"event_id": str(ev.event_id), "ts": ev.ts, "payload": dict(ev.payload or {})}
        return None

    def insert_event(self, ev: Any) -> None:
        self.events.append(ev)


def test_prework_ready_returns_true_without_refresh(monkeypatch):
    repo = _RepoStub()

    monkeypatch.setattr(
        gm,
        "collect_latest_work_reports",
        lambda **_: {
            "reports": {},
            "missing": [],
            "stale": [],
            "max_age_minutes": 360,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )

    called: list[tuple] = []

    def _fake_run_agent_work_cycle(**kwargs):  # type: ignore[no-untyped-def]
        called.append(tuple(kwargs.get("selected_agents") or []))
        return None

    monkeypatch.setattr(gm, "run_agent_work_cycle", _fake_run_agent_work_cycle)

    ok = gm.ensure_prework_ready_for_slot(
        repo=repo,  # type: ignore[arg-type]
        rules_raw={"governance": {"require_prework_reports": True, "prework_max_age_min": 360}},
        slot_key="2026-02-11 08:00",
    )
    assert ok is True
    assert called == []


def test_prework_missing_triggers_refresh_then_ready(monkeypatch):
    repo = _RepoStub()
    calls = {"n": 0}

    def _fake_collect_latest_work_reports(**_: Mapping[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "reports": {},
                "missing": ["research_agent"],
                "stale": [],
                "max_age_minutes": 360,
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        return {
            "reports": {"research_agent": {"report_id": "r1"}},
            "missing": [],
            "stale": [],
            "max_age_minutes": 360,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    monkeypatch.setattr(gm, "collect_latest_work_reports", _fake_collect_latest_work_reports)

    selected: list[list[str]] = []

    def _fake_run_agent_work_cycle(**kwargs):  # type: ignore[no-untyped-def]
        selected.append(list(kwargs.get("selected_agents") or []))
        return None

    monkeypatch.setattr(gm, "run_agent_work_cycle", _fake_run_agent_work_cycle)

    ok = gm.ensure_prework_ready_for_slot(
        repo=repo,  # type: ignore[arg-type]
        rules_raw={"governance": {"require_prework_reports": True, "prework_max_age_min": 360, "prework_refresh_cooldown_min": 5}},
        slot_key="2026-02-11 08:00",
    )
    assert ok is True
    assert selected == [["research_agent"]]
    event_types = [str(ev.event_type) for ev in repo.events]
    assert "MEETING_PREWORK_REFRESH_REQUESTED" in event_types
    assert "MEETING_PREWORK_READY" in event_types


def test_prework_missing_still_pending_returns_false(monkeypatch):
    repo = _RepoStub()

    monkeypatch.setattr(
        gm,
        "collect_latest_work_reports",
        lambda **_: {
            "reports": {},
            "missing": ["quant_strategist"],
            "stale": [],
            "max_age_minutes": 360,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(gm, "run_agent_work_cycle", lambda **_: None)

    ok = gm.ensure_prework_ready_for_slot(
        repo=repo,  # type: ignore[arg-type]
        rules_raw={"governance": {"require_prework_reports": True, "prework_max_age_min": 360, "prework_refresh_cooldown_min": 5}},
        slot_key="2026-02-11 08:00",
    )
    assert ok is False
    event_types = [str(ev.event_type) for ev in repo.events]
    assert "MEETING_PREWORK_REFRESH_REQUESTED" in event_types
    assert "MEETING_PREWORK_PENDING" in event_types
