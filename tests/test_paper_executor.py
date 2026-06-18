from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from ai_invest.config.rules_loader import load_rules
from ai_invest.execution.paper_execution import PaperExecutor
from ai_invest.market_data.upbit_public import MarketSnapshot
from ai_invest.storage.postgres import DbExecutionMetric, DbFill, DbLedgerEntry, DbOrder, DbPosition


class _FakeRepo:
    def __init__(self) -> None:
        self.orders: list[DbOrder] = []
        self.fills: list[DbFill] = []
        self.ledger: list[DbLedgerEntry] = []
        self.positions: dict[str, DbPosition] = {}
        self.realized_trades: list[dict[str, object]] = []
        self.pnl_daily_deltas: list[dict[str, object]] = []
        self.exec_metrics: list[DbExecutionMetric] = []
        self.events: list[object] = []

    def insert_order(self, order: DbOrder) -> None:
        self.orders.append(order)

    def insert_event(self, event: object) -> None:
        self.events.append(event)

    def update_order_status(self, order_id: str, *, status: str, meta_patch: dict[str, object] | None = None) -> None:  # noqa: ARG002
        return

    def insert_fill(self, fill: DbFill) -> None:
        self.fills.append(fill)

    def insert_ledger_entry(self, entry: DbLedgerEntry) -> None:
        self.ledger.append(entry)

    def paper_seed_exists(self, *, currency: str) -> bool:  # noqa: ARG002
        for e in self.ledger:
            if e.entry_type == "DEPOSIT" and str(e.currency).upper() == str(currency).upper() and (e.meta or {}).get("paper_seed"):
                return True
        return False

    def ensure_paper_seed_cash(self, *, currency: str, amount: float) -> None:
        if self.paper_seed_exists(currency=currency):
            return
        self.insert_ledger_entry(
            DbLedgerEntry(
                entry_id=uuid.uuid4(),
                ts=datetime.now(timezone.utc),
                entry_type="DEPOSIT",
                symbol=None,
                currency=str(currency).upper(),
                amount=float(amount),
                price=None,
                fee_amount=None,
                fee_currency=None,
                order_id=None,
                fill_id=None,
                meta={"paper": True, "paper_seed": True},
            )
        )

    def fetch_cash_balance(self, *, currency: str) -> float:
        ccy = str(currency).upper()
        total = 0.0
        for e in self.ledger:
            if str(e.currency).upper() != ccy:
                continue
            fee = float(e.fee_amount or 0.0)
            total += float(e.amount) - fee
        return float(total)

    def fetch_position(self, symbol: str) -> DbPosition | None:
        return self.positions.get(symbol)

    def upsert_position(self, pos: DbPosition) -> None:
        self.positions[pos.symbol] = pos

    def insert_realized_trade(self, **kwargs: object) -> None:
        self.realized_trades.append(kwargs)

    def upsert_pnl_daily_delta(
        self, *, day: str, realized_pnl_delta: float, fees_paid_delta: float, trades_count_delta: int
    ) -> None:
        self.pnl_daily_deltas.append(
            {
                "day": day,
                "realized_pnl_delta": realized_pnl_delta,
                "fees_paid_delta": fees_paid_delta,
                "trades_count_delta": trades_count_delta,
            }
        )

    def insert_execution_metric(self, metric: DbExecutionMetric) -> None:
        self.exec_metrics.append(metric)


