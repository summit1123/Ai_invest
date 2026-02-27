from __future__ import annotations

import time

from ai_invest.config.rules_loader import load_rules
from ai_invest.config.llm_router import LLMRoute
from ai_invest.meetings.governance_meeting import run_governance_protocol
from ai_invest.meetings.governance_meeting import _infer_time_horizon
from ai_invest.meetings.governance_meeting import (
    AllowedActions,
    FinalTradePlan,
    _build_execution_plan,
    _build_plan_consistency_checks,
    _final_plan_declares_no_trade,
    _governance_llm_call_timeout_sec,
    _run_with_timeout,
    _to_final_trade_plan_v2,
)


def _base_fact_pack(*, recon_status: str = "OK", paused: bool = False) -> dict:
    return {
        "slot_key": "2026-02-11 09:00",
        "meeting_type": "DAILY_STRATEGY",
        "allowed_symbols": ["KRW-BTC", "KRW-ETH"],
        "evaluated": [
            {
                "symbol": "KRW-BTC",
                "score": 0.25,
                "snapshot": {"last_price": 100.0, "mid_price": 100.0, "spread_bps": 2.0},
                "features": {"rsi_14": 55.0, "atr_pct": 1.1, "vol_zscore": 1.7},
            }
        ],
        "rules": {
            "risk": {
                "max_position_pct_per_symbol": 20.0,
                "cooldown_minutes_after_trigger": 180,
                "max_risk_per_trade_pct": 0.35,
                "max_daily_loss_pct": 1.5,
            },
            "cost_guard": {"max_spread_bps_entry": 8.0, "max_total_cost_bps": 18.0},
            "stop_policy": {"hard_stop_pct": 1.2, "time_stop_minutes": 360},
            "execution": {"min_order_krw": 10000},
        },
        "ops_state": {"pause": {"paused": bool(paused)}, "latest_reconciliation": {"status": str(recon_status)}},
        "account_state": {"cash_krw": 1_000_000.0, "current_qty": 0.0, "avg_entry_price": None},
        "research_brief": {"headlines": [], "headlines_text": ""},
        "raw_rules_hint": {"signal": {"rsi_min": 50, "volume_zscore_min": 1.2}, "governance": {"default_target_position_pct": 10.0}},
        "valid_from_kst": "2026-02-11T09:00:00+09:00",
        "valid_to_kst": "2026-02-11T21:00:00+09:00",
    }


def test_governance_protocol_deterministic_ok(monkeypatch):
    monkeypatch.setenv("GOVERNANCE_LLM_ENABLED", "0")
    fact_pack = _base_fact_pack(recon_status="OK", paused=False)
    out = run_governance_protocol(fact_pack=fact_pack, rules_raw={})

    assert out.final_plan.symbol == "KRW-BTC"
    assert 0.0 < float(out.final_plan.target_position_pct) <= 20.0
    assert out.final_plan.allowed_actions.buy is True
    assert out.final_plan.valid_from_kst.endswith("+09:00")
    assert out.final_plan.valid_to_kst.endswith("+09:00")


def test_governance_protocol_enforces_flat_on_recon_fail(monkeypatch):
    monkeypatch.setenv("GOVERNANCE_LLM_ENABLED", "0")
    fact_pack = _base_fact_pack(recon_status="FAIL", paused=False)
    out = run_governance_protocol(fact_pack=fact_pack, rules_raw={})

    assert float(out.final_plan.target_position_pct) == 0.0
    assert out.final_plan.allowed_actions.buy is False


def test_governance_protocol_applies_capital_profile_cap(monkeypatch):
    monkeypatch.setenv("GOVERNANCE_LLM_ENABLED", "0")
    fact_pack = _base_fact_pack(recon_status="OK", paused=False)
    fact_pack["capital_profile"] = {
        "enabled": True,
        "tier_name": "seed_small",
        "equity_krw": 1_000_000.0,
        "max_target_position_pct": 4.0,
        "max_position_pct_per_symbol": 10.0,
        "cooldown_minutes_after_trigger": 240,
    }
    out = run_governance_protocol(fact_pack=fact_pack, rules_raw={})

    assert float(out.final_plan.target_position_pct) <= 4.0
    assert int(out.final_plan.cooldown_minutes) >= 240


def test_run_with_timeout_success():
    out = _run_with_timeout(fn=lambda: 7, timeout_sec=1, label="unit_ok")
    assert out == 7


def test_run_with_timeout_raises_timeout():
    def _slow() -> int:
        time.sleep(1.2)
        return 1

    try:
        _run_with_timeout(fn=_slow, timeout_sec=1, label="unit_timeout")
        raise AssertionError("expected timeout")
    except TimeoutError:
        assert True


def test_governance_llm_call_timeout_applies_hard_cap():
    route = LLMRoute(
        enabled=True,
        model="gpt-5-mini",
        api_style="responses",
        reasoning_effort="low",
        temperature=0.2,
        timeout_sec=3600,
    )
    timeout_sec = _governance_llm_call_timeout_sec(
        rules_raw={"governance": {"llm_call_timeout_sec": 45}},
        route=route,
    )
    assert timeout_sec == 45


