from __future__ import annotations

from ai_invest.runtime.orchestrator_state import (
    build_orchestrator_summary,
    orchestrator_status_signature,
)


def test_orchestrator_status_signature_ignores_timestamp() -> None:
    before = {
        "ts_utc": "2026-03-11T01:00:00+00:00",
        "stopping": False,
        "workers": {"paper_loop": {"alive": True, "restarts": 0}},
    }
    after = {
        "ts_utc": "2026-03-11T01:00:05+00:00",
        "stopping": False,
        "workers": {"paper_loop": {"alive": True, "restarts": 0}},
    }

    assert orchestrator_status_signature(before) == orchestrator_status_signature(after)


def test_build_orchestrator_summary_counts_alive_and_dead_workers() -> None:
    summary = build_orchestrator_summary(
        {
            "ts_utc": "2026-03-11T01:00:00+00:00",
            "stopping": False,
            "workers": {
                "paper_loop": {"alive": True, "restarts": 0},
                "ops_work_loop": {"alive": False, "restarts": 2},
            },
        },
        source="db_event",
        exists=True,
    )

    assert summary["running"] is True
    assert summary["alive_workers"] == ["paper_loop"]
    assert summary["dead_workers"] == ["ops_work_loop"]
    assert summary["restart_counts"]["ops_work_loop"] == 2
