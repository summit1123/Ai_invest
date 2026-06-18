from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from zoneinfo import ZoneInfo

from ai_invest.config.rules_loader import RulesConfig
from ai_invest.execution.live_sync import sync_symbol_account_state
from ai_invest.execution.order_state_machine import InvalidTransitionError, OrderState, OrderStateMachine
from ai_invest.execution.upbit_private import UpbitPrivateApiError, UpbitPrivateClient
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _env_float(name: str, default: float) -> float:
    try:
        raw = str(os.environ.get(name, "")).strip()
        if not raw:
            return float(default)
        return float(raw)
    except Exception:
        return float(default)


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, bool):
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s else float(default)
    except Exception:
        return float(default)


def _quote_currency(symbol: str) -> str:
    if "-" not in symbol:
        return "KRW"
    return symbol.split("-", 1)[0].strip().upper() or "KRW"


def _slippage_bps(fill_price: float, ref_price: float, *, side: str) -> float:
    if ref_price <= 0:
        return 0.0
    if side.upper() == "BUY":
        return (fill_price - ref_price) / ref_price * 10000.0
    return (ref_price - fill_price) / ref_price * 10000.0


def _fee_rate_bps(rules: RulesConfig, *, side: str) -> float:
    fees = rules.raw.get("fees", {}) if isinstance(rules.raw, Mapping) else {}
    if side.upper() == "BUY":
        return float(fees.get("fallback_bid_fee_bps", 5.0))
    return float(fees.get("fallback_ask_fee_bps", 5.0))


def _min_order_target_krw(rules: RulesConfig) -> float:
    min_order_krw = float(rules.execution.min_order_krw)
    cfg = rules.raw.get("runtime_controller", {}) if isinstance(rules.raw, Mapping) else {}
    buffer_mult = _as_float(cfg.get("min_order_buffer_mult"), default=1.0)
    buffer_mult = max(1.0, min(float(buffer_mult), 1.20))
    return max(float(min_order_krw), float(min_order_krw) * float(buffer_mult))


def _parse_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _settlement_day(rules: RulesConfig, ts: datetime) -> str:
    tz_name = str(rules.raw.get("settlement", {}).get("timezone", "Asia/Seoul"))
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Seoul")
    return ts.astimezone(tz).date().isoformat()


def _micro_max_trades_per_day(rules: RulesConfig) -> int:
    governance = rules.raw.get("governance", {}) if isinstance(rules.raw, Mapping) else {}
    micro = governance.get("micro_mode", {}) if isinstance(governance, Mapping) else {}
    try:
        return max(0, int(float(micro.get("max_trades_per_day") or 0)))
    except Exception:
        return 0


def _safe_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except Exception:
        return None


def _extract_fill_rows(
    *,
    order_snapshot: Mapping[str, Any],
    fallback_price: float,
) -> list[dict[str, float]]:
    trades = order_snapshot.get("trades")
    paid_fee_total = _as_float(order_snapshot.get("paid_fee"), default=0.0)
    rows: list[dict[str, float]] = []

    if isinstance(trades, list) and trades:
        for tr in trades:
            if not isinstance(tr, Mapping):
                continue
            qty = _as_float(tr.get("volume"), default=0.0)
            px = _as_float(tr.get("price"), default=0.0)
            fee = _as_float(tr.get("fee"), default=0.0)
            if qty <= 0 or px <= 0:
                continue
            rows.append({"qty": float(qty), "price": float(px), "fee": float(max(0.0, fee))})
        if rows:
            current_fee = sum(float(r["fee"]) for r in rows)
            if paid_fee_total > 0 and current_fee <= 0:
                notional = sum(float(r["qty"]) * float(r["price"]) for r in rows)
                for r in rows:
                    w = (float(r["qty"]) * float(r["price"])) / notional if notional > 0 else 0.0
                    r["fee"] = float(paid_fee_total * w)
            return rows

    executed_volume = _as_float(order_snapshot.get("executed_volume"), default=0.0)
    if executed_volume <= 0:
        return []

    avg_price = _as_float(order_snapshot.get("price"), default=0.0)
    if avg_price <= 0:
        funds = _as_float(order_snapshot.get("executed_funds"), default=0.0)
        avg_price = funds / executed_volume if funds > 0 else 0.0
    if avg_price <= 0:
        avg_price = float(fallback_price)
    if avg_price <= 0:
        return []
    return [{"qty": float(executed_volume), "price": float(avg_price), "fee": float(max(0.0, paid_fee_total))}]


