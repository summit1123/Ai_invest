from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import requests

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore


class UpbitPublicApiError(RuntimeError):
    pass


UPBIT_REST_BASE_URL = "https://api.upbit.com"
_SESSION = requests.Session()
_LOCAL_LIMIT_LOCK = Lock()
_LOCAL_NEXT_ALLOWED_TS = 0.0


def _env_float(name: str, default: float) -> float:
    try:
        s = str(os.environ.get(name, "")).strip()
        if not s:
            return float(default)
        return float(s)
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        s = str(os.environ.get(name, "")).strip()
        if not s:
            return int(default)
        return int(float(s))
    except Exception:
        return int(default)


_MIN_INTERVAL_SEC = max(0.0, _env_float("UPBIT_PUBLIC_MIN_INTERVAL_SEC", 0.12))
_MAX_RETRIES = max(0, _env_int("UPBIT_PUBLIC_MAX_RETRIES", 4))
_BACKOFF_BASE_SEC = max(0.05, _env_float("UPBIT_PUBLIC_BACKOFF_BASE_SEC", 0.35))
_BACKOFF_MAX_SEC = max(_BACKOFF_BASE_SEC, _env_float("UPBIT_PUBLIC_BACKOFF_MAX_SEC", 5.0))
_BACKOFF_JITTER_SEC = max(0.0, _env_float("UPBIT_PUBLIC_BACKOFF_JITTER_SEC", 0.15))
_RATE_LIMIT_FILE = Path(os.environ.get("UPBIT_PUBLIC_RATE_LIMIT_FILE", "/tmp/ai_invest_upbit_public_rate_limit.txt"))


def _next_retry_delay_sec(*, attempt: int, retry_after_header: str | None) -> float:
    retry_after = None
    if retry_after_header:
        try:
            retry_after = max(0.0, float(str(retry_after_header).strip()))
        except Exception:
            retry_after = None
    exp = min(_BACKOFF_MAX_SEC, _BACKOFF_BASE_SEC * (2 ** max(0, int(attempt))))
    jitter = random.uniform(0.0, _BACKOFF_JITTER_SEC) if _BACKOFF_JITTER_SEC > 0 else 0.0
    candidate = exp + jitter
    if retry_after is not None:
        candidate = max(candidate, retry_after)
    return min(_BACKOFF_MAX_SEC, candidate)


def _rate_limit_wait() -> None:
    if _MIN_INTERVAL_SEC <= 0:
        return

    # Cross-process throttle via lock-file (best effort; falls back to in-process).
    if fcntl is not None:
        try:
            _RATE_LIMIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            with _RATE_LIMIT_FILE.open("a+", encoding="utf-8") as fp:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
                fp.seek(0)
                raw = fp.read().strip()
                last_ts = float(raw) if raw else 0.0
                now = time.monotonic()
                wait = max(0.0, (last_ts + _MIN_INTERVAL_SEC) - now)
                if wait > 0:
                    time.sleep(wait)
                now2 = time.monotonic()
                fp.seek(0)
                fp.truncate()
                fp.write(f"{now2:.6f}")
                fp.flush()
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            return
        except Exception:
            pass

    global _LOCAL_NEXT_ALLOWED_TS
    with _LOCAL_LIMIT_LOCK:
        now = time.monotonic()
        wait = max(0.0, _LOCAL_NEXT_ALLOWED_TS - now)
        if wait > 0:
            time.sleep(wait)
        _LOCAL_NEXT_ALLOWED_TS = time.monotonic() + _MIN_INTERVAL_SEC


def _get(path: str, params: dict[str, Any], *, timeout_sec: int = 10) -> Any:
    url = f"{UPBIT_REST_BASE_URL}{path}"
    last_error = ""
    for attempt in range(_MAX_RETRIES + 1):
        _rate_limit_wait()
        resp: requests.Response | None = None
        try:
            resp = _SESSION.get(url, params=params, timeout=timeout_sec)
        except requests.RequestException as exc:
            last_error = f"request_error: {exc}"
        else:
            if resp.ok:
                try:
                    return resp.json()
                except Exception as exc:
                    raise UpbitPublicApiError(f"Upbit public API invalid JSON: {exc}") from exc
            last_error = f"status={resp.status_code}, body={resp.text[:200]}"
            if int(resp.status_code) not in {429, 500, 502, 503, 504}:
                raise UpbitPublicApiError(f"Upbit public API error: {last_error}")

        if attempt >= _MAX_RETRIES:
            break
        retry_after = str(resp.headers.get("Retry-After") or "").strip() if resp is not None else ""
        time.sleep(_next_retry_delay_sec(attempt=attempt, retry_after_header=(retry_after or None)))

    raise UpbitPublicApiError(f"Upbit public API error: {last_error}")


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
