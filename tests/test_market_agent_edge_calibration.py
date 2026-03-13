from __future__ import annotations

from types import SimpleNamespace

import ai_invest.agents.market_agent as ma
from ai_invest.config.rules_loader import load_rules
from ai_invest.runtime.edge_calibration import build_edge_calibration_dataset


def _payload() -> dict:
    return {
        "symbol": "KRW-BTC",
        "timestamp_utc": "2026-03-12T06:00:00+00:00",
        "snapshot": {
            "spread_bps": 1.2,
            "last_price": 100000000.0,
            "mid_price": 100000000.0,
            "best_bid": 99994000.0,
            "best_ask": 100006000.0,
        },
        "features": {
            "atr_pct": 0.08,
            "rsi_14": 58.0,
            "vol_zscore": 1.1,
            "dv_zscore": 0.9,
            "ret_15m": 0.004,
            "ret_60m": 0.011,
            "ema20": 101.0,
            "ema60": 99.0,
            "missing_rate_1m": 0.0,
        },
        "ops": {"rate_limit_alert": False, "reconciliation_status": "OK", "pause_state": False},
        "context": {
            "account": {"daily_loss_pct": 0.0, "daily_trades_count": 0},
            "position": {"current_qty": 0.0},
            "position_state": {},
            "runtime_controls": {
                "buy_enabled": True,
                "target_scale": 1.0,
                "entry_alpha_adj": 0.0,
                "min_edge_bps_adj": 0.0,
                "max_position_pct": 20.0,
                "allow_reversal_entries": True,
                "actionable_target_floor_pct": 0.0,
                "actionable_floor_alpha_margin": 0.05,
                "news_shock_score": 0.0,
                "mode": "NORMAL",
                "reason_codes": ["RG_PASS"],
            },
            "trade_plan": {"execution_plan": {"final_numbers": {}}},
            "learning_feedback": {"enabled": False},
            "research_signal": {"enabled": False},
        },
    }


def _dataset():
    events = []
    outcomes = []
    trades = []
    for idx in range(30):
        alpha = 0.34 + (idx * 0.003)
        decision_id = f"btc-dec-{idx}"
        trade_id = f"btc-trade-{idx}"
        payload = {
            "symbol": "KRW-BTC",
            "decision_id": decision_id,
            "agent_inputs": {
                "market": {
                    "alpha": alpha,
                    "alpha_raw": alpha,
                    "expected_cost_bps": 11.5,
                    "strategy_tag": "MOM",
                    "regime": "TREND",
                    "reason": {"atr_pct": 0.08, "dv_zscore": 0.9},
                }
            },
            "decision": {"gates": {"spread_bps": 1.2}},
        }
        events.append({"event_type": "SAFE_DECISION", "entity_id": decision_id, "ts": f"2026-03-11T{(idx%12):02d}:00:00+00:00", "payload": payload})
        outcomes.append({"decision_id": decision_id, "trade_id": trade_id, "symbol": "KRW-BTC", "ts_close": f"2026-03-11T{(idx%12):02d}:30:00+00:00", "outcome_label": "WIN"})
        trades.append({"trade_id": trade_id, "symbol": "KRW-BTC", "ts_close": f"2026-03-11T{(idx%12):02d}:30:00+00:00", "pnl_bps": 14.0 + idx * 0.1})
    return build_edge_calibration_dataset(
        events=events,
        outcomes=outcomes,
        trades=trades,
        symbol="KRW-BTC",
        rules_raw={"runtime_calibration": {"enabled": True, "min_samples": 12}},
    )


def test_market_agent_uses_calibrated_after_cost_signal(monkeypatch) -> None:
    rules = load_rules("rules.yaml")
    payload = _payload()

    monkeypatch.setattr(
        ma,
        "compute_alpha_score",
        lambda features, cfg: SimpleNamespace(
            alpha=0.43,
            alpha_raw=0.43,
            mom_s=0.41,
            rev_s=0.0,
            strength=0.2,
            vol_scale=1.0,
            signal_target_pct=10.0,
            strategy_tag_candidate="MOM",
            regime="TREND",
            trend_strength=0.42,
            shock_strength=0.02,
        ),
    )

    no_calibration = ma.market_agent_opine(payload, rules=rules)
    assert no_calibration.signal == "HOLD"

    dataset = _dataset()
    payload["context"]["edge_calibration"] = dataset.as_runtime_summary()
    payload["context"]["_edge_calibration_dataset"] = dataset

    calibrated = ma.market_agent_opine(payload, rules=rules)
    assert calibrated.signal == "LONG"
    assert calibrated.entry_allowed is True
    assert calibrated.expected_net_edge_bps > calibrated.min_edge_required_bps