@dataclass(frozen=True)
class LiveClosedTrade:
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
    exit_reason: str | None


@dataclass(frozen=True)
class LiveExecutionResult:
    order_id: str
    side: str
    trade_id: uuid.UUID
    entry_decision_id: uuid.UUID | None
    fill_event_id: uuid.UUID
    fill_id: uuid.UUID
    fill_price: float
    fill_qty: float
    fee: float
    closed_trade: LiveClosedTrade | None


class LiveExecutor:
    """Live execution adapter for Upbit private order API."""

    def __init__(self, repo: PostgresRepo, client: UpbitPrivateClient) -> None:
        self._repo = repo
        self._client = client
        self._poll_sec = max(0.2, _env_float("UPBIT_LIVE_POLL_SEC", 1.0))
        self._max_wait_sec_env = max(1.0, _env_float("UPBIT_LIVE_MAX_WAIT_SEC", 25.0))

    def _emit_order_state_event(
        self,
        *,
        run_id: uuid.UUID,
        rule_version_id: uuid.UUID,
        order_id: str,
        symbol: str,
        state: OrderState,
    ) -> None:
        event_type_by_state = {
            OrderState.ACK: "ORDER_ACK",
            OrderState.PARTIAL: "ORDER_PARTIAL_FILL",
            OrderState.FILLED: "ORDER_FILLED",
            OrderState.CANCELED: "ORDER_CANCELED",
            OrderState.REJECTED: "ORDER_REJECTED",
        }
        event_type = event_type_by_state.get(state)
        if not event_type:
            return
        self._repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=_utcnow(),
                event_type=event_type,
                entity_type="orders",
                entity_id=order_id,
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={"order_id": order_id, "symbol": symbol, "state": state.value},
            )
        )

    def _transition_state(
        self,
        *,
        machine: OrderStateMachine,
        upbit_status: str,
        executed_volume: float,
        remaining_volume: float | None,
    ) -> list[OrderState]:
        emitted: list[OrderState] = []
        status = str(upbit_status or "").strip().lower()
        executed = float(max(0.0, executed_volume))
        remaining = None if remaining_volume is None else float(max(0.0, remaining_volume))

        # Normalize Upbit states to our state machine transition constraints.
        sequence: list[OrderState] = []
        if status in {"wait", "watch"}:
            sequence = [OrderState.ACK]
            if executed > 0 and (remaining is None or remaining > 0):
                sequence.append(OrderState.PARTIAL)
        elif status == "done":
            sequence = [OrderState.ACK]
            if executed > 0 and remaining is not None and remaining > 0:
                sequence.append(OrderState.PARTIAL)
            sequence.append(OrderState.FILLED)
        elif status == "cancel":
            sequence = [OrderState.ACK]
            if executed > 0:
                sequence.append(OrderState.PARTIAL)
            sequence.append(OrderState.CANCELED)
        else:
            # Unknown state from exchange: leave machine untouched.
            return emitted

        for target in sequence:
            if machine.state == target:
                continue
            try:
                machine.transition(target, "RG_PASS")
            except InvalidTransitionError:
                continue
            emitted.append(target)
        return emitted

    def _skip_order(
        self,
        *,
        run_id: uuid.UUID,
        rule_version_id: uuid.UUID,
        decision_id: uuid.UUID,
        symbol: str,
        action: str,
        reason: str,
        ts: datetime,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        payload = {
            "decision_id": str(decision_id),
            "symbol": symbol,
            "action": str(action).upper(),
            "reason": str(reason),
        }
        payload.update(dict(extra or {}))
        self._repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=ts,
                event_type="ORDER_SKIPPED",
                entity_type="orders",
                entity_id=str(decision_id),
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload=payload,
            )
        )

    def _today_trade_count(self, *, rules: RulesConfig, ts: datetime) -> int:
        fetch_pnl_daily = getattr(self._repo, "fetch_pnl_daily", None)
        if not callable(fetch_pnl_daily):
            return 0
        today = _settlement_day(rules, ts)
        try:
            rows = list(fetch_pnl_daily(limit=3) or [])
        except Exception:
            return 0
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("day") or "") != today:
                continue
            try:
                return max(0, int(float(row.get("trades_count", row.get("trades_count_delta", 0)) or 0)))
            except Exception:
                return 0
        return 0

    def _active_execution_symbols(self, *, quote_currency: str) -> set[str]:
        active: set[str] = set()
        overview_fn = getattr(self._repo, "fetch_portfolio_overview", None)
        if callable(overview_fn):
            try:
                overview = overview_fn(quote_currency=quote_currency) or {}
            except Exception:
                overview = {}
            for row in list(overview.get("positions") or []):
                if not isinstance(row, Mapping):
                    continue
                sym = str(row.get("symbol") or "").strip().upper()
                qty = _as_float(row.get("qty"), default=0.0)
                if sym and qty > 0:
                    active.add(sym)

        fetch_open_orders = getattr(self._repo, "fetch_open_orders", None)
        if callable(fetch_open_orders):
            try:
                rows = list(fetch_open_orders(statuses=["NEW", "ACK", "PARTIAL"], limit=50) or [])
            except Exception:
                rows = []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                sym = str(row.get("symbol") or "").strip().upper()
                if sym:
                    active.add(sym)
        return active

    def execute(
        self,
        *,
        run_id: uuid.UUID,
        rule_version_id: uuid.UUID,
        decision_id: uuid.UUID,
        action: str,
        snapshot: MarketSnapshot,
        rules: RulesConfig,
        target_position_pct: float | None = None,
        strategy_tag: str | None = None,
        exit_reason: str | None = None,
        cooldown_minutes: int | None = None,
        allow_min_order_round_up: bool = False,
    ) -> LiveExecutionResult | None:
        action_u = str(action or "").strip().upper()
        if action_u not in {"BUY", "SELL"}:
            return None

        symbol = snapshot.symbol
        quote_ccy = _quote_currency(symbol)
        ts_decision = _utcnow()
        submit_mid = float(snapshot.mid_price)

        fetch_open_orders = getattr(self._repo, "fetch_open_orders", None)
        if callable(fetch_open_orders):
            try:
                open_orders = list(
                    fetch_open_orders(symbol=symbol, statuses=["NEW", "ACK", "PARTIAL"], limit=10)
                    or []
                )
            except Exception:
                open_orders = []
            if open_orders:
                self._skip_order(
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                    decision_id=decision_id,
                    symbol=symbol,
                    action=action_u,
                    reason="OPEN_ORDER_EXISTS",
                    ts=ts_decision,
                    extra={
                        "open_order_ids": [
                            str(row.get("order_id") or "")
                            for row in open_orders[:10]
                            if isinstance(row, Mapping)
                        ],
                    },
                )
                return None

        pos = self._repo.fetch_position(symbol)
        pos_meta = dict((pos.meta or {}) if pos else {})
        prev_qty_local = float(pos.qty) if pos else 0.0
        prev_avg_local = float(pos.avg_entry_price) if (pos and pos.avg_entry_price) else 0.0

        # Use exchange account snapshot for live sizing truth, and sync local cache.
        live_state = sync_symbol_account_state(
            repo=self._repo,
            client=self._client,
            symbol=symbol,
            run_id=run_id,
            rule_version_id=rule_version_id,
        )
        current_qty = float(live_state.base_qty_total)
        current_avg = float(live_state.base_avg_buy_price or 0.0)
        cash_balance = float(live_state.quote_balance_available)

        pos_trade_id = str(pos_meta.get("trade_id") or "").strip() or None
        pos_entry_decision_id = str(pos_meta.get("entry_decision_id") or "").strip() or None

        side = "BUY" if action_u == "BUY" else "SELL"
        if side == "BUY":
            if current_qty <= 0:
                cooldown_until = _parse_utc_datetime(pos_meta.get("cooldown_until"))
                if cooldown_until is not None and cooldown_until > ts_decision:
                    self._skip_order(
                        run_id=run_id,
                        rule_version_id=rule_version_id,
                        decision_id=decision_id,
                        symbol=symbol,
                        action=action_u,
                        reason="RG_COOLDOWN_ACTIVE",
                        ts=ts_decision,
                        extra={
                            "cooldown_until": cooldown_until.isoformat(),
                            "last_exit_reason": pos_meta.get("last_exit_reason"),
                        },
                    )
                    return None

                max_trades = _micro_max_trades_per_day(rules)
                if max_trades > 0:
                    today_trades = self._today_trade_count(rules=rules, ts=ts_decision)
                    if int(today_trades) >= int(max_trades):
                        self._skip_order(
                            run_id=run_id,
                            rule_version_id=rule_version_id,
                            decision_id=decision_id,
                            symbol=symbol,
                            action=action_u,
                            reason="RG_EXPOSURE_LIMIT",
                            ts=ts_decision,
                            extra={"daily_trades_count": int(today_trades), "max_trades_per_day": int(max_trades)},
                        )
                        return None

                max_open_positions = max(0, int(rules.universe.max_open_positions))
                if max_open_positions > 0:
                    active_symbols = self._active_execution_symbols(quote_currency=quote_ccy)
                    if symbol not in active_symbols and len(active_symbols) >= int(max_open_positions):
                        self._skip_order(
                            run_id=run_id,
                            rule_version_id=rule_version_id,
                            decision_id=decision_id,
                            symbol=symbol,
                            action=action_u,
                            reason="RG_EXPOSURE_LIMIT",
                            ts=ts_decision,
                            extra={
                                "active_symbols": sorted(active_symbols),
                                "max_open_positions": int(max_open_positions),
                            },
                        )
                        return None

            min_order_krw = int(rules.execution.min_order_krw)
            min_order_target_krw = _min_order_target_krw(rules)
            tgt_pct = None
            try:
                tgt_pct = float(target_position_pct) if target_position_pct is not None else None
            except Exception:
                tgt_pct = None

            if tgt_pct is None:
                desired_krw = float(min_order_krw)
            else:
                if tgt_pct <= 0:
                    return None
                pos_value = float(current_qty) * float(snapshot.mid_price)
                equity = float(cash_balance) + float(pos_value)
                desired_value = equity * (float(tgt_pct) / 100.0)
                desired_krw = max(0.0, float(desired_value) - float(pos_value))
            desired_krw = min(float(desired_krw), float(cash_balance))
            if desired_krw < float(min_order_krw):
                if (
                    not bool(allow_min_order_round_up)
                    or current_qty > 0
                    or float(cash_balance) < float(min_order_target_krw)
                ):
                    return None
                desired_krw = float(min_order_target_krw)

            # Fee-aware affordability.
            fee_rate = _fee_rate_bps(rules, side="BUY") / 10000.0
            max_affordable = float(cash_balance) / (1.0 + float(fee_rate))
            desired_krw = min(float(desired_krw), float(max_affordable))
            if desired_krw < float(min_order_target_krw if bool(allow_min_order_round_up) else min_order_krw):
                return None

            ord_type_cfg = str(rules.execution.default_ord_type).strip().lower()
            if ord_type_cfg == "market":
                side_upbit = "bid"
                ord_type = "price"
                volume = None
                price = float(desired_krw)
                tif = None
            else:
                side_upbit = "bid"
                ord_type = "limit"
                price = float(snapshot.best_bid)
                volume = float(desired_krw) / float(price) if float(price) > 0 else 0.0
                if volume <= 0:
                    return None
                tif = str(rules.execution.default_time_in_force or "").strip().lower() or None
        else:
            if current_qty <= 0:
                return None
            ord_type_cfg = str(rules.execution.default_ord_type).strip().lower()
            if ord_type_cfg == "market":
                side_upbit = "ask"
                ord_type = "market"
                volume = float(current_qty)
                price = None
                tif = None
            else:
                side_upbit = "ask"
                ord_type = "limit"
                volume = float(current_qty)
                price = float(snapshot.best_ask)
                tif = str(rules.execution.default_time_in_force or "").strip().lower() or None

        if action_u == "BUY":
            if current_qty <= 0 or not pos_trade_id:
                trade_id = uuid.uuid4()
                entry_decision_id = decision_id
            else:
                trade_id = _safe_uuid(pos_trade_id) or uuid.uuid4()
                entry_decision_id = _safe_uuid(pos_entry_decision_id) or None
        else:
            trade_id = _safe_uuid(pos_trade_id) or uuid.uuid4()
            entry_decision_id = _safe_uuid(pos_entry_decision_id) or None

        identifier = f"ai-invest:{decision_id}"
        submit_ts = _utcnow()
        try:
            submit_res = self._client.place_order(
                market=symbol,
                side=side_upbit,
                ord_type=ord_type,
                volume=volume,
                price=price,
                time_in_force=tif,
                identifier=identifier,
            )
        except UpbitPrivateApiError as exc:
            reject_order_id = f"live-rejected-{uuid.uuid4()}"
            self._repo.insert_order(
                DbOrder(
                    order_id=reject_order_id,
                    ts_created=submit_ts,
                    symbol=symbol,
                    side=side,
                    order_type=ord_type,
                    price=price,
                    quantity=float(volume or 0.0),
                    time_in_force=tif,
                    status=OrderState.REJECTED.value,
                    client_order_id=identifier,
                    meta={
                        "live": True,
                        "decision_id": str(decision_id),
                        "trade_id": str(trade_id),
                        "entry_decision_id": str(entry_decision_id) if entry_decision_id else None,
                        "submit_error": str(exc),
                    },
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                )
            )
            self._repo.insert_event(
                DbEvent(
                    event_id=uuid.uuid4(),
                    ts=_utcnow(),
                    event_type="ORDER_REJECTED",
                    entity_type="orders",
                    entity_id=reject_order_id,
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                    payload={"symbol": symbol, "decision_id": str(decision_id), "error": str(exc)},
                )
            )
            return None

        order_id = str(submit_res.get("uuid") or f"live-{uuid.uuid4()}")
        machine = OrderStateMachine()
        self._repo.insert_order(
            DbOrder(
                order_id=order_id,
                ts_created=submit_ts,
                symbol=symbol,
                side=side,
                order_type=ord_type,
                price=price,
                quantity=float(volume or 0.0),
                time_in_force=tif,
                status=OrderState.NEW.value,
                client_order_id=identifier,
                meta={
                    "live": True,
                    "decision_id": str(decision_id),
                    "trade_id": str(trade_id),
                    "entry_decision_id": str(entry_decision_id) if entry_decision_id else None,
                    "upbit_submit": dict(submit_res),
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
                payload={
                    "decision_id": str(decision_id),
                    "symbol": symbol,
                    "side": side,
                    "order_type": ord_type,
                    "price": price,
                    "qty": volume,
                    "identifier": identifier,
                },
            )
        )

        latest_snapshot: Mapping[str, Any] = dict(submit_res)
        emitted_states = self._transition_state(
            machine=machine,
            upbit_status=str(latest_snapshot.get("state") or ""),
            executed_volume=_as_float(latest_snapshot.get("executed_volume"), default=0.0),
            remaining_volume=(
                _as_float(latest_snapshot.get("remaining_volume"), default=0.0)
                if latest_snapshot.get("remaining_volume") is not None
                else None
            ),
        )
        for st in emitted_states:
            self._repo.update_order_status(order_id, status=st.value, meta_patch={"state_ts": _utcnow().isoformat()})
            self._emit_order_state_event(
                run_id=run_id, rule_version_id=rule_version_id, order_id=order_id, symbol=symbol, state=st
            )

        max_wait_sec = min(float(rules.execution.cancel_on_timeout_sec), float(self._max_wait_sec_env))
        deadline = submit_ts + timedelta(seconds=max_wait_sec)
        while _utcnow() < deadline:
            if machine.state in {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED}:
                break
            time.sleep(float(self._poll_sec))
            try:
                latest_snapshot = self._client.get_order(order_id=order_id)
            except UpbitPrivateApiError:
                continue
            emitted_states = self._transition_state(
                machine=machine,
                upbit_status=str(latest_snapshot.get("state") or ""),
                executed_volume=_as_float(latest_snapshot.get("executed_volume"), default=0.0),
                remaining_volume=(
                    _as_float(latest_snapshot.get("remaining_volume"), default=0.0)
                    if latest_snapshot.get("remaining_volume") is not None
                    else None
                ),
            )
            for st in emitted_states:
                self._repo.update_order_status(
                    order_id,
                    status=st.value,
                    meta_patch={"state_ts": _utcnow().isoformat(), "upbit_state": str(latest_snapshot.get("state") or "")},
                )
                self._emit_order_state_event(
                    run_id=run_id, rule_version_id=rule_version_id, order_id=order_id, symbol=symbol, state=st
                )

        if machine.state not in {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED}:
            try:
                latest_snapshot = self._client.cancel_order(order_id=order_id)
            except UpbitPrivateApiError:
                latest_snapshot = self._client.get_order(order_id=order_id)
            emitted_states = self._transition_state(
                machine=machine,
                upbit_status=str(latest_snapshot.get("state") or "cancel"),
                executed_volume=_as_float(latest_snapshot.get("executed_volume"), default=0.0),
                remaining_volume=(
                    _as_float(latest_snapshot.get("remaining_volume"), default=0.0)
                    if latest_snapshot.get("remaining_volume") is not None
                    else None
                ),
            )
            for st in emitted_states:
                self._repo.update_order_status(
                    order_id,
                    status=st.value,
                    meta_patch={"state_ts": _utcnow().isoformat(), "timeout_cancel": True},
                )
                self._emit_order_state_event(
                    run_id=run_id, rule_version_id=rule_version_id, order_id=order_id, symbol=symbol, state=st
                )

        fill_rows = _extract_fill_rows(order_snapshot=latest_snapshot, fallback_price=float(submit_mid))
        if not fill_rows:
            self._repo.insert_execution_metric(
                DbExecutionMetric(
                    metric_id=uuid.uuid4(),
                    order_id=order_id,
                    symbol=symbol,
                    ts_decision=ts_decision,
                    ts_submit=submit_ts,
                    ts_first_fill=None,
                    ts_last_fill=None,
                    decision_mid=float(snapshot.mid_price),
                    submit_mid=float(submit_mid),
                    fill_vwap=None,
                    slippage_bps_vs_decision=None,
                    slippage_bps_vs_submit=None,
                    spread_bps_at_submit=float(snapshot.spread_bps),
                    filled_ratio=0.0,
                    latency_ms_decision_to_submit=int((submit_ts - ts_decision).total_seconds() * 1000),
                    latency_ms_submit_to_fill=None,
                    meta={"live": True, "state": machine.state.value, "no_fill": True},
                )
            )
            return None

        total_qty = sum(float(r["qty"]) for r in fill_rows)
        total_notional = sum(float(r["qty"]) * float(r["price"]) for r in fill_rows)
        total_fee = sum(float(r["fee"]) for r in fill_rows)
        vwap = total_notional / total_qty if total_qty > 0 else 0.0

        first_fill_ts = _utcnow()
        first_fill_id: uuid.UUID | None = None
        first_fill_event_id: uuid.UUID | None = None

        for idx, row in enumerate(fill_rows):
            fill_ts = _utcnow()
            fill_id = uuid.uuid4()
            fee_i = float(row["fee"])
            qty_i = float(row["qty"])
            px_i = float(row["price"])
            self._repo.insert_fill(
                DbFill(
                    fill_id=fill_id,
                    order_id=order_id,
                    ts_filled=fill_ts,
                    price=px_i,
                    quantity=qty_i,
                    fee=fee_i,
                    fee_currency=quote_ccy,
                    liquidity="UNKNOWN",
                    meta={
                        "live": True,
                        "decision_id": str(decision_id),
                        "trade_id": str(trade_id),
                        "entry_decision_id": str(entry_decision_id) if entry_decision_id else None,
                        "fill_index": int(idx),
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
                    payload={"order_id": order_id, "symbol": symbol, "side": side, "price": px_i, "qty": qty_i},
                )
            )
            self._repo.insert_ledger_entry(
                DbLedgerEntry(
                    entry_id=uuid.uuid4(),
                    ts=fill_ts,
                    entry_type="TRADE_FILL",
                    symbol=symbol,
                    currency=quote_ccy,
                    amount=(-px_i * qty_i if side == "BUY" else px_i * qty_i),
                    price=px_i,
                    fee_amount=fee_i,
                    fee_currency=quote_ccy,
                    order_id=order_id,
                    fill_id=fill_id,
                    meta={
                        "live": True,
                        "side": side,
                        "qty": qty_i,
                        "decision_id": str(decision_id),
                        "trade_id": str(trade_id),
                        "entry_decision_id": str(entry_decision_id) if entry_decision_id else None,
                        "target_position_pct": float(target_position_pct) if target_position_pct is not None else None,
                    },
                )
            )
            if first_fill_id is None:
                first_fill_id = fill_id
                first_fill_event_id = fill_event_id
                first_fill_ts = fill_ts

        closed_trade: LiveClosedTrade | None = None
        if side == "BUY":
            pre_qty = float(prev_qty_local)
            pre_avg = float(prev_avg_local)
            new_qty = float(pre_qty + total_qty)
            if pre_qty > 0 and pre_avg > 0 and new_qty > 0:
                new_avg = (pre_avg * pre_qty + vwap * total_qty) / new_qty
                opened_at = str(pos_meta.get("opened_at") or pos_meta.get("entry_ts") or first_fill_ts.isoformat())
                fees_paid = float(pos_meta.get("fees_paid_krw") or 0.0) + float(total_fee)
            else:
                new_avg = float(vwap)
                opened_at = first_fill_ts.isoformat()
                fees_paid = float(total_fee)
            effective_tag = str((strategy_tag or pos_meta.get("strategy_tag") or "MOM")).strip().upper() or "MOM"
            self._repo.upsert_position(
                DbPosition(
                    symbol=symbol,
                    ts_updated=first_fill_ts,
                    qty=float(new_qty),
                    avg_entry_price=float(new_avg),
                    unrealized_pnl=None,
                    stop_price=None,
                    take_profit=None,
                    meta={
                        "opened_at": opened_at,
                        "entry_ts": opened_at,
                        "entry_price": float(new_avg),
                        "hwm_price": float(max(float(vwap), _as_float(pos_meta.get("hwm_price"), default=0.0))),
                        "strategy_tag": effective_tag,
                        "cooldown_until": None,
                        "last_exit_reason": None,
                        "fees_paid_krw": float(fees_paid),
                        "trade_id": str(trade_id),
                        "entry_decision_id": str(entry_decision_id) if entry_decision_id else str(decision_id),
                        "live": True,
                    },
                )
            )
        else:
            pre_qty = float(prev_qty_local if prev_qty_local > 0 else current_qty)
            pre_avg = float(prev_avg_local if prev_avg_local > 0 else current_avg)
            remaining_qty = max(0.0, float(pre_qty) - float(total_qty))
            if remaining_qty > 1e-12:
                self._repo.upsert_position(
                    DbPosition(
                        symbol=symbol,
                        ts_updated=first_fill_ts,
                        qty=float(remaining_qty),
                        avg_entry_price=(float(pre_avg) if pre_avg > 0 else None),
                        unrealized_pnl=None,
                        stop_price=None,
                        take_profit=None,
                        meta={**pos_meta, "live": True, "trade_id": str(trade_id)},
                    )
                )
            else:
                fees_paid_buy = float(pos_meta.get("fees_paid_krw") or 0.0)
                fees_total = float(fees_paid_buy + total_fee)
                gross = (float(vwap) - float(pre_avg)) * float(total_qty)
                realized_pnl = float(gross - fees_total)
                notional = max(0.0, float(pre_avg) * float(total_qty))
                pnl_bps = (float(realized_pnl) / float(notional) * 10000.0) if notional > 0 else None
                tz_name = str(rules.raw.get("settlement", {}).get("timezone", "Asia/Seoul"))
                try:
                    tz = ZoneInfo(tz_name)
                except Exception:
                    tz = ZoneInfo("Asia/Seoul")
                day = first_fill_ts.astimezone(tz).date().isoformat()
                opened_at_raw = str(pos_meta.get("opened_at") or pos_meta.get("entry_ts") or "")
                try:
                    ts_open = datetime.fromisoformat(opened_at_raw.replace("Z", "+00:00")) if opened_at_raw else first_fill_ts
                except Exception:
                    ts_open = first_fill_ts

                self._repo.insert_realized_trade(
                    trade_id=trade_id,
                    symbol=symbol,
                    ts_open=ts_open,
                    ts_close=first_fill_ts,
                    side="LONG",
                    qty=float(total_qty),
                    avg_entry_price=float(pre_avg),
                    avg_exit_price=float(vwap),
                    realized_pnl=float(realized_pnl),
                    fees_total=float(fees_total),
                    pnl_bps=pnl_bps,
                    tags={},
                    meta={
                        "order_id": order_id,
                        "fill_id": str(first_fill_id),
                        "live": True,
                        "trade_id": str(trade_id),
                        "entry_decision_id": str(entry_decision_id) if entry_decision_id else None,
                        "exit_decision_id": str(decision_id),
                        "exit_reason": str(exit_reason or "SELL_SIGNAL").strip().upper(),
                    },
                )
                self._repo.upsert_pnl_daily_delta(
                    day=day,
                    realized_pnl_delta=float(realized_pnl),
                    fees_paid_delta=float(fees_total),
                    trades_count_delta=1,
                )
                self._repo.upsert_position(
                    DbPosition(
                        symbol=symbol,
                        ts_updated=first_fill_ts,
                        qty=0.0,
                        avg_entry_price=None,
                        unrealized_pnl=None,
                        stop_price=None,
                        take_profit=None,
                        meta={
                            "cooldown_until": (
                                (first_fill_ts + timedelta(minutes=max(0, int(cooldown_minutes or 0)))).isoformat()
                                if int(cooldown_minutes or 0) > 0
                                else None
                            ),
                            "last_exit_reason": str(exit_reason or "SELL_SIGNAL").strip().upper(),
                            "live": True,
                        },
                    )
                )
                closed_trade = LiveClosedTrade(
                    trade_id=trade_id,
                    entry_decision_id=entry_decision_id,
                    exit_decision_id=decision_id,
                    symbol=symbol,
                    ts_open=ts_open,
                    ts_close=first_fill_ts,
                    side="LONG",
                    qty=float(total_qty),
                    avg_entry_price=float(pre_avg),
                    avg_exit_price=float(vwap),
                    realized_pnl=float(realized_pnl),
                    fees_total=float(fees_total),
                    pnl_bps=pnl_bps,
                    exit_reason=str(exit_reason or "SELL_SIGNAL").strip().upper(),
                )

        latency_submit_ms = int((submit_ts - ts_decision).total_seconds() * 1000)
        latency_fill_ms = int((first_fill_ts - submit_ts).total_seconds() * 1000)
        self._repo.insert_execution_metric(
            DbExecutionMetric(
                metric_id=uuid.uuid4(),
                order_id=order_id,
                symbol=symbol,
                ts_decision=ts_decision,
                ts_submit=submit_ts,
                ts_first_fill=first_fill_ts,
                ts_last_fill=_utcnow(),
                decision_mid=float(snapshot.mid_price),
                submit_mid=float(submit_mid),
                fill_vwap=float(vwap),
                slippage_bps_vs_decision=_slippage_bps(float(vwap), float(snapshot.mid_price), side=side),
                slippage_bps_vs_submit=_slippage_bps(float(vwap), float(submit_mid), side=side),
                spread_bps_at_submit=float(snapshot.spread_bps),
                filled_ratio=(
                    float(total_qty / float(volume)) if (volume is not None and float(volume) > 0) else 1.0
                ),
                latency_ms_decision_to_submit=latency_submit_ms,
                latency_ms_submit_to_fill=latency_fill_ms,
                meta={"live": True, "state": machine.state.value},
            )
        )

        # Final source-of-truth sync after execution.
        sync_symbol_account_state(
            repo=self._repo,
            client=self._client,
            symbol=symbol,
            run_id=run_id,
            rule_version_id=rule_version_id,
        )

        assert first_fill_id is not None
        assert first_fill_event_id is not None
        return LiveExecutionResult(
            order_id=order_id,
            side=side,
            trade_id=trade_id,
            entry_decision_id=entry_decision_id,
            fill_event_id=first_fill_event_id,
            fill_id=first_fill_id,
            fill_price=float(vwap),
            fill_qty=float(total_qty),
            fee=float(total_fee),
            closed_trade=closed_trade,
        )
