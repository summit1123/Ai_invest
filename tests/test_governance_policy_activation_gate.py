from __future__ import annotations

from ai_invest.meetings.governance_meeting import evaluate_policy_activation_gate


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


def test_policy_activation_gate_blocks_when_backtest_missing():
    out = evaluate_policy_activation_gate(
        rules_raw={"governance": {"activation_gate": {"enabled": True}}},
        fact_pack={"prework_reports": {}},
        final_symbol="KRW-BTC",
    )
    assert out["enabled"] is True
    assert out["passed"] is False
    assert out["reason_code"] == "POLICY_GATE_BACKTEST_MISSING"


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
