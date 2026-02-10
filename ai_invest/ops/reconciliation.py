from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ai_invest.storage.postgres import DbReconciliationCheck, PostgresRepo


@dataclass(frozen=True)
class ReconResult:
    status: str  # OK / FAIL
    diff_summary: str | None
    diff_payload: dict[str, Any]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_positions_vs_fills(repo: PostgresRepo, *, symbol: str | None = None) -> ReconResult:
    # For v1, reconcile only net position qty.
    # positions.qty should equal sum(fills.qty signed by order side).
    with repo.connect() as conn, conn.cursor() as cur:
        params: list[Any] = []
        where = ""
        if symbol:
            where = "where o.symbol=%s"
            params.append(symbol)
        cur.execute(
            f"""
            select
              o.symbol,
              sum(case when o.side='BUY' then f.quantity else -f.quantity end) as net_qty
            from orders o
            join fills f on f.order_id=o.order_id
            {where}
            group by o.symbol
            """,
            params,
        )
        rows = cur.fetchall()

    diffs: dict[str, Any] = {}
    ok = True
    for sym, net_qty in rows:
        pos = repo.fetch_position(sym)
        pos_qty = float(pos.qty) if pos else 0.0
        expected = float(net_qty or 0.0)
        diff = pos_qty - expected
        diffs[sym] = {"pos_qty": pos_qty, "fills_net_qty": expected, "diff": diff}
        if abs(diff) > 1e-9:
            ok = False

    if ok:
        return ReconResult(status="OK", diff_summary=None, diff_payload=diffs)
    return ReconResult(status="FAIL", diff_summary="positions.qty != fills net qty", diff_payload=diffs)


def record_reconciliation_check(
    repo: PostgresRepo,
    *,
    run_id: uuid.UUID,
    symbol: str | None = None,
) -> DbReconciliationCheck:
    result = reconcile_positions_vs_fills(repo, symbol=symbol)
    check = DbReconciliationCheck(
        check_id=uuid.uuid4(),
        ts=_utcnow(),
        scope="POSITION",
        symbol=symbol,
        status=result.status,
        diff_summary=result.diff_summary,
        diff_payload=result.diff_payload,
        action_taken="PAUSE" if result.status == "FAIL" else "NONE",
        run_id=run_id,
    )
    repo.insert_reconciliation_check(check)
    return check

