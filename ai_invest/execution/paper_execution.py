from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from zoneinfo import ZoneInfo

from ai_invest.config.rules_loader import RulesConfig
from ai_invest.execution.order_state_machine import OrderState, OrderStateMachine
from ai_invest.market_data.upbit_public import MarketSnapshot
from ai_invest.storage.postgres import (
    DbEvent,
    DbExecutionMetric,
    DbFill,
    DbLedgerEntry,
    DbOrder,
    DbPosition,
    PostgresRepo,
)


@dataclass(frozen=True)
class PaperClosedTrade:
    trade_id: uuid.UUID
    entry_decision_id: uuid.UUID | None
    exit_decision_id: uuid.UUID
    symbol: str
    ts_open: datetime
    ts_close: datetime
    side: str
    qty: float
    avg_entry_price: float
    avg_exit_price: float
    realized_pnl: float
    fees_total: float
    pnl_bps: float | None


@dataclass(frozen=True)
class PaperExecutionResult:
    order_id: str
    side: str
    trade_id: uuid.UUID
    entry_decision_id: uuid.UUID | None
    fill_event_id: uuid.UUID
    fill_id: uuid.UUID
    fill_price: float
    fill_qty: float
    fee: float
    closed_trade: PaperClosedTrade | None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fee_rate_bps(rules: RulesConfig, *, side: str) -> float:
    fees = rules.raw.get("fees", {}) if isinstance(rules.raw, dict) else {}
    if side.upper() == "BUY":
        return float(fees.get("fallback_bid_fee_bps", 5.0))
    return float(fees.get("fallback_ask_fee_bps", 5.0))


def _slippage_bps(fill_price: float, ref_price: float, *, side: str) -> float:
    if ref_price <= 0:
        return 0.0
    # Positive slippage means worse fill (higher buy, lower sell).
    if side.upper() == "BUY":
        return (fill_price - ref_price) / ref_price * 10000.0
    return (ref_price - fill_price) / ref_price * 10000.0


def _quote_currency(symbol: str) -> str:
    if "-" not in symbol:
        return "KRW"
    return symbol.split("-", 1)[0].strip().upper() or "KRW"


