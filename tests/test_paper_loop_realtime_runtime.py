from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ai_invest.runtime.paper_loop import _latest_quant_candidate_symbol, _plan_is_hold_activation


class _RepoStub:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def fetch_latest_agent_daily_report(self, *, agent_name: str):  # type: ignore[no-untyped-def]
        _ = agent_name
        return self._row


def test_plan_is_hold_activation_detects_hold_variants() -> None:
    assert _plan_is_hold_activation({"activation_gate": {"decision_effective": "HOLD"}}) is True
    assert _plan_is_hold_activation({"activation_gate": {"decision": "HOLD"}}) is True
    assert _plan_is_hold_activation({"activation_gate": {"hold_mode": "HOLD_CONDITIONAL"}}) is True
    assert _plan_is_hold_activation({"activation_gate": {"decision_effective": "PAPER"}}) is False


def test_latest_quant_candidate_symbol_prefers_highest_score_with_allowlist() -> None:
    now = datetime.now(timezone.utc)
    repo = _RepoStub(
        {
            "created_at": now,
            "findings": {
                "candidates": [
                    {"symbol": "KRW-BTC", "score": 0.32},
                    {"symbol": "KRW-ETH", "score": 0.41},
                    {"symbol": "KRW-SOL", "score": 0.28},
                ]
            },
        }
    )
    out = _latest_quant_candidate_symbol(
        repo=repo,
        max_age_minutes=180,
        allowed_symbols={"KRW-BTC", "KRW-ETH"},
    )
    assert out == "KRW-ETH"


def test_latest_quant_candidate_symbol_ignores_stale_report() -> None:
    now = datetime.now(timezone.utc)
    repo = _RepoStub(
        {
            "created_at": now - timedelta(minutes=400),
            "findings": {"candidates": [{"symbol": "KRW-BTC", "score": 0.9}]},
        }
    )
    out = _latest_quant_candidate_symbol(repo=repo, max_age_minutes=180)
    assert out is None


def test_latest_quant_candidate_symbol_falls_back_to_suggested_plan() -> None:
    now = datetime.now(timezone.utc)
    repo = _RepoStub(
        {
            "created_at": now,
            "findings": {"candidates": [], "suggested_plan": {"symbol": "KRW-XRP"}},
        }
    )
    out = _latest_quant_candidate_symbol(repo=repo, max_age_minutes=180)
    assert out == "KRW-XRP"

