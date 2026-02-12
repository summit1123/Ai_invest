from __future__ import annotations

from ai_invest.meetings.governance_meeting import (
    _activation_decision_from_gate,
    _hard_plan_block_from_fact_pack,
    evaluate_policy_activation_gate,
)


def _fact_pack_with_backtest() -> dict:
    return {
        "prework_reports": {
            "quant_strategist": {
                "findings": {
                    "backtest": {
                        "ranked": [
                            {
                                "symbol": "KRW-BTC",
                                "trades": 12,
                                "win_rate_pct": 54.0,
                                "backtest_score": 7.5,
                                "max_drawdown_pct": 11.2,
                                "profit_factor": 1.2,
                                "expectancy_after_cost_pct": 0.2,
                            }
                        ]
                    }
                }
            }
        }
    }


def test_policy_activation_gate_passes_when_thresholds_met():
    out = evaluate_policy_activation_gate(
        rules_raw={"governance": {"activation_gate": {"enabled": True}}},
        fact_pack=_fact_pack_with_backtest(),
        final_symbol="KRW-BTC",
    )
    assert out["enabled"] is True
    assert out["passed"] is True
    assert out["reason_code"] == "POLICY_GATE_PASS"
    assert out["decision"] == "LIVE"


def test_policy_activation_gate_blocks_when_backtest_missing():
    out = evaluate_policy_activation_gate(
        rules_raw={"governance": {"activation_gate": {"enabled": True}}},
        fact_pack={"prework_reports": {}},
        final_symbol="KRW-BTC",
    )
    assert out["enabled"] is True
    assert out["passed"] is False
    assert out["reason_code"] == "POLICY_GATE_BACKTEST_MISSING"
    assert out["decision"] == "PAPER"


def test_policy_activation_gate_blocks_when_score_below_threshold():
    fp = _fact_pack_with_backtest()
    fp["prework_reports"]["quant_strategist"]["findings"]["backtest"]["ranked"][0]["backtest_score"] = -2.0
    out = evaluate_policy_activation_gate(
        rules_raw={"governance": {"activation_gate": {"enabled": True, "min_backtest_score": 0.0}}},
        fact_pack=fp,
        final_symbol="KRW-BTC",
    )
    assert out["enabled"] is True
    assert out["passed"] is False
    assert out["reason_code"] == "POLICY_GATE_BLOCKED"
    assert out["decision"] == "PAPER"


def test_policy_activation_gate_returns_insufficient_data_in_paper_mode():
    fp = _fact_pack_with_backtest()
    fp["prework_reports"]["quant_strategist"]["findings"]["backtest"]["ranked"][0]["trades"] = 5
    out = evaluate_policy_activation_gate(
        rules_raw={
            "universe": {"mode": "paper"},
            "governance": {"activation_gate": {"enabled": True, "min_backtest_trades": 3}},
            "paper_mode": {"data_collection": {"enabled": True, "min_trades_for_strict_gate": 30}},
        },
        fact_pack=fp,
        final_symbol="KRW-BTC",
    )
    assert out["enabled"] is True
    assert out["passed"] is False
    assert out["reason_code"] == "POLICY_GATE_INSUFFICIENT_DATA"
    assert out["paper_data_collection_mode"] is True
    assert out["decision"] == "PAPER"


def test_policy_activation_gate_relaxed_or_passes_in_paper_mode():
    fp = _fact_pack_with_backtest()
    row = fp["prework_reports"]["quant_strategist"]["findings"]["backtest"]["ranked"][0]
    row["trades"] = 40
    row["win_rate_pct"] = 20.0
    row["backtest_score"] = -3.0
    row["profit_factor"] = 1.12
    row["expectancy_after_cost_pct"] = -0.05
    out = evaluate_policy_activation_gate(
        rules_raw={
            "universe": {"mode": "paper"},
            "governance": {"activation_gate": {"enabled": True, "min_backtest_trades": 3, "max_drawdown_pct": 25.0}},
            "paper_mode": {
                "data_collection": {
                    "enabled": True,
                    "min_trades_for_strict_gate": 30,
                    "relaxed_min_win_rate_pct": 25.0,
                    "relaxed_min_backtest_score": -1.5,
                    "relaxed_min_profit_factor": 1.05,
                    "relaxed_min_expectancy_pct": 0.0,
                }
            },
        },
        fact_pack=fp,
        final_symbol="KRW-BTC",
    )
    assert out["enabled"] is True
    assert out["passed"] is True
    assert out["reason_code"] == "POLICY_GATE_PASS"
    assert out["decision"] == "LIVE"


def test_activation_decision_paper_when_insufficient_data_without_hard_block():
    gate = {"reason_code": "POLICY_GATE_INSUFFICIENT_DATA"}
    out = _activation_decision_from_gate(activation_gate=gate, hard_plan_block=False)
    assert out == "PAPER"


def test_activation_decision_hold_when_hard_block_even_if_insufficient_data():
    gate = {"reason_code": "POLICY_GATE_INSUFFICIENT_DATA"}
    out = _activation_decision_from_gate(activation_gate=gate, hard_plan_block=True)
    assert out == "HOLD"


def test_hard_plan_block_from_fact_pack_only_for_pause_or_recon_fail():
    blocked, reasons = _hard_plan_block_from_fact_pack(
        fact_pack={
            "ops_state": {
                "pause": {"paused": True},
                "latest_reconciliation": {"status": "OK"},
            }
        }
    )
    assert blocked is True
    assert "pause_state=true" in reasons

    blocked2, reasons2 = _hard_plan_block_from_fact_pack(
        fact_pack={
            "ops_state": {
                "pause": {"paused": False},
                "latest_reconciliation": {"status": "FAIL"},
            }
        }
    )
    assert blocked2 is True
    assert "reconciliation_status=FAIL" in reasons2

    blocked3, reasons3 = _hard_plan_block_from_fact_pack(
        fact_pack={
            "ops_state": {
                "pause": {"paused": False},
                "latest_reconciliation": {"status": "OK"},
            }
        }
    )
    assert blocked3 is False
    assert reasons3 == []
