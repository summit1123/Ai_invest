from __future__ import annotations

from ai_invest.meetings.governance_meeting import (
    _activation_hold_mode,
    _build_inputs_hash_payload,
    _initial_cap_runtime,
    _normalized_conditional_activation_config,
    _should_enable_inter_slot_realtime_mode,
    _stable_hash,
)
from ai_invest.runtime.paper_loop import _cap_required_passes


def test_cap_required_passes_rounds_up_by_loop_interval():
    assert _cap_required_passes(sustain_seconds=180, loop_interval_seconds=15) == 12
    assert _cap_required_passes(sustain_seconds=181, loop_interval_seconds=15) == 13
    assert _cap_required_passes(sustain_seconds=10, loop_interval_seconds=15) == 1


def test_cap_default_config_and_runtime_seed():
    cfg = _normalized_conditional_activation_config(rules_raw={})
    assert cfg["enabled"] is True
    assert cfg["auto_promote_to"] == "PAPER"
    assert cfg["conditions"]["min_alpha"] == 0.75
    assert cfg["conditions"]["sustain_seconds"] == 180
    assert cfg["promotion"]["target_position_pct_cap"] == 3.0

    hold_mode = _activation_hold_mode(
        activation_decision_effective="HOLD",
        conditional_activation=cfg,
    )
    assert hold_mode == "HOLD_CONDITIONAL"

    runtime_seed = _initial_cap_runtime(conditional_activation=cfg, decision_interval_sec=15)
    assert runtime_seed["required_passes"] == 12
    assert runtime_seed["consecutive_passes"] == 0


def test_inter_slot_realtime_mode_only_enabled_for_live_conditional_hold():
    cfg = _normalized_conditional_activation_config(rules_raw={"governance": {"activation_gate": {"conditional_activation": {"enabled": True}}}})

    assert _should_enable_inter_slot_realtime_mode(
        universe_mode="live",
        live_execution_enabled=True,
        hard_plan_block=False,
        final_plan_no_trade=False,
        activation_decision_effective="PAPER",
        conditional_activation=cfg,
    ) is True
    assert _should_enable_inter_slot_realtime_mode(
        universe_mode="paper",
        live_execution_enabled=True,
        hard_plan_block=False,
        final_plan_no_trade=False,
        activation_decision_effective="PAPER",
        conditional_activation=cfg,
    ) is False
    assert _should_enable_inter_slot_realtime_mode(
        universe_mode="live",
        live_execution_enabled=True,
        hard_plan_block=True,
        final_plan_no_trade=False,
        activation_decision_effective="PAPER",
        conditional_activation=cfg,
    ) is False


def test_inputs_hash_is_deterministic_with_ordering_noise():
    evaluated_a = [
        {
            "symbol": "KRW-BTC",
            "score": 1.234567,
            "snapshot": {"spread_bps": 1.512345, "ts": "2026-02-12T00:00:00+09:00"},
            "features": {"rsi_14": 54.123456, "vol_zscore": 1.234567, "atr_pct": 0.912345},
        },
        {
            "symbol": "KRW-ETH",
            "score": 0.987654,
            "snapshot": {"spread_bps": 1.7321},
            "features": {"rsi_14": 50.0, "vol_zscore": 0.1234, "atr_pct": 1.2},
        },
    ]
    evaluated_b = list(reversed(evaluated_a))
    checks_a = [
        {"name": "win_rate_pct", "passed": False, "actual": 33.3, "required": 40},
        {"name": "symbol_match", "passed": True, "actual": "KRW-BTC", "required": "KRW-BTC"},
    ]
    checks_b = list(reversed(checks_a))
    cost_model = {
        "fee_total_bps": 10.0,
        "base_slippage_bps": 1.0,
        "spread_penalty_mult": 0.3,
        "low_liquidity_penalty_bps": 1.2,
    }

    hash_a = _stable_hash(
        _build_inputs_hash_payload(
            slot_key="2026-02-12 16:00",
            symbol="KRW-BTC",
            allowed_symbols=["KRW-ETH", "KRW-BTC"],
            evaluated=evaluated_a,
            activation_checks=checks_a,
            cost_model=cost_model,
        )
    )
    hash_b = _stable_hash(
        _build_inputs_hash_payload(
            slot_key="2026-02-12 16:00",
            symbol="KRW-BTC",
            allowed_symbols=["KRW-BTC", "KRW-ETH"],
            evaluated=evaluated_b,
            activation_checks=checks_b,
            cost_model=cost_model,
        )
    )
    assert hash_a == hash_b

    changed_hash = _stable_hash(
        _build_inputs_hash_payload(
            slot_key="2026-02-12 16:00",
            symbol="KRW-BTC",
            allowed_symbols=["KRW-BTC", "KRW-ETH"],
            evaluated=evaluated_b,
            activation_checks=checks_b,
            cost_model={**cost_model, "base_slippage_bps": 1.4},
        )
    )
    assert changed_hash != hash_a
