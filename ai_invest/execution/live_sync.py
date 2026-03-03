from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from ai_invest.execution.upbit_private import UpbitPrivateClient
from ai_invest.storage.postgres import DbEvent, DbLedgerEntry, DbPosition, PostgresRepo


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def _base_currency(symbol: str) -> str:
    if "-" not in symbol:
        return str(symbol).strip().upper()
    return symbol.split("-", 1)[1].strip().upper()


@dataclass(frozen=True)
class LiveSymbolAccountState:
    symbol: str
    quote_currency: str
    quote_balance_available: float
    quote_balance_locked: float
    base_currency: str
    base_qty_total: float
    base_qty_available: float
    base_qty_locked: float
    base_avg_buy_price: float | None


def extract_live_symbol_state(*, symbol: str, accounts: list[Mapping[str, Any]]) -> LiveSymbolAccountState:
    quote = _quote_currency(symbol)
    base = _base_currency(symbol)
    by_ccy: dict[str, Mapping[str, Any]] = {}
    for row in accounts:
        ccy = str(row.get("currency") or "").strip().upper()
        if not ccy:
            continue
        by_ccy[ccy] = row

    quote_row = by_ccy.get(quote) or {}
    base_row = by_ccy.get(base) or {}

    quote_bal = _as_float(quote_row.get("balance"), default=0.0)
    quote_locked = _as_float(quote_row.get("locked"), default=0.0)

    base_bal = _as_float(base_row.get("balance"), default=0.0)
    base_locked = _as_float(base_row.get("locked"), default=0.0)
    base_total = float(base_bal + base_locked)
    base_avg = _as_float(base_row.get("avg_buy_price"), default=0.0)

    return LiveSymbolAccountState(
        symbol=str(symbol).strip().upper(),
        quote_currency=quote,
        quote_balance_available=float(max(0.0, quote_bal)),
        quote_balance_locked=float(max(0.0, quote_locked)),
        base_currency=base,
        base_qty_total=float(max(0.0, base_total)),
        base_qty_available=float(max(0.0, base_bal)),
        base_qty_locked=float(max(0.0, base_locked)),
        base_avg_buy_price=(float(base_avg) if base_avg > 0 else None),
    )


def sync_symbol_account_state(
    *,
    repo: PostgresRepo,
    client: UpbitPrivateClient,
    symbol: str,
    run_id: uuid.UUID | None = None,
    rule_version_id: uuid.UUID | None = None,
) -> LiveSymbolAccountState:
    accounts = [dict(x) for x in client.get_accounts()]
    state = extract_live_symbol_state(symbol=symbol, accounts=accounts)
    now = _utcnow()

    local_cash = float(repo.fetch_cash_balance(currency=state.quote_currency))
    cash_delta = float(state.quote_balance_available - local_cash)
    if abs(cash_delta) > 1e-8:
        repo.insert_ledger_entry(
            DbLedgerEntry(
                entry_id=uuid.uuid4(),
                ts=now,
                entry_type="ADJUSTMENT",
                symbol=None,
                currency=state.quote_currency,
                amount=float(cash_delta),
                price=None,
                fee_amount=None,
                fee_currency=None,
                order_id=None,
                fill_id=None,
                meta={
                    "live_sync": True,
                    "source": "upbit_accounts",
                    "symbol": state.symbol,
                    "quote_balance_available": state.quote_balance_available,
                    "quote_balance_locked": state.quote_balance_locked,
                },
            )
        )

    prev = repo.fetch_position(state.symbol)
    prev_meta = dict((prev.meta or {}) if prev else {})
    meta = dict(prev_meta)
    meta["live_sync"] = True
    meta["live_synced_at"] = now.isoformat()
    meta["base_qty_available"] = float(state.base_qty_available)
    meta["base_qty_locked"] = float(state.base_qty_locked)

    if state.base_qty_total > 0:
        if state.base_avg_buy_price is not None:
            meta["entry_price"] = float(state.base_avg_buy_price)
        if not meta.get("entry_ts"):
            meta["entry_ts"] = now.isoformat()
        if not meta.get("hwm_price"):
            ref = state.base_avg_buy_price if state.base_avg_buy_price is not None else (prev.avg_entry_price if prev else None)
            if ref is not None:
                meta["hwm_price"] = float(ref)
    else:
        meta.pop("trade_id", None)
        meta.pop("entry_decision_id", None)

    repo.upsert_position(
        DbPosition(
            symbol=state.symbol,
            ts_updated=now,
            qty=float(state.base_qty_total),
            avg_entry_price=state.base_avg_buy_price if state.base_qty_total > 0 else None,
            unrealized_pnl=None,
            stop_price=prev.stop_price if prev else None,
            take_profit=prev.take_profit if prev else None,
            meta=meta,
        )
    )

    repo.insert_event(
        DbEvent(
            event_id=uuid.uuid4(),
            ts=now,
            event_type="LIVE_ACCOUNT_SYNC",
            entity_type="positions",
            entity_id=state.symbol,
            run_id=run_id,
            rule_version_id=rule_version_id,
            payload={
                "symbol": state.symbol,
                "quote_currency": state.quote_currency,
                "quote_balance_available": float(state.quote_balance_available),
                "quote_balance_locked": float(state.quote_balance_locked),
                "base_currency": state.base_currency,
                "base_qty_total": float(state.base_qty_total),
                "base_qty_available": float(state.base_qty_available),
                "base_qty_locked": float(state.base_qty_locked),
                "base_avg_buy_price": state.base_avg_buy_price,
                "cash_delta_local_adjustment": float(cash_delta),
            },
        )
    )
    return state
