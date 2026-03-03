from __future__ import annotations

from ai_invest.market_data import macro as mm


class _Resp:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = int(status_code)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http_error:{self.status_code}")

    def json(self) -> dict:
        return dict(self._payload)


def test_fetch_macro_context_ok(monkeypatch):
    def _fake_get(url: str, headers: dict, timeout: int):  # noqa: ARG001
        if "alternative.me" in str(url):
            return _Resp(
                {
                    "name": "Fear and Greed Index",
                    "data": [
                        {
                            "value": "22",
                            "value_classification": "Fear",
                            "timestamp": "1762041600",
                        }
                    ],
                }
            )
        if "coingecko.com" in str(url):
            return _Resp(
                {
                    "data": {
                        "market_cap_percentage": {"btc": 58.4, "eth": 11.2},
                        "total_market_cap": {"usd": 1_000_000_000},
                        "total_volume": {"usd": 100_000_000},
                        "market_cap_change_percentage_24h_usd": 1.1,
                    }
                }
            )
        raise RuntimeError("unexpected_url")

    monkeypatch.setattr(mm.requests, "get", _fake_get)
    out = mm.fetch_macro_context(timeout_sec=3)
    assert str(out.get("status")) == "OK"
    assert str(out.get("risk_mode")) == "RISK_OFF"
    fg = dict(out.get("fear_greed_index") or {})
    cm = dict(out.get("crypto_market") or {})
    assert int(fg.get("value")) == 22
    assert float(cm.get("btc_dominance_pct")) == 58.4


def test_fetch_macro_context_partial_when_one_source_fails(monkeypatch):
    def _fake_get(url: str, headers: dict, timeout: int):  # noqa: ARG001
        if "alternative.me" in str(url):
            raise RuntimeError("network_down")
        if "coingecko.com" in str(url):
            return _Resp(
                {
                    "data": {
                        "market_cap_percentage": {"btc": 52.1},
                        "total_market_cap": {"usd": 1_500_000_000},
                        "total_volume": {"usd": 120_000_000},
                        "market_cap_change_percentage_24h_usd": -0.7,
                    }
                }
            )
        raise RuntimeError("unexpected_url")

    monkeypatch.setattr(mm.requests, "get", _fake_get)
    out = mm.fetch_macro_context(timeout_sec=3)
    assert str(out.get("status")) == "PARTIAL"
    errs = list(out.get("errors") or [])
    assert len(errs) == 1
    assert "fear_greed_fetch_failed" in str(errs[0])

