from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

from ai_invest.config.rules_loader import load_rules
from ai_invest.config.llm_router import LLMRoute
from ai_invest.meetings.governance_meeting import run_governance_protocol
from ai_invest.meetings.governance_meeting import _infer_time_horizon
from ai_invest.meetings.governance_meeting import (
    AllowedActions,
    FinalTradePlan,
    OpsDraft,
    QuantPlanDraft,
    RiskDraft,
    _build_agent_tasks,
    _build_runtime_entry_policy,
    _build_execution_plan,
    _build_plan_consistency_checks,
    enforce_final_trade_plan,
    _final_plan_declares_no_trade,
    _governance_llm_call_timeout_sec,
    _render_signal_audit_notes,
    _render_runtime_entry_policy_notes,
    _run_with_timeout,
    _summarize_signal_audit,
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


def test_final_plan_buy_disabled_with_positive_target_is_not_no_trade() -> None:
    plan = FinalTradePlan(
        symbol="KRW-BTC",
        target_position_pct=6.0,
        allowed_actions=AllowedActions(buy=False, sell=True),
        valid_from_kst="2026-02-24T11:00:00+09:00",
        valid_to_kst="2026-02-24T12:00:00+09:00",
        notes="회의는 정책 cap만 유지하고 진입은 런타임 재평가",
    )
    declared, reasons = _final_plan_declares_no_trade(final_plan=plan)
    assert declared is False
    assert reasons == []


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


def test_execution_plan_preserves_target_for_conditional_hold() -> None:
    rules = load_rules("rules.yaml")
    final_plan = FinalTradePlan(
        symbol="KRW-BTC",
        target_position_pct=6.0,
        allowed_actions=AllowedActions(buy=False, sell=True),
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
        activation_gate={
            "decision": "PAPER",
            "decision_effective": "HOLD",
            "hold_mode": "HOLD_CONDITIONAL",
            "inter_slot_realtime_mode": True,
            "conditional_activation": {"enabled": True},
        },
    )
    execution_plan = _build_execution_plan(
        final_plan=final_plan,
        plan_v2=plan_v2,
        rules=rules,
        rules_raw={},
        capital_profile={"max_target_position_pct": 20.0},
        risk_max_position_pct=20.0,
        activation_decision="HOLD",
        live_execution_enabled=True,
        conditional_hold_target_allowed=True,
    )
    assert float(execution_plan.final_numbers.target_position_pct) == 6.0
    assert bool(execution_plan.gates["conditional_hold_target_allowed"]) is True
    assert bool(execution_plan.gates["paper_only"]) is False
    assert bool(plan_v2.confidence.paper_only_recommended) is False


def test_enforce_final_trade_plan_keeps_target_under_live_soft_veto() -> None:
    plan = FinalTradePlan(
        symbol="KRW-BTC",
        target_position_pct=6.0,
        allowed_actions=AllowedActions(buy=True, sell=True),
        valid_from_kst="2026-02-24T11:00:00+09:00",
        valid_to_kst="2026-02-24T12:00:00+09:00",
        constraints={},
        rationale={},
        evidence_refs=[],
        open_questions=[],
        conflict_resolution=[],
        notes="",
    )
    out = enforce_final_trade_plan(
        plan=plan,
        quant=QuantPlanDraft(
            symbol="KRW-BTC",
            target_position_pct=6.0,
            allowed_actions=AllowedActions(buy=True, sell=True),
            notes="",
        ),
        risk=RiskDraft(
            veto=True,
            max_position_pct=20.0,
            max_loss_per_trade_pct=0.5,
            max_daily_loss_pct=1.5,
            notes="",
        ),
        ops=OpsDraft(
            veto=False,
            trade_window_allowed=True,
            notes="",
        ),
        fact_pack={
            "ops_state": {"pause": {"paused": False}, "latest_reconciliation": {"status": "OK"}},
            "raw_rules_hint": {
                "universe": {"mode": "live"},
                "governance": {"activation_gate": {"conditional_activation": {"enabled": True}}},
                "paper_mode": {"data_collection": {"allow_soft_plan_block_bypass": False}},
            },
        },
        allowed_symbols={"KRW-BTC"},
        fallback_symbol="KRW-BTC",
        hard_max_position_pct=20.0,
    )
    assert out.allowed_actions.buy is True
    assert float(out.target_position_pct) == 6.0


def test_enforce_final_trade_plan_recovers_policy_cap_when_soft_veto_zeroes_meeting_plan() -> None:
    plan = FinalTradePlan(
        symbol="KRW-BTC",
        target_position_pct=0.0,
        allowed_actions=AllowedActions(buy=False, sell=True),
        valid_from_kst="2026-02-24T11:00:00+09:00",
        valid_to_kst="2026-02-24T12:00:00+09:00",
        constraints={},
        rationale={},
        evidence_refs=[],
        open_questions=[],
        conflict_resolution=[],
        notes="",
    )
    out = enforce_final_trade_plan(
        plan=plan,
        quant=QuantPlanDraft(
            symbol="KRW-BTC",
            target_position_pct=8.0,
            allowed_actions=AllowedActions(buy=True, sell=True),
            notes="",
        ),
        risk=RiskDraft(
            veto=True,
            max_position_pct=20.0,
            max_loss_per_trade_pct=0.5,
            max_daily_loss_pct=1.5,
            notes="",
        ),
        ops=OpsDraft(
            veto=False,
            trade_window_allowed=True,
            notes="",
        ),
        fact_pack={
            "ops_state": {"pause": {"paused": False}, "latest_reconciliation": {"status": "OK"}},
            "raw_rules_hint": {
                "universe": {"mode": "live"},
                "governance": {"activation_gate": {"conditional_activation": {"enabled": True}}},
                "paper_mode": {"data_collection": {"allow_soft_plan_block_bypass": False}},
            },
        },
        allowed_symbols={"KRW-BTC"},
        fallback_symbol="KRW-BTC",
        hard_max_position_pct=20.0,
    )
    assert out.allowed_actions.buy is False
    assert out.allowed_actions.sell is True
    assert float(out.target_position_pct) == 8.0
    assert any("policy cap 8.0%" in str(x) for x in list(out.conflict_resolution or []))


def test_runtime_entry_policy_marks_conditional_runtime_mode() -> None:
    policy = _build_runtime_entry_policy(
        inter_slot_realtime_mode=True,
        plan_execution_blocked=False,
        resolved_allowed_actions={"buy": False, "sell": True},
        resolved_target_position_pct=10.0,
        activation_gate={
            "conditional_activation": {
                "conditions": {
                    "min_pass_conditions": 3,
                    "sustain_seconds": 180,
                }
            },
            "cap_runtime": {
                "required_passes": 6,
                "consecutive_passes": 0,
            },
        },
        rules_raw={"governance": {"micro_mode": {"allow_live_exploration": False}}},
        universe_mode="live",
    )
    assert policy["mode"] == "CONDITIONAL_RUNTIME"
    assert bool(policy["runtime_entry_allowed"]) is True
    assert bool(policy["runtime_promotion_enabled"]) is True
    assert policy["execution_authority"] == "realtime_loop"
    assert policy["entry_objective"] == "profit-first"
    assert bool(policy["exploration_enabled"]) is False
    assert float(policy["profit_floor_bps"]) == 1.0
    assert float(policy["profit_required_margin_bps"]) == 0.5
    assert bool(policy["meeting_buy_flag"]) is False
    assert float(policy["policy_cap_target_pct"]) == 10.0


def test_runtime_entry_policy_marks_live_learning_mode() -> None:
    policy = _build_runtime_entry_policy(
        inter_slot_realtime_mode=True,
        plan_execution_blocked=False,
        resolved_allowed_actions={"buy": False, "sell": True},
        resolved_target_position_pct=10.0,
        activation_gate={
            "live_data_collection_applied": True,
            "conditional_activation": {
                "conditions": {
                    "min_pass_conditions": 3,
                    "sustain_seconds": 180,
                }
            },
            "cap_runtime": {
                "required_passes": 6,
                "consecutive_passes": 0,
            },
        },
        rules_raw={
            "governance": {
                "micro_mode": {"allow_live_exploration": False},
                "activation_gate": {
                    "live_data_collection": {
                        "enabled": True,
                        "target_position_pct": 12.0,
                        "exploration_enabled": True,
                        "profit_floor_bps": 0.0,
                        "profit_required_margin_bps": 0.0,
                        "min_predicted_after_cost_bps": -0.25,
                        "alpha_bypass_on_exploration": True,
                    }
                },
            }
        },
        universe_mode="live",
    )
    assert policy["mode"] == "LIVE_DATA_COLLECTION"
    assert bool(policy["runtime_entry_allowed"]) is True
    assert policy["entry_objective"] == "learning-loop"
    assert bool(policy["exploration_enabled"]) is True
    assert bool(policy["learning_mode"]) is True
    assert float(policy["profit_floor_bps"]) == 0.0
    assert float(policy["profit_required_margin_bps"]) == 0.0
    assert float(policy["min_predicted_after_cost_bps"]) == -0.25
    assert bool(policy["alpha_bypass_on_exploration"]) is True


def test_runtime_entry_policy_notes_explain_realtime_ownership() -> None:
    notes = _render_runtime_entry_policy_notes(
        {
            "runtime_entry_allowed": True,
            "runtime_promotion_enabled": True,
            "execution_authority": "realtime_loop",
            "entry_timing_owner": "realtime_loop",
            "entry_objective": "profit-first",
            "exploration_enabled": False,
            "learning_mode": False,
            "min_predicted_after_cost_bps": 0.0,
            "alpha_bypass_on_exploration": False,
            "profit_floor_bps": 1.0,
            "profit_required_margin_bps": 0.5,
            "meeting_buy_flag": False,
            "policy_cap_target_pct": 10.0,
            "min_pass_conditions": 3,
            "sustain_seconds": 180,
            "required_passes": 6,
        }
    )
    assert "[runtime_entry_policy]" in notes
    assert "runtime_entry_allowed=True" in notes
    assert "runtime_promotion_enabled=True" in notes
    assert "execution_authority=realtime_loop" in notes
    assert "entry_timing_owner=realtime_loop" in notes
    assert "entry_objective=profit-first" in notes
    assert "exploration_enabled=False" in notes
    assert "learning_mode=False" in notes
    assert "exploration_floor=min_predicted_after_cost_bps=0.00,alpha_bypass_on_exploration=False" in notes
    assert "profit_gate=floor_bps=1.00,required_margin_bps=0.50" in notes
    assert "promotion_rule=min_pass_conditions=3,sustain_seconds=180,required_passes=6" in notes


def test_summarize_signal_audit_flags_blocked_watch_hits_and_runtime_gaps() -> None:
    window_start = datetime(2026, 3, 18, 23, 5, tzinfo=timezone.utc)
    window_end = window_start + timedelta(hours=2)
    market_ts = window_start + timedelta(minutes=5)
    audit = _summarize_signal_audit(
        symbol="KRW-BTC",
        window_start=window_start,
        window_end=window_end,
        market_rows=[
            {
                "ts": market_ts,
                "signal": "BUY",
                "decision_id": "d-1",
                "raw_payload": {
                    "signal": "BUY",
                    "entry_allowed": True,
                    "alpha": 0.82,
                    "expected_net_edge_bps": 2.4,
                    "reason_codes": ["RG_PASS"],
                },
            }
        ],
        safe_rows=[
            {
                "ts": market_ts,
                "decision_id": "d-1",
                "action": "HOLD",
                "selected_reasons": ["RG_MICRO_BLOCKED_POLICY"],
            }
        ],
        orchestrator_rows=[
            {
                "ts": window_start + timedelta(minutes=45),
                "payload": {
                    "stopping": False,
                    "workers": {
                        "paper_loop": {"alive": False},
                        "ops_work_loop": {"alive": True},
                    },
                },
            }
        ],
        rules_raw={
            "scheduling": {"decision_interval_sec": 30},
            "governance": {
                "micro_mode": {
                    "min_alpha": 0.65,
                    "live_min_predicted_after_cost_bps": 0.0,
                    "live_profit_floor_bps": 1.0,
                    "live_profit_required_margin_bps": 0.5,
                    "live_max_uncertainty_bps": 8.0,
                },
                "activation_gate": {"conditional_activation": {"conditions": {"min_alpha": 0.75}}},
            },
        },
    )

    assert audit["market_sample_count"] == 1
    assert audit["market_buy_signal_count"] == 1
    assert audit["alpha_entry_hits"] == 1
    assert audit["profit_watch_hits"] == 1
    assert audit["profit_watch_blocked"] == 1
    assert audit["profit_watch_promoted"] == 0
    assert audit["safe_buy_count"] == 0
    assert audit["blocked_reason_counts"] == {"RG_MICRO_BLOCKED_POLICY": 1}
    assert audit["runtime_down_snapshots"] == 1
    assert audit["observation_gap"] is True


def test_summarize_signal_audit_counts_runtime_hold_promotions() -> None:
    window_start = datetime(2026, 3, 18, 23, 5, tzinfo=timezone.utc)
    market_ts = window_start + timedelta(minutes=5)
    audit = _summarize_signal_audit(
        symbol="KRW-BTC",
        window_start=window_start,
        window_end=window_start + timedelta(hours=1),
        market_rows=[
            {
                "ts": market_ts,
                "signal": "HOLD",
                "decision_id": "d-2",
                "raw_payload": {
                    "signal": "HOLD",
                    "entry_allowed": False,
                    "alpha": 0.82,
                    "predicted_after_cost_bps": 1.75,
                    "required_after_cost_bps": 4.0,
                    "after_cost_uncertainty_bps": 2.0,
                    "reason_codes": ["RG_EDGE_TOO_LOW"],
                },
            }
        ],
        safe_rows=[
            {
                "ts": market_ts,
                "decision_id": "d-2",
                "action": "BUY",
                "selected_reasons": ["RG_CAP_PROMOTED"],
                "gates": {
                    "micro_mode_runtime_hold_entry_allowed": True,
                },
            }
        ],
        orchestrator_rows=[],
        rules_raw={
            "scheduling": {"decision_interval_sec": 30},
            "governance": {
                "micro_mode": {
                    "min_alpha": 0.65,
                    "live_profit_floor_bps": 1.0,
                    "live_profit_required_margin_bps": 0.5,
                    "live_max_uncertainty_bps": 8.0,
                }
            },
        },
    )

    assert audit["market_buy_signal_count"] == 0
    assert audit["alpha_entry_hits"] == 1
    assert audit["profit_watch_hits"] == 1
    assert audit["profit_watch_promoted"] == 1
    assert audit["profit_watch_blocked"] == 0
    assert audit["safe_buy_count"] == 1


def test_render_signal_audit_notes_and_followup_tasks() -> None:
    out = run_governance_protocol(fact_pack=_base_fact_pack(), rules_raw={})
    signal_audit = {
        "window_start_kst": "2026-03-19T08:05:00+09:00",
        "window_end_kst": "2026-03-19T10:05:00+09:00",
        "market_sample_count": 180,
        "market_buy_signal_count": 3,
        "alpha_entry_hits": 2,
        "profit_watch_hits": 2,
        "profit_watch_promoted": 1,
        "profit_watch_blocked": 1,
        "safe_buy_count": 1,
        "runtime_down_snapshots": 2,
        "observation_gap": True,
        "max_observed_gap_min": 17.5,
        "alpha_threshold": 0.75,
        "profit_floor_bps": 1.0,
        "profit_required_margin_bps": 0.5,
        "max_uncertainty_bps": 8.0,
        "blocked_reason_counts": {"RG_MICRO_BLOCKED_POLICY": 1},
    }

    notes = _render_signal_audit_notes(signal_audit)
    tasks = _build_agent_tasks(
        slot_key="2026-03-19 10:05",
        outputs=out,
        rules_raw={},
        signal_audit=signal_audit,
    )
    task_types = [str(task.get("task_type")) for task in tasks]

    assert "[btc_signal_audit]" in notes
    assert "watch_rule=alpha>=0.75" in notes
    assert "blocked_reasons=RG_MICRO_BLOCKED_POLICY:1" in notes
    assert "BTC_SIGNAL_AUDIT" in task_types
    assert "RUNTIME_COVERAGE_AUDIT" in task_types
