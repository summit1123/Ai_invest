from __future__ import annotations

from ai_invest.meetings.governance_meeting import run_governance_protocol


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
