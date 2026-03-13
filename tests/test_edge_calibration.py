from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ai_invest.runtime.edge_calibration import (
    build_edge_calibration_dataset,
    evaluate_edge_calibration,
    load_edge_calibration_config,
    resolve_effective_cap_min_alpha,
)


def _sample_rows(*, n: int = 32):
    now = datetime(2026, 3, 12, 6, 0, tzinfo=timezone.utc)
    events = []
    outcomes = []
    trades = []
    for idx in range(n):
        positive_cluster = idx < (n // 2)
        alpha = 0.34 + (idx * 0.004) if positive_cluster else 0.12 + (idx * 0.002)
        spread = 1.8 if positive_cluster else 4.6
        atr = 0.09 if positive_cluster else 0.04
        dv = 0.8 if positive_cluster else -0.4
        pnl_bps = 14.0 + idx * 0.2 if positive_cluster else -18.0 + idx * 0.1
        decision_id = f"dec-{idx}"
        trade_id = f"trade-{idx}"
        ts = now - timedelta(hours=idx + 1)
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
                    "reason": {
                        "atr_pct": atr,
                        "dv_zscore": dv,
                    },
                }
            },
            "decision": {
                "gates": {
                    "spread_bps": spread,
                }
            },
        }
        events.append(
            {
                "event_type": "SAFE_DECISION",
                "entity_id": decision_id,
                "ts": ts,
                "payload": payload,
            }
        )
        outcomes.append(
            {
                "decision_id": decision_id,
                "trade_id": trade_id,
                "symbol": "KRW-BTC",
                "ts_close": ts + timedelta(minutes=45),
                "outcome_label": "WIN" if pnl_bps > 0 else "LOSS",
            }
        )
        trades.append(
            {
                "trade_id": trade_id,
                "symbol": "KRW-BTC",
                "ts_close": ts + timedelta(minutes=45),
                "pnl_bps": pnl_bps,
            }
        )
    return now, events, outcomes, trades


def test_edge_calibration_builds_similarity_model() -> None:
    now, events, outcomes, trades = _sample_rows()
    dataset = build_edge_calibration_dataset(
        events=events,
        outcomes=outcomes,
        trades=trades,
        symbol="KRW-BTC",
        now_utc=now,
        rules_raw={"runtime_calibration": {"enabled": True, "min_samples": 12}},
    )

    assert dataset.enabled is True
    assert dataset.sample_count == len(trades)
    assert dataset.alpha_entry_threshold < 0.50
    assert dataset.alpha_promotion_threshold < 0.60

    view = evaluate_edge_calibration(
        dataset=dataset,
        alpha_raw=0.39,
        spread_bps=1.7,
        atr_pct=0.10,
        dv_zscore=0.9,
        regime="TREND",
        strategy_tag="MOM",
        current_expected_cost_bps=11.2,
    )

    assert view["enabled"] is True
    assert view["predicted_after_cost_bps"] > 0.0
    assert view["required_after_cost_bps"] < view["predicted_after_cost_bps"]
    assert resolve_effective_cap_min_alpha(
        configured_min_alpha=0.75,
        edge_calibration=dataset.as_runtime_summary(),
    ) < 0.75


def test_edge_calibration_disables_when_samples_are_too_small() -> None:
    now, events, outcomes, trades = _sample_rows(n=8)
    dataset = build_edge_calibration_dataset(
        events=events,
        outcomes=outcomes,
        trades=trades,
        symbol="KRW-BTC",
        now_utc=now,
        rules_raw={"runtime_calibration": {"enabled": True, "min_samples": 12}},
    )

    assert dataset.enabled is False
    assert dataset.sample_count == 0


def test_edge_calibration_config_defaults_are_reasonable() -> None:
    cfg = load_edge_calibration_config(rules_raw={})
    assert cfg["enabled"] is True
    assert cfg["min_samples"] >= 8
    assert cfg["alpha_entry_percentile"] < cfg["alpha_promotion_percentile"]