class PaperExecutor:
    """Paper execution engine (no real orders).

    This writes to orders/fills/positions/execution_metrics + event mirrors.
    """

    def __init__(self, repo: PostgresRepo) -> None:
        self._repo = repo

    def execute(
        self,
        *,
        run_id: uuid.UUID,
        rule_version_id: uuid.UUID,
        decision_id: uuid.UUID,
        action: str,
        snapshot: MarketSnapshot,
        rules: RulesConfig,
    ) -> PaperExecutionResult | None:
        action = action.upper()
        if action not in {"BUY", "SELL"}:
            return None

        symbol = snapshot.symbol
        ts_decision = _utcnow()
        decision_mid = snapshot.mid_price

        # Determine quantity.
        pos = self._repo.fetch_position(symbol)
        current_qty = float(pos.qty) if pos else 0.0

        pos_meta = (pos.meta or {}) if pos else {}
        pos_trade_id = str(pos_meta.get("trade_id") or "").strip() or None
        pos_entry_decision_id = str(pos_meta.get("entry_decision_id") or "").strip() or None

        if action == "BUY":
            min_order_krw = int(rules.execution.min_order_krw)
            price = snapshot.best_bid  # post-only maker bias
            qty = float(min_order_krw) / float(price)
            side = "BUY"
        else:
            if current_qty <= 0:
                return None
            price = snapshot.best_ask  # maker bias
            qty = current_qty
            side = "SELL"

        trade_id: uuid.UUID
        entry_decision_id: uuid.UUID | None
        if action == "BUY":
            if current_qty <= 0 or not pos_trade_id:
                trade_id = uuid.uuid4()
                entry_decision_id = decision_id
            else:
                try:
                    trade_id = uuid.UUID(pos_trade_id)
                except Exception:
                    trade_id = uuid.uuid4()
                try:
                    entry_decision_id = uuid.UUID(pos_entry_decision_id) if pos_entry_decision_id else None
                except Exception:
                    entry_decision_id = None
        else:
            try:
                trade_id = uuid.UUID(pos_trade_id) if pos_trade_id else uuid.uuid4()
            except Exception:
                trade_id = uuid.uuid4()
            try:
                entry_decision_id = uuid.UUID(pos_entry_decision_id) if pos_entry_decision_id else None
            except Exception:
                entry_decision_id = None

        order_id = f"paper-{uuid.uuid4()}"
        submit_ts = _utcnow()

        machine = OrderStateMachine()

        # NEW -> ACK
        self._repo.insert_order(
            DbOrder(
                order_id=order_id,
                ts_created=submit_ts,
                symbol=symbol,
                side=side,
                order_type="limit",
                price=price,
                quantity=qty,
                time_in_force=str(rules.execution.default_time_in_force),
                status=OrderState.NEW.value,
                client_order_id=None,
                meta={
                    "paper": True,
                    "decision_id": str(decision_id),
                    "trade_id": str(trade_id),
                    "entry_decision_id": str(entry_decision_id) if entry_decision_id else None,
                },
                run_id=run_id,
                rule_version_id=rule_version_id,
            )
        )
        self._repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=submit_ts,
                event_type="ORDER_SUBMITTED",
                entity_type="orders",
                entity_id=order_id,
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={"decision_id": str(decision_id), "symbol": symbol, "side": side, "price": price, "qty": qty},
            )
        )

        machine.transition(OrderState.ACK, "RG_PASS")
        self._repo.update_order_status(order_id, status=OrderState.ACK.value, meta_patch={"ack_ts": submit_ts.isoformat()})
        self._repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=_utcnow(),
                event_type="ORDER_ACK",
                entity_type="orders",
                entity_id=order_id,
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={"order_id": order_id, "symbol": symbol},
            )
        )

        # Fill immediately in paper.
        fill_ts = _utcnow()
        fill_id = uuid.uuid4()
        fill_price = float(price)
        fee_bps = _fee_rate_bps(rules, side=side)
        fee = fill_price * qty * (fee_bps / 10000.0)

        self._repo.insert_fill(
            DbFill(
                fill_id=fill_id,
                order_id=order_id,
                ts_filled=fill_ts,
                price=fill_price,
                quantity=qty,
                fee=fee,
                fee_currency=_quote_currency(symbol),
                liquidity="MAKER",
                meta={
                    "paper": True,
                    "decision_id": str(decision_id),
                    "trade_id": str(trade_id),
                    "entry_decision_id": str(entry_decision_id) if entry_decision_id else None,
                },
            )
        )
        fill_event_id = uuid.uuid4()
        self._repo.insert_event(
            DbEvent(
                event_id=fill_event_id,
                ts=fill_ts,
                event_type="FILL",
                entity_type="fills",
                entity_id=str(fill_id),
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={"order_id": order_id, "symbol": symbol, "side": side, "price": fill_price, "qty": qty},
            )
        )

        machine.transition(OrderState.FILLED, "RG_PASS")
        self._repo.update_order_status(order_id, status=OrderState.FILLED.value, meta_patch={"filled_ts": fill_ts.isoformat()})

        # Finance ledger primitive (quote-currency cashflow + fee).
        quote_ccy = _quote_currency(symbol)
        notional = float(fill_price) * float(qty)
        cashflow = -notional if side.upper() == "BUY" else notional
        self._repo.insert_ledger_entry(
            DbLedgerEntry(
                entry_id=uuid.uuid4(),
                ts=fill_ts,
                entry_type="TRADE_FILL",
                symbol=symbol,
                currency=quote_ccy,
                amount=cashflow,
                price=fill_price,
                fee_amount=float(fee) if fee is not None else None,
                fee_currency=quote_ccy,
                order_id=order_id,
                fill_id=fill_id,
                meta={
                    "paper": True,
                    "side": side,
                    "qty": qty,
                    "decision_id": str(decision_id),
                    "trade_id": str(trade_id),
                    "entry_decision_id": str(entry_decision_id) if entry_decision_id else None,
                },
            )
        )

        # Position update.
        closed_trade: PaperClosedTrade | None = None
        if side == "BUY":
            new_qty = current_qty + qty
            if not pos or not pos.avg_entry_price or current_qty <= 0:
                new_avg = fill_price
                opened_at = fill_ts.isoformat()
            else:
                new_avg = (pos.avg_entry_price * current_qty + fill_price * qty) / new_qty
                opened_at = (pos.meta or {}).get("opened_at") or fill_ts.isoformat()
            prev_fees = float((pos.meta or {}).get("fees_paid_krw") or 0.0) if pos else 0.0
            fees_paid = prev_fees + fee
            self._repo.upsert_position(
                DbPosition(
                    symbol=symbol,
                    ts_updated=fill_ts,
                    qty=new_qty,
                    avg_entry_price=new_avg,
                    unrealized_pnl=None,
                    stop_price=None,
                    take_profit=None,
                    meta={
                        "opened_at": opened_at,
                        "fees_paid_krw": fees_paid,
                        "trade_id": str(trade_id),
                        "entry_decision_id": str(entry_decision_id) if entry_decision_id else str(decision_id),
                    },
                )
            )
        else:
            # Sell all in v1.
            avg_entry = float(pos.avg_entry_price) if pos and pos.avg_entry_price else 0.0
            opened_at = (pos.meta or {}).get("opened_at") if pos else None
            fees_paid_buy = float((pos.meta or {}).get("fees_paid_krw") or 0.0) if pos else 0.0
            fees_total = fees_paid_buy + fee
            gross = (fill_price - avg_entry) * qty
            realized_pnl = gross - fees_total
            notional = max(0.0, avg_entry * qty)
            pnl_bps = (realized_pnl / notional * 10000.0) if notional > 0 else None

            # realized_trades / pnl_daily (KST day)
            tz_name = str(rules.raw.get("settlement", {}).get("timezone", "Asia/Seoul"))
            try:
                tz = ZoneInfo(tz_name)
            except Exception:  # pragma: no cover
                tz = ZoneInfo("Asia/Seoul")
            day = fill_ts.astimezone(tz).date().isoformat()

            ts_open = fill_ts
            if opened_at:
                try:
                    ts_open = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                except Exception:
                    ts_open = fill_ts

            self._repo.insert_realized_trade(
                trade_id=trade_id,
                symbol=symbol,
                ts_open=ts_open,
                ts_close=fill_ts,
                side="LONG",
                qty=qty,
                avg_entry_price=avg_entry,
                avg_exit_price=fill_price,
                realized_pnl=realized_pnl,
                fees_total=fees_total,
                pnl_bps=pnl_bps,
                tags={},
                meta={
                    "order_id": order_id,
                    "fill_id": str(fill_id),
                    "paper": True,
                    "trade_id": str(trade_id),
                    "entry_decision_id": str(entry_decision_id) if entry_decision_id else None,
                    "exit_decision_id": str(decision_id),
                },
            )
            self._repo.upsert_pnl_daily_delta(
                day=day,
                realized_pnl_delta=realized_pnl,
                fees_paid_delta=fees_total,
                trades_count_delta=1,
            )
            self._repo.upsert_position(
                DbPosition(
                    symbol=symbol,
                    ts_updated=fill_ts,
                    qty=0.0,
                    avg_entry_price=None,
                    unrealized_pnl=None,
                    stop_price=None,
                    take_profit=None,
                    meta={},
                )
            )
            closed_trade = PaperClosedTrade(
                trade_id=trade_id,
                entry_decision_id=entry_decision_id,
                exit_decision_id=decision_id,
                symbol=symbol,
                ts_open=ts_open,
                ts_close=fill_ts,
                side="LONG",
                qty=qty,
                avg_entry_price=avg_entry,
                avg_exit_price=fill_price,
                realized_pnl=realized_pnl,
                fees_total=fees_total,
                pnl_bps=pnl_bps,
            )

        # TCA-lite metric.
        self._repo.insert_execution_metric(
            DbExecutionMetric(
                metric_id=uuid.uuid4(),
                order_id=order_id,
                symbol=symbol,
                ts_decision=ts_decision,
                ts_submit=submit_ts,
                ts_first_fill=fill_ts,
                ts_last_fill=fill_ts,
                decision_mid=decision_mid,
                submit_mid=snapshot.mid_price,
                fill_vwap=fill_price,
                slippage_bps_vs_decision=_slippage_bps(fill_price, decision_mid, side=side),
                slippage_bps_vs_submit=_slippage_bps(fill_price, snapshot.mid_price, side=side),
                spread_bps_at_submit=snapshot.spread_bps,
                filled_ratio=1.0,
                latency_ms_decision_to_submit=int((submit_ts - ts_decision).total_seconds() * 1000),
                latency_ms_submit_to_fill=int((fill_ts - submit_ts).total_seconds() * 1000),
                meta={"paper": True},
            )
        )

        return PaperExecutionResult(
            order_id=order_id,
            side=side,
            trade_id=trade_id,
            entry_decision_id=entry_decision_id,
            fill_event_id=fill_event_id,
            fill_id=fill_id,
            fill_price=fill_price,
            fill_qty=qty,
            fee=fee,
            closed_trade=closed_trade,
        )