def test_infer_time_horizon_prefers_swing_when_cost_pain_is_high():
    fact_pack = _base_fact_pack()
    fact_pack["evaluated"][0]["features"]["atr_pct"] = 1.1
    fact_pack["evaluated"][0]["snapshot"]["spread_bps"] = 3.2
    fact_pack["learning_context"] = {
        "recent_outcomes": {"total_trades": 20, "win_rate_pct": 42.0, "top_error_types": []},
        "outcome_windows": {
            "execution": {"total_trades": 6, "win_rate_pct": 41.0, "top_error_types": []},
            "short": {
                "total_trades": 20,
                "win_rate_pct": 46.0,
                "top_error_types": [{"error_type": "OC_COST_UNDERESTIMATED", "count": 8}],
            },
        },
    }
    out = _infer_time_horizon(fact_pack=fact_pack, symbol="KRW-BTC", fallback="1d")
    assert out == "swing"


def test_infer_time_horizon_prefers_intraday_on_high_volatility():
    fact_pack = _base_fact_pack()
    fact_pack["evaluated"][0]["features"]["atr_pct"] = 2.7
    fact_pack["evaluated"][0]["snapshot"]["spread_bps"] = 5.5
    out = _infer_time_horizon(fact_pack=fact_pack, symbol="KRW-BTC", fallback="1d")
    assert out == "intraday"


def test_infer_time_horizon_avoids_intraday_on_fee_dominated_churn():
    fact_pack = _base_fact_pack()
    fact_pack["evaluated"][0]["features"]["atr_pct"] = 1.3
    fact_pack["evaluated"][0]["snapshot"]["spread_bps"] = 5.0
    fact_pack["learning_context"] = {
        "recent_outcomes": {"total_trades": 14, "win_rate_pct": 39.0, "top_error_types": []},
        "outcome_windows": {
            "execution": {"total_trades": 8, "win_rate_pct": 37.0, "top_error_types": []},
            "short": {"total_trades": 14, "win_rate_pct": 41.0, "top_error_types": []},
        },
        "performance_windows": {
            "execution": {"trades_count": 8, "net_pnl_after_fees": -1400.0, "avg_hold_minutes": 4.2},
            "short": {"trades_count": 14, "fee_to_realized_ratio": 0.88},
        },
    }
    out = _infer_time_horizon(fact_pack=fact_pack, symbol="KRW-BTC", fallback="intraday")
    assert out == "1d"


def test_final_plan_declares_no_trade_on_zero_target() -> None:
    plan = FinalTradePlan(
        symbol="KRW-BTC",
        target_position_pct=0.0,
        allowed_actions=AllowedActions(buy=True, sell=True),
        valid_from_kst="2026-02-24T11:00:00+09:00",
        valid_to_kst="2026-02-24T12:00:00+09:00",
        notes="이번 슬롯은 신규 진입 금지",
    )
    declared, reasons = _final_plan_declares_no_trade(final_plan=plan)
    assert declared is True
    assert "target_position_pct<=0" in reasons


def test_consistency_check_blocks_data_collection_promotion_when_no_trade_declared() -> None:
    checks = _build_plan_consistency_checks(
        hard_plan_block=False,
        hard_plan_block_reasons=[],
        soft_plan_block=False,
        soft_plan_block_reasons=[],
        activation_decision_effective="PAPER",
        paper_data_collection_applied=True,
        allowed_actions={"buy": True, "sell": True},
        target_position_pct=3.0,
        notes="",
        no_trade_declared=True,
        no_trade_reasons=["target_position_pct<=0"],
    )
    assert checks["passed"] is False
    assert "final_no_trade_must_not_be_promoted" in checks["failed_checks"]


def test_execution_plan_target_remains_equal_to_resolved_plan_target() -> None:
    rules = load_rules("rules.yaml")
    final_plan = FinalTradePlan(
        symbol="KRW-BTC",
        target_position_pct=3.0,
        allowed_actions=AllowedActions(buy=True, sell=True),
        rebalance_band_pct=0.4,
        cooldown_minutes=180,
        valid_from_kst="2026-02-24T11:00:00+09:00",
        valid_to_kst="2026-02-24T12:00:00+09:00",
        constraints={},
        rationale={},
        evidence_refs=[],
        open_questions=[],
        conflict_resolution=[],
        notes="",
    )
    plan_v2 = _to_final_trade_plan_v2(
        final_plan=final_plan,
        rules_raw={},
        fact_pack=_base_fact_pack(),
        activation_gate={"decision": "PAPER"},
    )
    execution_plan = _build_execution_plan(
        final_plan=final_plan,
        plan_v2=plan_v2,
        rules=rules,
        rules_raw={},
        capital_profile={"max_target_position_pct": 20.0},
        risk_max_position_pct=20.0,
        activation_decision="PAPER",
        live_execution_enabled=False,
    )
    assert float(execution_plan.final_numbers.target_position_pct) == 3.0