class PaperExecutorTests(unittest.TestCase):
    def test_executor_noop_on_hold(self) -> None:
        ex = PaperExecutor(repo=object())  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")
        snap = MarketSnapshot(
            ts_ms=0,
            symbol="KRW-BTC",
            last_price=100.0,
            best_bid=99.0,
            best_ask=101.0,
        )
        res = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="HOLD",
            snapshot=snap,
            rules=rules,
        )
        self.assertIsNone(res)

    def test_buy_sell_flow_inserts_ledger_and_closes_trade(self) -> None:
        repo = _FakeRepo()
        ex = PaperExecutor(repo=repo)  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")

        entry_decision_id = uuid.uuid4()
        snap_buy = MarketSnapshot(
            ts_ms=0,
            symbol="KRW-BTC",
            last_price=100.0,
            best_bid=99.0,
            best_ask=101.0,
        )
        res_buy = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=entry_decision_id,
            action="BUY",
            snapshot=snap_buy,
            rules=rules,
        )
        self.assertIsNotNone(res_buy)
        assert res_buy is not None
        self.assertIsNone(res_buy.closed_trade)
        self.assertEqual(res_buy.side, "BUY")
        self.assertEqual(res_buy.entry_decision_id, entry_decision_id)

        # Paper seed(초기 현금) + BUY cashflow 1건
        self.assertGreaterEqual(len(repo.ledger), 2)
        trade_entries = [e for e in repo.ledger if e.entry_type == "TRADE_FILL"]
        self.assertEqual(len(trade_entries), 1)
        entry = trade_entries[0]
        self.assertEqual(entry.entry_type, "TRADE_FILL")
        self.assertEqual(entry.currency, "KRW")
        self.assertLess(entry.amount, 0.0)
        self.assertAlmostEqual(entry.amount, -float(rules.execution.min_order_krw), places=6)
        self.assertEqual(str(entry.meta.get("decision_id")), str(entry_decision_id))
        self.assertEqual(str(entry.meta.get("trade_id")), str(res_buy.trade_id))

        pos = repo.positions.get("KRW-BTC")
        self.assertIsNotNone(pos)
        assert pos is not None
        self.assertGreater(pos.qty, 0.0)
        self.assertEqual(str((pos.meta or {}).get("trade_id")), str(res_buy.trade_id))
        self.assertEqual(str((pos.meta or {}).get("entry_decision_id")), str(entry_decision_id))

        exit_decision_id = uuid.uuid4()
        snap_sell = MarketSnapshot(
            ts_ms=0,
            symbol="KRW-BTC",
            last_price=110.0,
            best_bid=109.0,
            best_ask=111.0,
        )
        res_sell = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=exit_decision_id,
            action="SELL",
            snapshot=snap_sell,
            rules=rules,
        )
        self.assertIsNotNone(res_sell)
        assert res_sell is not None
        self.assertIsNotNone(res_sell.closed_trade)
        assert res_sell.closed_trade is not None
        self.assertEqual(res_sell.side, "SELL")
        self.assertEqual(res_sell.trade_id, res_buy.trade_id)
        self.assertEqual(res_sell.entry_decision_id, entry_decision_id)
        self.assertEqual(res_sell.closed_trade.trade_id, res_buy.trade_id)
        self.assertEqual(res_sell.closed_trade.entry_decision_id, entry_decision_id)
        self.assertEqual(res_sell.closed_trade.exit_decision_id, exit_decision_id)

        # Ledger gets one more TRADE_FILL row (sell cashflow).
        trade_entries2 = [e for e in repo.ledger if e.entry_type == "TRADE_FILL"]
        self.assertEqual(len(trade_entries2), 2)
        exit_entry = trade_entries2[1]
        self.assertGreater(float(exit_entry.amount), 0.0)

        self.assertEqual(len(repo.realized_trades), 1)
        self.assertEqual(str(repo.realized_trades[0]["trade_id"]), str(res_buy.trade_id))

        pos2 = repo.positions.get("KRW-BTC")
        self.assertIsNotNone(pos2)
        assert pos2 is not None
        self.assertEqual(pos2.qty, 0.0)

    def test_buy_uses_target_position_pct_for_incremental_sizing(self) -> None:
        repo = _FakeRepo()
        ex = PaperExecutor(repo=repo)  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")

        # Seed equity ~10,000,000 KRW with existing 1% position.
        repo.ensure_paper_seed_cash(currency="KRW", amount=10_000_000)
        repo.insert_ledger_entry(
            DbLedgerEntry(
                entry_id=uuid.uuid4(),
                ts=datetime.now(timezone.utc),
                entry_type="TRADE_FILL",
                symbol="KRW-BTC",
                currency="KRW",
                amount=-100_000.0,
                price=100.0,
                fee_amount=0.0,
                fee_currency="KRW",
                order_id=None,
                fill_id=None,
                meta={"paper": True, "side": "BUY"},
            )
        )
        repo.upsert_position(
            DbPosition(
                symbol="KRW-BTC",
                ts_updated=datetime.now(timezone.utc),
                qty=1000.0,
                avg_entry_price=100.0,
                unrealized_pnl=None,
                stop_price=None,
                take_profit=None,
                meta={"trade_id": str(uuid.uuid4()), "entry_decision_id": str(uuid.uuid4())},
            )
        )

        snap_buy = MarketSnapshot(
            ts_ms=0,
            symbol="KRW-BTC",
            last_price=100.0,
            best_bid=99.0,
            best_ask=101.0,
        )
        res = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="BUY",
            snapshot=snap_buy,
            rules=rules,
            target_position_pct=3.0,
        )
        self.assertIsNotNone(res)
        assert res is not None
        # 1% -> 3% top-up should be around 2,000 qty at 100 KRW level (allow fee/price tolerance).
        self.assertGreater(res.fill_qty, 1900.0)
        self.assertLess(res.fill_qty, 2100.0)

    def test_learning_mode_rounds_small_buy_up_to_min_order(self) -> None:
        repo = _FakeRepo()
        ex = PaperExecutor(repo=repo)  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")
        repo.ensure_paper_seed_cash(currency="KRW", amount=50_100.0)

        snap_buy = MarketSnapshot(
            ts_ms=0,
            symbol="KRW-BTC",
            last_price=100.0,
            best_bid=100.0,
            best_ask=101.0,
        )
        res = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="BUY",
            snapshot=snap_buy,
            rules=rules,
            target_position_pct=9.77,
            allow_min_order_round_up=True,
        )
        self.assertIsNotNone(res)
        assert res is not None
        expected_notional = float(rules.execution.min_order_krw) * float(
            (rules.raw.get("runtime_controller") or {}).get("min_order_buffer_mult") or 1.0
        )
        self.assertAlmostEqual(float(res.fill_qty), float(expected_notional) / 100.0, places=6)

    def test_position_meta_tracks_strategy_and_cooldown(self) -> None:
        repo = _FakeRepo()
        ex = PaperExecutor(repo=repo)  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")

        snap_buy = MarketSnapshot(
            ts_ms=0,
            symbol="KRW-BTC",
            last_price=100.0,
            best_bid=99.0,
            best_ask=101.0,
        )
        buy = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="BUY",
            snapshot=snap_buy,
            rules=rules,
            strategy_tag="REV",
        )
        self.assertIsNotNone(buy)
        pos = repo.fetch_position("KRW-BTC")
        self.assertIsNotNone(pos)
        assert pos is not None
        self.assertEqual(str((pos.meta or {}).get("strategy_tag")), "REV")
        self.assertIsNotNone((pos.meta or {}).get("entry_ts"))
        self.assertIsNotNone((pos.meta or {}).get("entry_price"))
        self.assertIsNotNone((pos.meta or {}).get("hwm_price"))

        snap_sell = MarketSnapshot(
            ts_ms=0,
            symbol="KRW-BTC",
            last_price=95.0,
            best_bid=94.0,
            best_ask=96.0,
        )
        sell = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="SELL",
            snapshot=snap_sell,
            rules=rules,
            exit_reason="STOP",
            cooldown_minutes=30,
        )
        self.assertIsNotNone(sell)
        pos2 = repo.fetch_position("KRW-BTC")
        self.assertIsNotNone(pos2)
        assert pos2 is not None
        self.assertEqual(pos2.qty, 0.0)
        self.assertEqual(str((pos2.meta or {}).get("last_exit_reason")), "STOP")
        self.assertIsNotNone((pos2.meta or {}).get("cooldown_until"))


if __name__ == "__main__":
    unittest.main()
