from __future__ import annotations

from ai_invest.research.news_signal import build_news_signal


def test_build_news_signal_detects_high_impact_headlines() -> None:
    signal = build_news_signal(
        headlines=[
            {"title": "Bitcoin exchange hack triggers liquidation fears"},
            {"title": "SEC lawsuit adds regulatory pressure to crypto market"},
        ],
        risk_watchlist=["latency risk elevated"],
    )

    assert signal["enabled"] is True
    assert float(signal["shock_score"]) > 0.45
    assert str(signal["severity"]) in {"ELEVATED", "HIGH"}
    assert int(signal["high_impact_count"]) >= 1


def test_build_news_signal_stays_normal_without_risk_keywords() -> None:
    signal = build_news_signal(
        headlines=[
            {"title": "Bitcoin consolidates as ETF flows remain steady"},
            {"title": "BTC market update with neutral sentiment"},
        ],
        risk_watchlist=["no critical operating issue"],
    )

    assert signal["enabled"] is True
    assert float(signal["shock_score"]) < 0.45
    assert str(signal["severity"]) == "NORMAL"
