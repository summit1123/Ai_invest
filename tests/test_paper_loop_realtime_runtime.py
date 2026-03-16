from __future__ import annotations

from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

from ai_invest.agents.market_agent import MarketOpinion
from ai_invest.runtime.paper_loop import (
    _latest_quant_candidate_symbol,
    _market_input_for_safe_judge,
    _plan_is_hold_activation,
    _resolve_runtime_trade_plan,
)


KST = ZoneInfo("Asia/Seoul")


class _RepoStub:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def fetch_latest_agent_daily_report(self, *, agent_name: str):  # type: ignore[no-untyped-def]
        _ = agent_name
        return self._row


class _PlanRepoStub:
    def __init__(self, plan: dict | None, sessions: list[dict] | None = None) -> None:
        self._plan = plan
        self._sessions = list(sessions or [])

    def fetch_latest_trade_plan(self, *, prefer_active: bool = True, lookback_limit: int = 300):  # type: ignore[no-untyped-def]
        _ = prefer_active
        _ = lookback_limit
        return self._plan

    def fetch_meeting_sessions(self, *, limit: int = 10):  # type: ignore[no-untyped-def]
        _ = limit
        return list(self._sessions)


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


def test_resolve_runtime_trade_plan_returns_active_plan_without_bridge(monkeypatch) -> None:
    fixed_now = datetime(2026, 3, 13, 1, 10, 0, tzinfo=timezone.utc)
    repo = _PlanRepoStub(
        {
            "slot_key": "2026-03-13 10:05",
            "symbol": "KRW-BTC",
            "valid_from_kst": "2026-03-13T10:05:00+09:00",
            "valid_to_kst": "2026-03-13T12:05:00+09:00",
            "activation_gate": {"decision_effective": "HOLD", "hold_mode": "HOLD_CONDITIONAL"},
        }
    )
    monkeypatch.setattr("ai_invest.runtime.paper_loop._utcnow", lambda: fixed_now)
    plan = _resolve_runtime_trade_plan(repo=repo, rules_raw={"governance": {"plan_continuity": {"handoff_grace_minutes": 20}}})
    assert plan is not None
    assert plan.get("slot_key") == "2026-03-13 10:05"
    assert bool((plan.get("activation_gate") or {}).get("handoff_pending")) is False


def test_resolve_runtime_trade_plan_bridges_recent_expired_plan_while_meeting_open(monkeypatch) -> None:
    fixed_now = datetime(2026, 3, 13, 1, 11, 0, tzinfo=timezone.utc)  # 10:11 KST
    repo = _PlanRepoStub(
        {
            "slot_key": "2026-03-13 08:05",
            "symbol": "KRW-BTC",
            "valid_from_kst": "2026-03-13T08:05:00+09:00",
            "valid_to_kst": "2026-03-13T10:05:00+09:00",
            "activation_gate": {"decision_effective": "HOLD", "hold_mode": "HOLD_CONDITIONAL"},
        },
        sessions=[
            {
                "meeting_id": "mid-1",
                "meeting_type": "DAILY_STRATEGY",
                "status": "OPEN",
                "started_at": datetime(2026, 3, 13, 10, 5, 10, tzinfo=KST),
                "agenda": {"slot_key": "2026-03-13 10:05"},
            }
        ],
    )
    monkeypatch.setattr("ai_invest.runtime.paper_loop._utcnow", lambda: fixed_now)
    plan = _resolve_runtime_trade_plan(
        repo=repo,
        rules_raw={"governance": {"meeting_window_min": 5, "plan_continuity": {"handoff_grace_minutes": 20}}},
    )
    assert plan is not None
    assert bool(plan.get("handoff_bridge")) is True
    assert bool((plan.get("activation_gate") or {}).get("handoff_pending")) is True
    assert (plan.get("activation_gate") or {}).get("handoff_open_slot_key") == "2026-03-13 10:05"


def test_resolve_runtime_trade_plan_drops_expired_plan_after_bridge_window(monkeypatch) -> None:
    fixed_now = datetime(2026, 3, 13, 1, 40, 0, tzinfo=timezone.utc)  # 10:40 KST
    repo = _PlanRepoStub(
        {
            "slot_key": "2026-03-13 08:05",
            "symbol": "KRW-BTC",
            "valid_from_kst": "2026-03-13T08:05:00+09:00",
            "valid_to_kst": "2026-03-13T10:05:00+09:00",
            "activation_gate": {"decision_effective": "HOLD", "hold_mode": "HOLD_CONDITIONAL"},
        },
        sessions=[
            {
                "meeting_id": "mid-1",
                "meeting_type": "DAILY_STRATEGY",
                "status": "OPEN",
                "started_at": datetime(2026, 3, 13, 10, 5, 10, tzinfo=KST),
                "agenda": {"slot_key": "2026-03-13 10:05"},
            }
        ],
    )
    monkeypatch.setattr("ai_invest.runtime.paper_loop._utcnow", lambda: fixed_now)
    plan = _resolve_runtime_trade_plan(
        repo=repo,
        rules_raw={"governance": {"meeting_window_min": 5, "plan_continuity": {"handoff_grace_minutes": 20}}},
    )
    assert plan is None


def test_market_input_for_safe_judge_preserves_edge_calibration_reason() -> None:
    market = MarketOpinion(
        signal="HOLD",
        confidence=0.55,
        target_position_pct=0.0,
        signal_target_pct=0.0,
        alpha=0.12,
        mom_s=0.0,
        rev_s=0.0,
        strength=0.0,
        vol_scale=0.8,
        strategy_tag=None,
        entry_allowed=False,
        exit_reason=None,
        reason_codes=["RG_EDGE_TOO_LOW"],
        reason={
            "edge_calibration": {
                "enabled": True,
                "predicted_after_cost_bps": 1.25,
                "required_after_cost_bps": 4.0,
                "uncertainty_bps": 2.0,
            }
        },
        alpha_raw=0.12,
        regime="RANGE",
        trend_strength=0.0,
        shock_strength=0.0,
        expected_edge_bps=12.0,
        expected_cost_bps=8.0,
        expected_net_edge_bps=4.0,
        min_edge_required_bps=4.0,
    )
    payload = _market_input_for_safe_judge(market)
    assert payload["reason"]["edge_calibration"]["predicted_after_cost_bps"] == 1.25
    assert payload["predicted_after_cost_bps"] == 1.25
    assert payload["required_after_cost_bps"] == 4.0
    assert payload["after_cost_uncertainty_bps"] == 2.0
