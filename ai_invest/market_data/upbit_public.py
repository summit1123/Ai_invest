from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests


class UpbitPublicApiError(RuntimeError):
    pass


UPBIT_REST_BASE_URL = "https://api.upbit.com"


def _get(path: str, params: dict[str, Any], *, timeout_sec: int = 10) -> Any:
    url = f"{UPBIT_REST_BASE_URL}{path}"
    resp = requests.get(url, params=params, timeout=timeout_sec)
    if not resp.ok:
        raise UpbitPublicApiError(f"Upbit public API error: status={resp.status_code}, body={resp.text[:200]}")
    return resp.json()


@dataclass(frozen=True)
class MarketSnapshot:
    ts_ms: int
    symbol: str
    last_price: float
    best_bid: float
    best_ask: float

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid <= 0:
            return 0.0
        return (self.best_ask - self.best_bid) / mid * 10000.0


def fetch_orderbook(symbol: str) -> dict[str, Any]:
    data = _get("/v1/orderbook", {"markets": symbol})
    if not isinstance(data, list) or not data:
        raise UpbitPublicApiError("Unexpected orderbook response")
    return data[0]


def fetch_ticker(symbol: str) -> dict[str, Any]:
    data = _get("/v1/ticker", {"markets": symbol})
    if not isinstance(data, list) or not data:
        raise UpbitPublicApiError("Unexpected ticker response")
    return data[0]


def fetch_tickers(symbols: list[str]) -> list[dict[str, Any]]:
    markets = [str(s).strip().upper() for s in list(symbols or []) if str(s).strip()]
    if not markets:
        return []
    data = _get("/v1/ticker", {"markets": ",".join(markets)})
    if not isinstance(data, list):
        raise UpbitPublicApiError("Unexpected ticker response list")
    return [row for row in data if isinstance(row, dict)]


def fetch_markets_all(*, is_details: bool = False) -> list[dict[str, Any]]:
    data = _get("/v1/market/all", {"isDetails": str(bool(is_details)).lower()})
    if not isinstance(data, list):
        raise UpbitPublicApiError("Unexpected market/all response list")
    return [row for row in data if isinstance(row, dict)]


def fetch_market_snapshot(symbol: str) -> MarketSnapshot:
    ob = fetch_orderbook(symbol)
    units = ob.get("orderbook_units") or []
    if not units:
        raise UpbitPublicApiError("Orderbook units empty")
    best_bid = float(units[0]["bid_price"])
    best_ask = float(units[0]["ask_price"])

    ticker = fetch_ticker(symbol)
    last_price = float(ticker.get("trade_price") or ticker.get("opening_price") or (best_bid + best_ask) / 2.0)
    ts_ms = int(ticker.get("timestamp") or ob.get("timestamp") or int(time.time() * 1000))

    return MarketSnapshot(
        ts_ms=ts_ms,
        symbol=symbol,
        last_price=last_price,
        best_bid=best_bid,
        best_ask=best_ask,
    )


def fetch_candles_minutes(symbol: str, *, unit: int, count: int = 200) -> list[dict[str, Any]]:
    data = _get(f"/v1/candles/minutes/{unit}", {"market": symbol, "count": count})
    if not isinstance(data, list):
        raise UpbitPublicApiError("Unexpected candle response")
    # Upbit returns most recent first; reverse to chronological order.
    return list(reversed(data))
