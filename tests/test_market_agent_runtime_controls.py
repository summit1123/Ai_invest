from __future__ import annotations

from types import SimpleNamespace

from ai_invest.agents import market_agent as ma
from ai_invest.config.rules_loader import load_rules
from ai_invest.domain.reason_codes import ReasonCode


def _payload(*, runtime_controls: dict | None = None) -> dict:
    return {
        "timestamp_utc": "2026-03-10T02:00:00Z",
        "snapshot": {
            "last_price": 100.0,
            "best_bid": 99.9,
            "best_ask": 100.1,
            "mid_price": 100.0,
            "spread_bps": 2.0,
        },
        "features": {
            "atr_pct": 0.6,
            "rsi_14": 55.0,
            "rsi_14_prev": 31.0,
            "ema20": 101.0,
            "ema60": 100.0,
            "vol_zscore": 1.6,
            "dv_zscore": 1.2,
            "ret_15m": -0.009,
            "ret_60m": 0.004,
        },
        "ops": {
            "pause_state": False,
            "reconciliation_status": "OK",
            "rate_limit_alert": False,
        },
        "context": {
            "account": {
                "daily_loss_pct": 0.0,
                "daily_trades_count": 0,
                "equity_krw": 50_000.0,
            },
            "position": {"current_qty": 0.0},
            "position_state": {},
            "trade_plan": {},
            "learning_feedback": {},
            "runtime_controls": dict(runtime_controls or {}),
            "research_signal": {},
        },
    }


def test_runtime_controls_block_reversal_entry_under_news_shock(monkeypatch) -> None:
    rules = load_rules("rules.yaml")
    monkeypatch.setattr(
        ma,
        "compute_alpha_score",
        lambda features, cfg: SimpleNamespace(
            alpha=0.82,
            alpha_raw=0.82,
            mom_s=0.20,
            rev_s=0.90,
            strength=0.8,
            vol_scale=1.0,
            strategy_tag_candidate="REV",
            signal_target_pct=20.0,
            regime="RANGE",
            trend_strength=0.2,
            shock_strength=0.1,
        ),
    )

    op = ma.market_agent_opine(
        _payload(
            runtime_controls={
                "buy_enabled": True,
                "allow_reversal_entries": False,
                "mode": "DEFENSIVE",
                "news_shock_score": 0.72,
            }
        ),
        rules=rules,
    )

    assert op.signal == "HOLD"
    assert list(op.reason_codes) == [ReasonCode.RG_NEWS_RISK.value]


def test_runtime_controls_make_small_account_target_actionable(monkeypatch) -> None:
    rules = load_rules("rules.yaml")
    monkeypatch.setattr(
        ma,
        "compute_alpha_score",
        lambda features, cfg: SimpleNamespace(
            alpha=0.88,
            alpha_raw=0.88,
            mom_s=0.88,
            rev_s=0.0,
            strength=0.9,
            vol_scale=1.0,
            strategy_tag_candidate="MOM",
            signal_target_pct=12.0,
            regime="TREND",
            trend_strength=0.9,
            shock_strength=0.1,
        ),
    )

    op = ma.market_agent_opine(
        _payload(
            runtime_controls={
                "buy_enabled": True,
                "target_scale": 0.9,
                "max_position_pct": 25.0,
                "actionable_target_floor_pct": 20.0,
                "actionable_floor_alpha_margin": 0.05,
                "mode": "LIMITED",
            }
        ),
        rules=rules,
    )

    assert op.signal == "LONG"
    assert float(op.signal_target_pct) >= 20.0
