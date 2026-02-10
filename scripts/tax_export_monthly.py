#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.notifications.service import NotificationService  # noqa: E402
from ai_invest.storage.postgres import DbEvent, PostgresRepo  # noqa: E402


KST = ZoneInfo("Asia/Seoul")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def month_period_kst(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=KST)
    end = next_month - timedelta(seconds=1)
    return start, end


def fmt_money(value: float | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.2f}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def overall_checksum(file_checksums: dict[str, str]) -> str:
    # Stable overall checksum, independent of dict order.
    payload = "\n".join(f"{name}:{digest}" for name, digest in sorted(file_checksums.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def export_monthly(*, repo: PostgresRepo, year: int, month: int, out_dir: Path, generated_by: str) -> dict[str, Any]:
    export_id = uuid.uuid4()
    period_start_kst, period_end_kst = month_period_kst(year, month)
    period_start_utc = period_start_kst.astimezone(timezone.utc)
    period_end_utc = period_end_kst.astimezone(timezone.utc)

    # Track start immediately (audit trail), even if later failures occur.
    repo.upsert_tax_export_run(
        export_id=export_id,
        period_start=period_start_utc,
        period_end=period_end_utc,
        status="STARTED",
        checksum_sha256=None,
        generated_by=generated_by,
        manifest={
            "export_id": str(export_id),
            "period_start": period_start_kst.isoformat(),
            "period_end": period_end_kst.isoformat(),
            "generated_at": _utcnow().astimezone(KST).isoformat(),
            "source_tables": ["ledger_entries", "realized_trades", "fills", "pnl_daily"],
            "row_counts": {},
            "status": "STARTED",
        },
        meta={"out_dir": str(out_dir)},
    )

    try:
        # Fetch rows (period boundary is KST, but DB stores timestamptz).
        with repo.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select trade_id, symbol, ts_open, ts_close, side, qty,
                       avg_entry_price, avg_exit_price, realized_pnl, fees_total, pnl_bps, tags
                from realized_trades
                where ts_close >= %s and ts_close <= %s
                order by ts_close asc
                """,
                (period_start_utc, period_end_utc),
            )
            trades = cur.fetchall()

            cur.execute(
                """
                select entry_id, ts, entry_type, symbol, currency, amount, price, fee_amount, fee_currency, order_id, fill_id
                from ledger_entries
                where ts >= %s and ts <= %s
                order by ts asc
                """,
                (period_start_utc, period_end_utc),
            )
            ledger = cur.fetchall()

            cur.execute(
                """
                select fill_id, order_id, ts_filled, price, quantity, fee, fee_currency
                from fills
                where ts_filled >= %s and ts_filled <= %s
                """,
                (period_start_utc, period_end_utc),
            )
            fills = cur.fetchall()

            start_day: date = period_start_kst.date()
            end_day: date = period_end_kst.date()
            cur.execute(
                """
                select day, realized_pnl, fees_paid, trades_count, max_drawdown
                from pnl_daily
                where day >= %s and day <= %s
                order by day asc
                """,
                (start_day, end_day),
            )
            pnl_days = cur.fetchall()
    except Exception as exc:
        manifest = {
            "export_id": str(export_id),
            "period_start": period_start_kst.isoformat(),
            "period_end": period_end_kst.isoformat(),
            "generated_at": _utcnow().astimezone(KST).isoformat(),
            "source_tables": ["ledger_entries", "realized_trades", "fills", "pnl_daily"],
            "row_counts": {},
            "file_checksums": {},
            "checksum_sha256": None,
            "generated_by": generated_by,
            "status": "FAILED",
            "validation_report": {},
            "errors": ["EXCEPTION"],
            "exception": str(exc)[:500],
        }
        repo.upsert_tax_export_run(
            export_id=export_id,
            period_start=period_start_utc,
            period_end=period_end_utc,
            status="FAILED",
            checksum_sha256=None,
            generated_by=generated_by,
            manifest=manifest,
            meta={"out_dir": str(out_dir)},
        )
        return {"export_id": str(export_id), "status": "FAILED", "out_dir": str(out_dir), "manifest": manifest}

    ym = f"{year:04d}_{month:02d}"
    trades_path = out_dir / f"tax_trades_{ym}.csv"
    ledger_path = out_dir / f"tax_ledger_{ym}.csv"
    summary_path = out_dir / f"tax_summary_{ym}.csv"
    manifest_path = out_dir / f"tax_manifest_{ym}.json"

    trade_rows: list[list[str]] = []
    for trade_id, symbol, ts_open, ts_close, side, qty, avg_entry, avg_exit, realized_pnl, fees_total, pnl_bps, tags in trades:
        trade_rows.append(
            [
                str(trade_id),
                symbol,
                ts_open.astimezone(KST).isoformat(),
                ts_close.astimezone(KST).isoformat(),
                side,
                repr(float(qty)),
                repr(float(avg_entry)),
                repr(float(avg_exit)),
                fmt_money(float(realized_pnl)),
                fmt_money(float(fees_total)),
                "" if pnl_bps is None else repr(float(pnl_bps)),
                json.dumps(tags or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ]
        )

    ledger_rows: list[list[str]] = []
    for entry_id, ts, entry_type, symbol, currency, amount, price, fee_amount, fee_currency, order_id, fill_id in ledger:
        ledger_rows.append(
            [
                str(entry_id),
                ts.astimezone(KST).isoformat(),
                entry_type,
                symbol or "",
                currency,
                fmt_money(float(amount)) if currency == "KRW" else repr(float(amount)),
                "" if price is None else repr(float(price)),
                "" if fee_amount is None else fmt_money(float(fee_amount)),
                fee_currency or "",
                order_id or "",
                "" if fill_id is None else str(fill_id),
            ]
        )

    # Summary
    realized_pnl_total = sum(float(t[8]) for t in trades)
    fees_total = sum(float(t[9]) for t in trades)
    trade_count = len(trades)
    winning = sum(1 for t in trades if float(t[8]) > 1.0)
    losing = sum(1 for t in trades if float(t[8]) < -1.0)
    max_drawdown_vals = [float(dd) for _day, _rp, _fp, _tc, dd in pnl_days if dd is not None]
    max_drawdown = max(max_drawdown_vals) if max_drawdown_vals else ""

    summary_rows = [
        [
            ym,
            fmt_money(realized_pnl_total),
            fmt_money(fees_total),
            str(trade_count),
            str(winning),
            str(losing),
            "" if max_drawdown == "" else fmt_money(max_drawdown),
        ]
    ]

    write_csv(
        trades_path,
        [
            "trade_id",
            "symbol",
            "ts_open_kst",
            "ts_close_kst",
            "side",
            "qty",
            "avg_entry_price",
            "avg_exit_price",
            "realized_pnl_krw",
            "fees_total_krw",
            "pnl_bps",
            "tags_json",
        ],
        trade_rows,
    )
    write_csv(
        ledger_path,
        [
            "entry_id",
            "ts_kst",
            "entry_type",
            "symbol",
            "currency",
            "amount",
            "price",
            "fee_amount",
            "fee_currency",
            "order_id",
            "fill_id",
        ],
        ledger_rows,
    )
    write_csv(
        summary_path,
        [
            "month",
            "realized_pnl_total_krw",
            "fees_total_krw",
            "trade_count",
            "winning_trade_count",
            "losing_trade_count",
            "max_drawdown",
        ],
        summary_rows,
    )

    file_checksums = {
        trades_path.name: sha256_file(trades_path),
        ledger_path.name: sha256_file(ledger_path),
        summary_path.name: sha256_file(summary_path),
    }

    # Validation report (v1): simple invariants + discrepancy surfacing.
    pnl_daily_realized = sum(float(rp) for _d, rp, _f, _tc, _dd in pnl_days)
    pnl_daily_fees = sum(float(fp) for _d, _rp, fp, _tc, _dd in pnl_days)
    fill_ids = [str(fid) for fid, _oid, _ts, _p, _q, _f, _fc in fills]
    dup_fill_count = len(fill_ids) - len(set(fill_ids))

    # Ledger net cashflow approximation (quote currency). Works when entry+exit fills both fall in the month.
    ledger_net_krw = 0.0
    for _eid, _ts, entry_type, _sym, currency, amount, _price, fee_amount, _fee_ccy, _oid, _fid in ledger:
        if entry_type != "TRADE_FILL" or currency != "KRW":
            continue
        ledger_net_krw += float(amount) - float(fee_amount or 0.0)

    validation_report: dict[str, Any] = {
        "sums": {
            "realized_trades_realized_pnl_total": realized_pnl_total,
            "pnl_daily_realized_pnl_total": pnl_daily_realized,
            "realized_trades_fees_total": fees_total,
            "pnl_daily_fees_total": pnl_daily_fees,
            "ledger_net_krw_approx": ledger_net_krw,
        },
        "diffs": {
            "realized_minus_pnl_daily": realized_pnl_total - pnl_daily_realized,
            "fees_minus_pnl_daily": fees_total - pnl_daily_fees,
            "realized_minus_ledger_net_approx": realized_pnl_total - ledger_net_krw,
        },
        "integrity": {"duplicate_fill_ids": dup_fill_count},
        "notes": [
            "ledger_net_krw_approx compares only TRADE_FILL rows in KRW, and assumes entry+exit fills are in-period.",
        ],
    }

    errors: list[str] = []
    if abs(realized_pnl_total - pnl_daily_realized) > 1.0:
        errors.append("SUM_MISMATCH_REALIZED_TRADES_VS_PNL_DAILY")
    if abs(fees_total - pnl_daily_fees) > 1.0:
        errors.append("SUM_MISMATCH_FEES_VS_PNL_DAILY")
    if dup_fill_count:
        errors.append("DUPLICATE_FILL_IDS")

    status = "COMPLETED" if not errors else "FAILED"

    manifest = {
        "export_id": str(export_id),
        "period_start": period_start_kst.isoformat(),
        "period_end": period_end_kst.isoformat(),
        "generated_at": _utcnow().astimezone(KST).isoformat(),
        "source_tables": ["ledger_entries", "realized_trades", "fills", "pnl_daily"],
        "row_counts": {
            "ledger_entries": len(ledger),
            "realized_trades": len(trades),
            "fills": len(fills),
            "pnl_daily": len(pnl_days),
        },
        "file_checksums": file_checksums,
        "checksum_sha256": overall_checksum(file_checksums),
        "generated_by": generated_by,
        "status": status,
        "validation_report": validation_report,
        "errors": errors,
    }

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")

    repo.upsert_tax_export_run(
        export_id=export_id,
        period_start=period_start_utc,
        period_end=period_end_utc,
        status=status,
        checksum_sha256=str(manifest["checksum_sha256"]),
        generated_by=generated_by,
        manifest=manifest,
        meta={"out_dir": str(out_dir), "files": sorted(file_checksums.keys()), "manifest_file": manifest_path.name},
    )

    return {"export_id": str(export_id), "status": status, "out_dir": str(out_dir), "manifest": manifest}


def main() -> int:
    p = argparse.ArgumentParser(description="Generate monthly tax exports (CSV + manifest) from Postgres.")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--month", type=int, required=True)
    p.add_argument("--out-dir", type=str, default="exports/tax")
    p.add_argument("--generated-by", type=str, default="system")
    args = p.parse_args()

    if args.month < 1 or args.month > 12:
        raise SystemExit("--month must be 1..12")

    load_dotenv()
    repo = PostgresRepo()
    notifier = NotificationService(repo)

    out_dir = Path(args.out_dir)

    event_id = uuid.uuid4()
    try:
        res = export_monthly(repo=repo, year=args.year, month=args.month, out_dir=out_dir, generated_by=args.generated_by)
        repo.insert_event(
            DbEvent(
                event_id=event_id,
                ts=_utcnow(),
                event_type="TAX_EXPORT_COMPLETED" if res["status"] == "COMPLETED" else "TAX_EXPORT_FAILED",
                entity_type="tax_export_runs",
                entity_id=str(res["export_id"]),
                run_id=None,
                rule_version_id=None,
                payload={"export_id": res["export_id"], "status": res["status"], "out_dir": res["out_dir"]},
            )
        )
        if res["status"] == "COMPLETED":
            try:
                notifier.notify_tax_export_done(event_id=event_id, export_id=res["export_id"], year=args.year, month=args.month)
            except Exception:
                pass
        else:
            try:
                notifier.notify_tax_export_fail(
                    event_id=event_id, export_id=res["export_id"], year=args.year, month=args.month, errors=res["manifest"].get("errors", [])
                )
            except Exception:
                pass
        return 0 if res["status"] == "COMPLETED" else 2
    except Exception as exc:
        repo.insert_event(
            DbEvent(
                event_id=event_id,
                ts=_utcnow(),
                event_type="TAX_EXPORT_FAILED",
                entity_type="tax_export_runs",
                entity_id="ERROR",
                run_id=None,
                rule_version_id=None,
                payload={"error": str(exc)[:500]},
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
