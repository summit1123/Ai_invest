from __future__ import annotations

from ai_invest.runtime.runtime_controls import build_runtime_controls


def test_runtime_controls_scale_down_on_news_and_losses() -> None:
    out = build_runtime_controls(
        rules_raw={
            "execution": {"min_order_krw": 10_000},
            "governance": {"default_target_position_pct": 100.0},
            "risk": {"max_position_pct_per_symbol": 100.0},
        },
        account={
            "equity_krw": 50_000.0,
            "cash_krw": 50_000.0,
            "daily_loss_pct": 1.1,
            "capital_profile": {"max_target_position_pct": 100.0, "max_position_pct_per_symbol": 100.0},
        },
        risk_limits={"max_daily_loss_pct": 1.5},
        learning_feedback={
            "symbol_profile": {
                "sample_total": 24,
                "trade_stats": {"avg_pnl_bps": -18.0, "win_rate_trades": 0.39},
                "outcome_stats": {
                    "oc_cost_underestimated_ratio": 0.42,
                    "oc_execution_latency_ratio": 0.22,
                },
            }
        },
        research_signal={"shock_score": 0.82, "report_age_minutes": 10.0},
    )

    assert out["buy_enabled"] is False
    assert str(out["mode"]) in {"DEFENSIVE", "PAUSED"}
    assert float(out["target_scale"]) < 0.60
    assert float(out["actionable_target_floor_pct"]) > 0.0


def test_runtime_controls_remain_normal_with_good_feedback() -> None:
    out = build_runtime_controls(
        rules_raw={
            "execution": {"min_order_krw": 10_000},
            "governance": {"default_target_position_pct": 100.0},
            "risk": {"max_position_pct_per_symbol": 100.0},
        },
        account={
            "equity_krw": 500_000.0,
            "cash_krw": 500_000.0,
            "daily_loss_pct": 0.1,
            "capital_profile": {"max_target_position_pct": 100.0, "max_position_pct_per_symbol": 100.0},
        },
        risk_limits={"max_daily_loss_pct": 1.5},
        learning_feedback={
            "symbol_profile": {
                "sample_total": 32,
                "trade_stats": {"avg_pnl_bps": 22.0, "win_rate_trades": 0.61},
                "outcome_stats": {
                    "oc_cost_underestimated_ratio": 0.05,
                    "oc_execution_latency_ratio": 0.04,
                },
            }
        },
        research_signal={"shock_score": 0.10, "report_age_minutes": 20.0},
    )

    assert out["buy_enabled"] is True
    assert str(out["mode"]) == "NORMAL"
    assert float(out["target_scale"]) > 0.60
    assert float(out["entry_alpha_adj"]) < 0.10


def test_runtime_controls_default_min_order_floor_matches_small_account() -> None:
    out = build_runtime_controls(
        rules_raw={
            "governance": {"default_target_position_pct": 20.0},
            "risk": {"max_position_pct_per_symbol": 20.0},
        },
        account={
            "equity_krw": 50_100.0,
            "cash_krw": 50_100.0,
            "daily_loss_pct": 0.0,
            "capital_profile": {"max_target_position_pct": 20.0, "max_position_pct_per_symbol": 20.0},
        },
        risk_limits={"max_daily_loss_pct": 1.5},
        learning_feedback={},
        research_signal={},
    )

    # Default runtime min order should align with Upbit KRW market minimum (5,000 KRW).
    assert 10.0 <= float(out["actionable_target_floor_pct"]) <= 10.3
