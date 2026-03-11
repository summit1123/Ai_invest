from __future__ import annotations

import json
from collections.abc import Sequence

from ai_invest.market_data.upbit_public import MarketSnapshot
from ai_invest.market_data.upbit_ws import UpbitPublicWsSnapshotHub


class _FakeWs:
    def __init__(self, messages: Sequence[object]) -> None:
        self._messages = list(messages)
        self.sent: list[str] = []

    def __enter__(self) -> _FakeWs:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def send(self, payload: str) -> None:
        self.sent.append(str(payload))

    def recv(self, timeout: float | None = None, decode: bool | None = None) -> object:  # noqa: ARG002
        if self._messages:
            item = self._messages.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        raise TimeoutError()


def test_ws_snapshot_hub_builds_snapshot_from_stream(monkeypatch) -> None:
    monkeypatch.setenv("UPBIT_PUBLIC_WS_RECV_TIMEOUT_SEC", "0.05")
    fake = _FakeWs(
        messages=[
            json.dumps(
                {
                    "type": "ticker",
                    "code": "KRW-BTC",
                    "trade_price": 150_000_000.0,
                    "timestamp": 1234567890,
                }
            ),
            json.dumps(
                {
                    "type": "orderbook",
                    "code": "KRW-BTC",
                    "timestamp": 1234567891,
                    "orderbook_units": [
                        {
                            "ask_price": 150_001_000.0,
                            "bid_price": 149_999_000.0,
                        }
                    ],
                }
            ),
        ]
    )
    fallback = MarketSnapshot(
        ts_ms=1,
        symbol="KRW-BTC",
        last_price=1.0,
        best_bid=1.0,
        best_ask=1.0,
    )
    hub = UpbitPublicWsSnapshotHub(
        connect_fn=lambda *args, **kwargs: fake,
        rest_fallback_fn=lambda symbol: fallback,
    )
    try:
        snapshot = hub.get_snapshot("KRW-BTC", wait_timeout_sec=0.5, allow_rest_fallback=False)
        assert snapshot.symbol == "KRW-BTC"
        assert float(snapshot.last_price) == 150_000_000.0
        assert float(snapshot.best_bid) == 149_999_000.0
        assert float(snapshot.best_ask) == 150_001_000.0
        sent = json.loads(fake.sent[0])
        assert any(row.get("type") == "ticker" for row in sent if isinstance(row, dict))
        assert any(row.get("type") == "orderbook" for row in sent if isinstance(row, dict))
    finally:
        hub.close()


def test_ws_snapshot_hub_falls_back_to_rest_when_stream_missing(monkeypatch) -> None:
    monkeypatch.setenv("UPBIT_PUBLIC_WS_RECV_TIMEOUT_SEC", "0.05")
    fake = _FakeWs(messages=[])
    fallback = MarketSnapshot(
        ts_ms=55,
        symbol="KRW-BTC",
        last_price=120_000_000.0,
        best_bid=119_999_000.0,
        best_ask=120_001_000.0,
    )
    hub = UpbitPublicWsSnapshotHub(
        connect_fn=lambda *args, **kwargs: fake,
        rest_fallback_fn=lambda symbol: fallback,
    )
    try:
        snapshot = hub.get_snapshot("KRW-BTC", wait_timeout_sec=0.05, allow_rest_fallback=True)
        assert snapshot == fallback
    finally:
        hub.close()
