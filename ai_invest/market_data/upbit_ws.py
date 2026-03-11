from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from websockets.sync.client import connect

from ai_invest.market_data.upbit_public import MarketSnapshot, UpbitPublicApiError, fetch_market_snapshot


def _env_float(name: str, default: float) -> float:
    try:
        raw = str(os.environ.get(name, "")).strip()
        if not raw:
            return float(default)
        return float(raw)
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "y", "on"}


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, bool):
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        raw = str(value).strip()
        return float(raw) if raw else float(default)
    except Exception:
        return float(default)


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, bool):
            return int(default)
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return int(value)
        raw = str(value).strip()
        return int(float(raw)) if raw else int(default)
    except Exception:
        return int(default)


def _normalize_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    out = sorted({str(s or "").strip().upper() for s in list(symbols or []) if str(s or "").strip()})
    return tuple(out)


class UpbitPublicStreamError(RuntimeError):
    pass


class UpbitPublicWsSnapshotHub:
    """Background WebSocket hub for public ticker/orderbook snapshots.

    - Maintains one background connection for the current symbol set.
    - Builds top-of-book `MarketSnapshot` objects from orderbook+ticker messages.
    - Falls back to REST snapshot fetch if the stream is unavailable or stale.
    """

    def __init__(
        self,
        *,
        ws_url: str | None = None,
        connect_fn: Callable[..., Any] | None = None,
        rest_fallback_fn: Callable[[str], MarketSnapshot] | None = None,
    ) -> None:
        self._ws_url = str(ws_url or os.environ.get("UPBIT_PUBLIC_WS_URL") or "wss://api.upbit.com/websocket/v1")
        self._connect_fn = connect_fn or connect
        self._rest_fallback_fn = rest_fallback_fn or fetch_market_snapshot
        self._stale_after_sec = max(0.5, _env_float("UPBIT_PUBLIC_WS_STALE_SEC", 5.0))
        self._connect_timeout_sec = max(1.0, _env_float("UPBIT_PUBLIC_WS_CONNECT_TIMEOUT_SEC", 8.0))
        self._recv_timeout_sec = max(0.2, _env_float("UPBIT_PUBLIC_WS_RECV_TIMEOUT_SEC", 1.0))
        self._backoff_initial_sec = max(0.1, _env_float("UPBIT_PUBLIC_WS_BACKOFF_INITIAL_SEC", 0.5))
        self._backoff_max_sec = max(self._backoff_initial_sec, _env_float("UPBIT_PUBLIC_WS_BACKOFF_MAX_SEC", 5.0))
        self._ping_interval_sec = max(5.0, _env_float("UPBIT_PUBLIC_WS_PING_INTERVAL_SEC", 20.0))
        self._ping_timeout_sec = max(5.0, _env_float("UPBIT_PUBLIC_WS_PING_TIMEOUT_SEC", 20.0))
        self._enabled = _env_bool("UPBIT_PUBLIC_WS_ENABLED", True)

        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._symbols: tuple[str, ...] = ()
        self._generation = 0
        self._thread: threading.Thread | None = None
        self._started = False
        self._last_error: str | None = None
        self._ticker_rows: dict[str, Mapping[str, Any]] = {}
        self._orderbook_rows: dict[str, Mapping[str, Any]] = {}
        self._snapshots: dict[str, MarketSnapshot] = {}
        self._snapshot_mono: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._enabled)

    def set_symbols(self, symbols: Sequence[str]) -> None:
        if not self._enabled:
            return
        normalized = _normalize_symbols(symbols)
        with self._cond:
            if normalized == self._symbols:
                self._ensure_started_locked()
                return
            self._symbols = normalized
            self._generation += 1
            self._trim_locked(normalized)
            self._ensure_started_locked()
            self._cond.notify_all()

    def get_snapshot(self, symbol: str, *, wait_timeout_sec: float = 2.0, allow_rest_fallback: bool = True) -> MarketSnapshot:
        sym = str(symbol or "").strip().upper()
        if not sym:
            raise UpbitPublicStreamError("symbol is required")
        if not self._enabled:
            return self._rest_fallback_fn(sym)
        with self._cond:
            desired = tuple(sorted(set(self._symbols) | {sym}))
        self.set_symbols(desired)
        deadline = time.monotonic() + max(0.0, float(wait_timeout_sec))
        with self._cond:
            while not self._stop.is_set():
                snapshot = self._snapshots.get(sym)
                ts_mono = self._snapshot_mono.get(sym, 0.0)
                if snapshot is not None and (time.monotonic() - float(ts_mono)) <= float(self._stale_after_sec):
                    return snapshot
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=min(remaining, 0.25))
        if allow_rest_fallback:
            return self._rest_fallback_fn(sym)
        raise UpbitPublicStreamError(f"no fresh websocket snapshot for {sym}")

    def close(self) -> None:
        if not self._enabled:
            return
        self._stop.set()
        with self._cond:
            self._generation += 1
            self._cond.notify_all()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _ensure_started_locked(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(target=self._run, name="upbit-public-ws", daemon=True)
        self._thread.start()

    def _trim_locked(self, symbols: Sequence[str]) -> None:
        keep = set(symbols)
        self._ticker_rows = {k: v for k, v in self._ticker_rows.items() if k in keep}
        self._orderbook_rows = {k: v for k, v in self._orderbook_rows.items() if k in keep}
        self._snapshots = {k: v for k, v in self._snapshots.items() if k in keep}
        self._snapshot_mono = {k: v for k, v in self._snapshot_mono.items() if k in keep}

    def _current_symbols_and_generation(self) -> tuple[tuple[str, ...], int]:
        with self._cond:
            return tuple(self._symbols), int(self._generation)

    def _subscription_payload(self, symbols: Sequence[str]) -> str:
        payload = [
            {"ticket": f"ai-invest-{int(time.time() * 1000)}"},
            {"type": "ticker", "codes": list(symbols), "isOnlyRealtime": True},
            {"type": "orderbook", "codes": list(symbols), "isOnlyRealtime": True},
            {"format": "DEFAULT"},
        ]
        return json.dumps(payload, separators=(",", ":"))

    def _run(self) -> None:
        backoff_sec = float(self._backoff_initial_sec)
        while not self._stop.is_set():
            symbols, generation = self._current_symbols_and_generation()
            if not symbols:
                with self._cond:
                    self._cond.wait(timeout=0.5)
                continue
            try:
                self._recv_loop(symbols=symbols, generation=generation)
                backoff_sec = float(self._backoff_initial_sec)
            except Exception as exc:
                with self._cond:
                    self._last_error = str(exc)[:300]
                    self._cond.notify_all()
                if self._stop.wait(timeout=backoff_sec):
                    break
                backoff_sec = min(float(self._backoff_max_sec), float(backoff_sec) * 2.0)

    def _recv_loop(self, *, symbols: Sequence[str], generation: int) -> None:
        with self._connect_fn(
            self._ws_url,
            open_timeout=float(self._connect_timeout_sec),
            ping_interval=float(self._ping_interval_sec),
            ping_timeout=float(self._ping_timeout_sec),
            close_timeout=2.0,
            max_size=None,
        ) as ws:
            ws.send(self._subscription_payload(symbols))
            with self._cond:
                self._last_error = None
            while not self._stop.is_set():
                current_symbols, current_generation = self._current_symbols_and_generation()
                if current_generation != generation or tuple(current_symbols) != tuple(symbols):
                    return
                try:
                    raw = ws.recv(timeout=float(self._recv_timeout_sec), decode=True)
                except TimeoutError:
                    continue
                if raw is None:
                    continue
                self._handle_message(raw)

    def _handle_message(self, raw: Any) -> None:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8")
        else:
            text = str(raw)
        data = json.loads(text)
        if not isinstance(data, Mapping):
            return
        code = str(data.get("code") or "").strip().upper()
        msg_type = str(data.get("type") or "").strip().lower()
        if not code or msg_type not in {"ticker", "orderbook"}:
            return
        with self._cond:
            if msg_type == "ticker":
                self._ticker_rows[code] = dict(data)
            else:
                self._orderbook_rows[code] = dict(data)
            snapshot = self._compose_snapshot_locked(code)
            if snapshot is not None:
                self._snapshots[code] = snapshot
                self._snapshot_mono[code] = time.monotonic()
                self._cond.notify_all()

    def _compose_snapshot_locked(self, code: str) -> MarketSnapshot | None:
        ob = self._orderbook_rows.get(code)
        if not isinstance(ob, Mapping):
            return None
        units = ob.get("orderbook_units")
        if not isinstance(units, Sequence) or not units:
            return None
        top = units[0]
        if not isinstance(top, Mapping):
            return None
        best_bid = _as_float(top.get("bid_price"), default=0.0)
        best_ask = _as_float(top.get("ask_price"), default=0.0)
        if best_bid <= 0 or best_ask <= 0:
            return None
        ticker = self._ticker_rows.get(code) or {}
        mid = (best_bid + best_ask) / 2.0
        last_price = _as_float(ticker.get("trade_price"), default=mid)
        if last_price <= 0:
            last_price = mid
        ts_ms = _as_int(ticker.get("timestamp"), default=_as_int(ob.get("timestamp"), default=int(time.time() * 1000)))
        return MarketSnapshot(
            ts_ms=int(ts_ms),
            symbol=str(code),
            last_price=float(last_price),
            best_bid=float(best_bid),
            best_ask=float(best_ask),
        )


__all__ = ["UpbitPublicStreamError", "UpbitPublicWsSnapshotHub"]
