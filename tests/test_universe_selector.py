from __future__ import annotations

from ai_invest.market_data.universe_selector import resolve_dynamic_universe


def test_resolve_dynamic_universe_static_when_disabled() -> None:
    out = resolve_dynamic_universe(
        rules_raw={"universe": {"dynamic": {"enabled": False}}},
        fallback_symbols=["KRW-BTC", "KRW-ETH"],
    )
    assert out.symbols == ["KRW-BTC", "KRW-ETH"]
    assert out.source == "static"
    assert out.ranked_count == 0

