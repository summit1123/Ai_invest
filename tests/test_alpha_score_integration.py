from __future__ import annotations

from ai_invest.agents.market_agent import market_agent_opine
from ai_invest.config.rules_loader import load_rules
from ai_invest.market_data.features import build_alpha_features_from_1m_candles


def _payload_from_candles(*, closes: list[float], pause: bool = False) -> dict:
    highs = [c * 1.002 for c in closes]
    lows = [c * 0.998 for c in closes]
    volumes = [100.0] * (len(closes) - 1) + [400.0]
    f = build_alpha_features_from_1m_candles(
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        ema_fast=20,
        ema_slow=60,
        ret_short_bars=15,
        ret_long_bars=60,
    )
    return {
        "symbol": "KRW-BTC",
        "timestamp_utc": "2026-02-12T00:00:00+00:00",
        "snapshot": {
            "last_price": float(closes[-1]),
            "mid_price": float(closes[-1]),
            "spread_bps": 2.0,
        },
        "features": f,
        "ops": {"rate_limit_alert": False, "reconciliation_status": "OK", "pause_state": bool(pause)},
        "context": {
            "account": {"daily_loss_pct": 0.0},
            "position": {"current_qty": 0.0},
            "position_state": {},
        },
    }


def test_market_agent_mom_entry_on_trend_sequence():
    rules = load_rules("rules.yaml")
    closes = [100.0 + (i * 0.2) for i in range(120)]
    payload = _payload_from_candles(closes=closes)
    op = market_agent_opine(payload, rules=rules)
    assert op.signal == "LONG"
    assert op.entry_allowed is True
    assert op.signal_target_pct > 0
    assert op.alpha >= 0.65


def test_market_agent_rev_entry_on_crash_rebound_sequence():
    rules = load_rules("rules.yaml")
    closes = [100.0] * 80
    c = 100.0
    for _ in range(20):
        c -= 1.5
        closes.append(c)
    for _ in range(9):
        c += 0.2
        closes.append(c)
    c += 5.0
    closes.append(c)
    closes = closes[-120:]
    payload = _payload_from_candles(closes=closes)
    op = market_agent_opine(payload, rules=rules)
    # Extreme crash-rebound with volume spike is treated as SHOCK regime (risk-first hold).
    assert op.signal == "HOLD"
    assert op.entry_allowed is False
    assert op.regime == "SHOCK"
    assert op.rev_s == 1.0
    assert "RG_REGIME_BLOCKED" in list(op.reason_codes)


def test_market_agent_veto_scenario_holds_even_with_signal():
    rules = load_rules("rules.yaml")
    closes = [100.0 + (i * 0.2) for i in range(120)]
    payload = _payload_from_candles(closes=closes, pause=True)
    op = market_agent_opine(payload, rules=rules)
    assert op.signal == "HOLD"
    assert op.entry_allowed is False
    assert op.signal_target_pct == 0.0
