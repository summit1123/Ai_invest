from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from ai_invest.execution.live_sync import extract_live_symbol_state, sync_symbol_account_state
from ai_invest.storage.postgres import DbEvent, DbLedgerEntry, DbPosition


class _FakeClient:
    def __init__(self, accounts: list[dict[str, object]]) -> None:
        self._accounts = accounts

    def get_accounts(self) -> list[dict[str, object]]:
        return [dict(x) for x in self._accounts]


class _FakeRepo:
    def __init__(self) -> None:
        self.ledger: list[DbLedgerEntry] = []
        self.positions: dict[str, DbPosition] = {}
        self.events: list[DbEvent] = []

    def fetch_cash_balance(self, *, currency: str) -> float:
        ccy = str(currency).upper()
        total = 0.0
        for e in self.ledger:
            if str(e.currency).upper() != ccy:
                continue
            total += float(e.amount) - float(e.fee_amount or 0.0)
        return total

    def insert_ledger_entry(self, entry: DbLedgerEntry) -> None:
        self.ledger.append(entry)

    def fetch_position(self, symbol: str) -> DbPosition | None:
        return self.positions.get(symbol)

    def upsert_position(self, pos: DbPosition) -> None:
        self.positions[pos.symbol] = pos

    def insert_event(self, event: DbEvent) -> None:
        self.events.append(event)


class LiveSyncTests(unittest.TestCase):
    def test_extract_live_symbol_state(self) -> None:
        state = extract_live_symbol_state(
            symbol="KRW-BTC",
            accounts=[
                {"currency": "KRW", "balance": "100000", "locked": "5000"},
                {"currency": "BTC", "balance": "0.001", "locked": "0.0002", "avg_buy_price": "120000000"},
            ],
        )
        self.assertEqual(state.quote_currency, "KRW")
        self.assertAlmostEqual(state.quote_balance_available, 100000.0, places=6)
        self.assertAlmostEqual(state.quote_balance_locked, 5000.0, places=6)
        self.assertAlmostEqual(state.base_qty_total, 0.0012, places=9)
        self.assertAlmostEqual(float(state.base_avg_buy_price or 0.0), 120000000.0, places=6)

    def test_sync_writes_adjustment_and_position(self) -> None:
        repo = _FakeRepo()
        repo.insert_ledger_entry(
            DbLedgerEntry(
                entry_id=uuid.uuid4(),
                ts=datetime.now(timezone.utc),
                entry_type="DEPOSIT",
                symbol=None,
                currency="KRW",
                amount=10000.0,
                price=None,
                fee_amount=None,
                fee_currency=None,
                order_id=None,
                fill_id=None,
                meta={},
            )
        )
        client = _FakeClient(
            accounts=[
                {"currency": "KRW", "balance": "15000", "locked": "0"},
                {"currency": "BTC", "balance": "0.0004", "locked": "0.0001", "avg_buy_price": "100000000"},
            ]
        )
        state = sync_symbol_account_state(
            repo=repo,  # type: ignore[arg-type]
            client=client,  # type: ignore[arg-type]
            symbol="KRW-BTC",
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
        )
        self.assertAlmostEqual(state.quote_balance_available, 15000.0, places=6)
        self.assertGreaterEqual(len(repo.ledger), 2)  # existing deposit + adjustment
        pos = repo.fetch_position("KRW-BTC")
        self.assertIsNotNone(pos)
        assert pos is not None
        self.assertAlmostEqual(pos.qty, 0.0005, places=9)
        self.assertAlmostEqual(float(pos.avg_entry_price or 0.0), 100000000.0, places=6)
        self.assertTrue(any(e.event_type == "LIVE_ACCOUNT_SYNC" for e in repo.events))


if __name__ == "__main__":
    unittest.main()
