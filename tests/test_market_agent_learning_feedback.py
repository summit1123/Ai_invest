from __future__ import annotations

from types import SimpleNamespace

from ai_invest.agents import market_agent as ma
from ai_invest.config.rules_loader import load_rules


def _payload(*, learning_feedback: dict | None = None) -> dict:
    return {
        "timestamp_utc": "2026-02-24T02:00:00Z",
        "snapshot": {
            "last_price": 100.0,
            "best_bid": 99.9,
            "best_ask": 100.1,
            "mid_price": 100.0,
            "spread_bps": 2.0,
        },
        "features": {
            "atr_pct": 0.5,
            "rsi_14": 55.0,
            "ema20": 101.0,
            "ema60": 100.0,
            "vol_zscore": 0.4,
        },
        "ops": {
            "pause_state": False,
            "reconciliation_status": "OK",
            "rate_limit_alert": False,
        },
        "context": {
            "account": {"daily_loss_pct": 0.0},
            "position": {"current_qty": 0.0},
            "position_state": {},
            "trade_plan": {},
            "learning_feedback": dict(learning_feedback or {}),
        },
    }


def test_learning_feedback_can_tighten_entry_alpha(monkeypatch) -> None:
    rules = load_rules("rules.yaml")

    # Keep alpha fixed near threshold so feedback adjustment changes the decision.
    monkeypatch.setattr(
        ma,
        "compute_alpha_score",
        lambda features, cfg: SimpleNamespace(
            alpha=0.84,
            mom_s=0.84,
            rev_s=0.0,
            strength=0.8,
            vol_scale=1.0,
            strategy_tag_candidate="MOM",
            signal_target_pct=2.0,
        ),
    )

    no_feedback = ma.market_agent_opine(_payload(), rules=rules)
    assert no_feedback.signal == "LONG"

    bad_feedback = ma.market_agent_opine(
        _payload(
            learning_feedback={
                "enabled": True,
                "symbol_profile": {
                    "sample_total": 24,
                    "trade_stats": {"avg_pnl_bps": -15.0, "win_rate_trades": 0.35},
                    "outcome_stats": {
                        "oc_cost_underestimated_ratio": 0.55,
                        "oc_execution_latency_ratio": 0.30,
                    },
                },
            }
        ),
        rules=rules,
    )
    assert bad_feedback.signal == "HOLD"
    assert "RG_EDGE_TOO_LOW" in list(bad_feedback.reason_codes)
    assert float(bad_feedback.reason.get("entry_alpha_feedback_adj") or 0.0) > 0.0
