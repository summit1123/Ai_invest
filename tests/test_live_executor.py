from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ai_invest.config.rules_loader import load_rules
from ai_invest.execution.live_execution import LiveExecutor
from ai_invest.market_data.upbit_public import MarketSnapshot
from ai_invest.storage.postgres import DbExecutionMetric, DbEvent, DbFill, DbLedgerEntry, DbOrder, DbPosition


class _FakeLiveClient:
    def __init__(self) -> None:
        self.krw_balance = 1_000_000.0
        self.btc_balance = 0.0
        self.btc_avg = 0.0
        self.place_calls = 0
        self.orders: dict[str, dict[str, Any]] = {}

    def get_accounts(self) -> list[dict[str, Any]]:
        return [
            {
                "currency": "KRW",
                "balance": f"{self.krw_balance:.8f}",
                "locked": "0",
                "avg_buy_price": "0",
            },
            {
                "currency": "BTC",
                "balance": f"{self.btc_balance:.12f}",
                "locked": "0",
                "avg_buy_price": f"{self.btc_avg:.8f}",
            },
        ]

    def place_order(
        self,
        *,
        market: str,
        side: str,
        ord_type: str,
        volume: float | None = None,
        price: float | None = None,
        time_in_force: str | None = None,  # noqa: ARG002
        identifier: str | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        self.place_calls += 1
        order_id = str(uuid.uuid4())
        if market != "KRW-BTC":
            raise RuntimeError("unsupported market")
        fee_rate = 0.0005

        if side == "bid":
            if ord_type == "price":
                funds = float(price or 0.0)
                exec_price = 100.0
                qty = funds / exec_price if exec_price > 0 else 0.0
            else:
                qty = float(volume or 0.0)
                exec_price = float(price or 100.0)
                funds = qty * exec_price
            fee = funds * fee_rate
            self.krw_balance -= funds + fee
            prev_qty = self.btc_balance
            self.btc_balance += qty
            if self.btc_balance > 0:
                self.btc_avg = (self.btc_avg * prev_qty + exec_price * qty) / self.btc_balance
        else:
            qty = float(volume or 0.0)
            exec_price = float(price or 110.0)
            qty = min(qty, self.btc_balance)
            funds = qty * exec_price
            fee = funds * fee_rate
            self.btc_balance -= qty
            if self.btc_balance <= 1e-12:
                self.btc_balance = 0.0
                self.btc_avg = 0.0
            self.krw_balance += funds - fee

        payload = {
            "uuid": order_id,
            "state": "done",
            "market": "KRW-BTC",
            "side": side,
            "ord_type": ord_type,
            "price": str(exec_price),
            "volume": str(qty),
            "remaining_volume": "0",
            "executed_volume": str(qty),
            "paid_fee": str(fee),
            "trades": [{"price": str(exec_price), "volume": str(qty), "fee": str(fee)}],
        }
        self.orders[order_id] = payload
        return dict(payload)

    def get_order(self, *, order_id: str | None = None, identifier: str | None = None) -> dict[str, Any]:  # noqa: ARG002
        if not order_id:
            raise RuntimeError("order_id required")
        return dict(self.orders[order_id])

    def cancel_order(self, *, order_id: str | None = None, identifier: str | None = None) -> dict[str, Any]:  # noqa: ARG002
        if not order_id:
            raise RuntimeError("order_id required")
        payload = dict(self.orders[order_id])
        payload["state"] = "cancel"
        return payload


class _FakeRepo:
    def __init__(self) -> None:
        self.orders: dict[str, DbOrder] = {}
        self.fills: list[DbFill] = []
        self.ledger: list[DbLedgerEntry] = []
        self.positions: dict[str, DbPosition] = {}
        self.exec_metrics: list[DbExecutionMetric] = []
        self.events: list[DbEvent] = []
        self.realized_trades: list[dict[str, Any]] = []
        self.pnl_daily: list[dict[str, Any]] = []

    def fetch_open_orders(
        self,
        *,
        symbol: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        wanted_statuses = {str(s).upper() for s in list(statuses or ["NEW", "ACK", "PARTIAL"])}
        rows: list[dict[str, Any]] = []
        for order in self.orders.values():
            if symbol is not None and str(order.symbol).upper() != str(symbol).upper():
                continue
            if str(order.status).upper() not in wanted_statuses:
                continue
            rows.append(
                {
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "status": order.status,
                    "meta": dict(order.meta or {}),
                }
            )
        return rows[: max(1, int(limit))]

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

    def fetch_portfolio_overview(self, *, quote_currency: str = "KRW") -> dict[str, Any]:  # noqa: ARG002
        positions: list[dict[str, Any]] = []
        for pos in self.positions.values():
            if float(pos.qty or 0.0) <= 0.0:
                continue
            positions.append({"symbol": pos.symbol, "qty": float(pos.qty or 0.0)})
        return {"positions_count": len(positions), "positions": positions}

    def insert_event(self, event: DbEvent) -> None:
        self.events.append(event)

    def insert_order(self, order: DbOrder) -> None:
        self.orders[order.order_id] = order

    def update_order_status(self, order_id: str, *, status: str, meta_patch: dict[str, Any] | None = None) -> None:
        if order_id not in self.orders:
            return
        o = self.orders[order_id]
        meta = dict(o.meta or {})
        meta.update(dict(meta_patch or {}))
        self.orders[order_id] = DbOrder(
            order_id=o.order_id,
            ts_created=o.ts_created,
            symbol=o.symbol,
            side=o.side,
            order_type=o.order_type,
            price=o.price,
            quantity=o.quantity,
            time_in_force=o.time_in_force,
            status=status,
            client_order_id=o.client_order_id,
            meta=meta,
            run_id=o.run_id,
            rule_version_id=o.rule_version_id,
        )

    def insert_fill(self, fill: DbFill) -> None:
        self.fills.append(fill)

    def insert_execution_metric(self, metric: DbExecutionMetric) -> None:
        self.exec_metrics.append(metric)

    def insert_realized_trade(self, **kwargs: Any) -> None:
        self.realized_trades.append(kwargs)

    def upsert_pnl_daily_delta(
        self, *, day: str, realized_pnl_delta: float, fees_paid_delta: float, trades_count_delta: int
    ) -> None:
        self.pnl_daily.append(
            {
                "day": day,
                "realized_pnl_delta": realized_pnl_delta,
                "fees_paid_delta": fees_paid_delta,
                "trades_count_delta": trades_count_delta,
            }
        )

    def fetch_pnl_daily(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return list(self.pnl_daily[: max(1, int(limit))])


class LiveExecutorTests(unittest.TestCase):
    def test_live_buy_then_sell_flow(self) -> None:
        repo = _FakeRepo()
        client = _FakeLiveClient()
        ex = LiveExecutor(repo=repo, client=client)  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")

        buy_snap = MarketSnapshot(
            ts_ms=0,
            symbol="KRW-BTC",
            last_price=100.0,
            best_bid=100.0,
            best_ask=101.0,
        )
        buy = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="BUY",
            snapshot=buy_snap,
            rules=rules,
            target_position_pct=10.0,
            strategy_tag="MOM",
        )
        self.assertIsNotNone(buy)
        assert buy is not None
        self.assertGreater(float(buy.fill_qty), 0.0)
        self.assertGreater(len(repo.fills), 0)
        self.assertTrue(any(e.event_type == "ORDER_SUBMITTED" for e in repo.events))
        pos = repo.fetch_position("KRW-BTC")
        self.assertIsNotNone(pos)
        assert pos is not None
        self.assertGreater(float(pos.qty), 0.0)

        sell_snap = MarketSnapshot(
            ts_ms=0,
            symbol="KRW-BTC",
            last_price=110.0,
            best_bid=109.0,
            best_ask=110.0,
        )
        sell = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="SELL",
            snapshot=sell_snap,
            rules=rules,
            exit_reason="SELL_SIGNAL",
            cooldown_minutes=30,
        )
        self.assertIsNotNone(sell)
        assert sell is not None
        self.assertIsNotNone(sell.closed_trade)
        pos2 = repo.fetch_position("KRW-BTC")
        self.assertIsNotNone(pos2)
        assert pos2 is not None
        self.assertAlmostEqual(float(pos2.qty), 0.0, places=9)
        self.assertEqual(len(repo.realized_trades), 1)
        self.assertGreater(len(repo.exec_metrics), 1)

    def test_live_learning_mode_rounds_small_buy_up_to_min_order(self) -> None:
        repo = _FakeRepo()
        client = _FakeLiveClient()
        client.krw_balance = 50_100.0
        ex = LiveExecutor(repo=repo, client=client)  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")

        buy_snap = MarketSnapshot(
            ts_ms=0,
            symbol="KRW-BTC",
            last_price=100.0,
            best_bid=100.0,
            best_ask=101.0,
        )
        buy = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="BUY",
            snapshot=buy_snap,
            rules=rules,
            target_position_pct=9.77,
            allow_min_order_round_up=True,
        )
        self.assertIsNotNone(buy)
        assert buy is not None
        self.assertGreater(float(buy.fill_qty), 0.0)
        order = next(iter(repo.orders.values()))
        expected_notional = float(rules.execution.min_order_krw) * float(
            (rules.raw.get("runtime_controller") or {}).get("min_order_buffer_mult") or 1.0
        )
        self.assertAlmostEqual(float(order.price or 0.0) * float(order.quantity or 0.0), float(expected_notional), delta=1.0)

    def test_live_executor_blocks_new_order_when_symbol_has_open_order(self) -> None:
        repo = _FakeRepo()
        client = _FakeLiveClient()
        ex = LiveExecutor(repo=repo, client=client)  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")
        run_id = uuid.uuid4()
        rule_version_id = uuid.uuid4()
        repo.insert_order(
            DbOrder(
                order_id="existing-open-order",
                ts_created=datetime.now(timezone.utc),
                symbol="KRW-BTC",
                side="BUY",
                order_type="limit",
                price=100.0,
                quantity=1.0,
                time_in_force="post_only",
                status="ACK",
                client_order_id="existing",
                meta={"live": True},
                run_id=run_id,
                rule_version_id=rule_version_id,
            )
        )

        result = ex.execute(
            run_id=run_id,
            rule_version_id=rule_version_id,
            decision_id=uuid.uuid4(),
            action="BUY",
            snapshot=MarketSnapshot(
                ts_ms=0,
                symbol="KRW-BTC",
                last_price=100.0,
                best_bid=100.0,
                best_ask=101.0,
            ),
            rules=rules,
            target_position_pct=20.0,
            allow_min_order_round_up=True,
        )

        self.assertIsNone(result)
        self.assertEqual(client.place_calls, 0)
        self.assertEqual(len(repo.orders), 1)
        self.assertTrue(any(e.event_type == "ORDER_SKIPPED" for e in repo.events))

    def test_live_executor_blocks_buy_during_position_cooldown(self) -> None:
        repo = _FakeRepo()
        client = _FakeLiveClient()
        ex = LiveExecutor(repo=repo, client=client)  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")
        cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=15)
        repo.upsert_position(
            DbPosition(
                symbol="KRW-BTC",
                ts_updated=datetime.now(timezone.utc),
                qty=0.0,
                avg_entry_price=None,
                unrealized_pnl=None,
                stop_price=None,
                take_profit=None,
                meta={"cooldown_until": cooldown_until.isoformat(), "last_exit_reason": "STOP"},
            )
        )

        result = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="BUY",
            snapshot=MarketSnapshot(ts_ms=0, symbol="KRW-BTC", last_price=100.0, best_bid=100.0, best_ask=101.0),
            rules=rules,
            target_position_pct=20.0,
            allow_min_order_round_up=True,
        )

        self.assertIsNone(result)
        self.assertEqual(client.place_calls, 0)
        self.assertTrue(
            any((e.payload or {}).get("reason") == "RG_COOLDOWN_ACTIVE" for e in repo.events),
            "cooldown skip event should be observable",
        )

    def test_live_executor_blocks_new_symbol_when_position_cap_reached(self) -> None:
        repo = _FakeRepo()
        client = _FakeLiveClient()
        ex = LiveExecutor(repo=repo, client=client)  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")
        repo.upsert_position(
            DbPosition(
                symbol="KRW-ETH",
                ts_updated=datetime.now(timezone.utc),
                qty=1.0,
                avg_entry_price=100.0,
                unrealized_pnl=None,
                stop_price=None,
                take_profit=None,
                meta={"trade_id": str(uuid.uuid4())},
            )
        )

        result = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="BUY",
            snapshot=MarketSnapshot(ts_ms=0, symbol="KRW-BTC", last_price=100.0, best_bid=100.0, best_ask=101.0),
            rules=rules,
            target_position_pct=20.0,
            allow_min_order_round_up=True,
        )

        self.assertIsNone(result)
        self.assertEqual(client.place_calls, 0)
        self.assertTrue(
            any((e.payload or {}).get("reason") == "RG_EXPOSURE_LIMIT" for e in repo.events),
            "position-cap skip event should be observable",
        )

    def test_live_executor_blocks_new_symbol_when_other_symbol_has_open_order(self) -> None:
        repo = _FakeRepo()
        client = _FakeLiveClient()
        ex = LiveExecutor(repo=repo, client=client)  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")
        repo.insert_order(
            DbOrder(
                order_id="other-symbol-open-order",
                ts_created=datetime.now(timezone.utc),
                symbol="KRW-ETH",
                side="BUY",
                order_type="limit",
                price=100.0,
                quantity=1.0,
                time_in_force="post_only",
                status="ACK",
                client_order_id="existing",
                meta={"live": True},
                run_id=uuid.uuid4(),
                rule_version_id=uuid.uuid4(),
            )
        )

        result = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="BUY",
            snapshot=MarketSnapshot(ts_ms=0, symbol="KRW-BTC", last_price=100.0, best_bid=100.0, best_ask=101.0),
            rules=rules,
            target_position_pct=20.0,
            allow_min_order_round_up=True,
        )

        self.assertIsNone(result)
        self.assertEqual(client.place_calls, 0)
        self.assertTrue(
            any((e.payload or {}).get("reason") == "RG_EXPOSURE_LIMIT" for e in repo.events),
            "other-symbol open order should lock new entries under max_open_positions=1",
        )

    def test_live_executor_blocks_buy_after_daily_trade_cap(self) -> None:
        repo = _FakeRepo()
        client = _FakeLiveClient()
        ex = LiveExecutor(repo=repo, client=client)  # type: ignore[arg-type]
        rules = load_rules("rules.yaml")
        today_kst = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        max_trades = int((((rules.raw.get("governance") or {}).get("micro_mode") or {}).get("max_trades_per_day") or 0))
        repo.pnl_daily.append({"day": today_kst, "trades_count": max_trades})

        result = ex.execute(
            run_id=uuid.uuid4(),
            rule_version_id=uuid.uuid4(),
            decision_id=uuid.uuid4(),
            action="BUY",
            snapshot=MarketSnapshot(ts_ms=0, symbol="KRW-BTC", last_price=100.0, best_bid=100.0, best_ask=101.0),
            rules=rules,
            target_position_pct=20.0,
            allow_min_order_round_up=True,
        )

        self.assertIsNone(result)
        self.assertEqual(client.place_calls, 0)
        self.assertTrue(
            any((e.payload or {}).get("reason") == "RG_EXPOSURE_LIMIT" for e in repo.events),
            "daily trade-cap skip event should be observable",
        )


if __name__ == "__main__":
    unittest.main()
