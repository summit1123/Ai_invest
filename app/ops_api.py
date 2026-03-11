from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from ai_invest.config.dotenv import load_dotenv
from ai_invest.ops.read_api import (
    build_no_trade_snapshot,
    build_ops_status_snapshot,
    build_pause_explanation,
    build_pnl_snapshot,
    build_state_at,
    build_state_compare,
    utc_now_iso,
)
from ai_invest.storage.postgres import PostgresRepo

load_dotenv()

app = FastAPI(title="ai-invest-ops-api", version="0.1.0")


def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "meta": {"ts_utc": utc_now_iso(), "request_id": str(uuid.uuid4())}}


def _status_path() -> Path:
    return Path(os.environ.get("ORCHESTRATOR_STATUS_PATH", "runtime/orchestrator_status.json"))


def _require_ops_api_key(x_ops_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = str(os.environ.get("OPS_READ_API_KEY", "")).strip()
    if not expected:
        return
    if str(x_ops_api_key or "").strip() != expected:
        raise HTTPException(status_code=401, detail="invalid ops api key")


def _parse_ts_arg(name: str, value: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail=f"{name} is required")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid {name}: {exc}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@app.get("/healthz", dependencies=[Depends(_require_ops_api_key)], tags=["ops"])
def healthz() -> dict[str, Any]:
    return ok({"status": "ok"})


@app.get("/api/v1/ops/status", dependencies=[Depends(_require_ops_api_key)], tags=["ops"])
def ops_status() -> dict[str, Any]:
    repo = PostgresRepo()
    return ok(build_ops_status_snapshot(repo=repo, status_path=_status_path()))


@app.get("/api/v1/ops/why-paused", dependencies=[Depends(_require_ops_api_key)], tags=["ops"])
def why_paused() -> dict[str, Any]:
    repo = PostgresRepo()
    return ok(build_pause_explanation(repo=repo, status_path=_status_path()))


@app.get("/api/v1/ops/pnl-today", dependencies=[Depends(_require_ops_api_key)], tags=["ops"])
def pnl_today(
    trade_limit: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok(build_pnl_snapshot(repo=repo, trade_limit=int(trade_limit)))


@app.get("/api/v1/ops/recent-trades", dependencies=[Depends(_require_ops_api_key)], tags=["ops"])
def recent_trades(
    limit: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_realized_trades(limit=int(limit))})


@app.get("/api/v1/ops/why-no-trade", dependencies=[Depends(_require_ops_api_key)], tags=["ops"])
def why_no_trade(
    symbol: str = Query("KRW-BTC"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok(build_no_trade_snapshot(repo=repo, symbol=symbol, status_path=_status_path()))


@app.get("/api/v1/ops/state-at", dependencies=[Depends(_require_ops_api_key)], tags=["ops"])
def state_at(
    ts: str = Query(..., description="ISO-8601 timestamp"),
    symbol: str = Query("KRW-BTC"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok(build_state_at(repo=repo, ts_at=_parse_ts_arg("ts", ts), symbol=symbol))


@app.get("/api/v1/ops/compare", dependencies=[Depends(_require_ops_api_key)], tags=["ops"])
def compare_states(
    from_ts: str = Query(..., description="Start ISO-8601 timestamp"),
    to_ts: str = Query(..., description="End ISO-8601 timestamp"),
    symbol: str = Query("KRW-BTC"),
) -> dict[str, Any]:
    from_dt = _parse_ts_arg("from_ts", from_ts)
    to_dt = _parse_ts_arg("to_ts", to_ts)
    if from_dt > to_dt:
        raise HTTPException(status_code=400, detail="from_ts must be earlier than or equal to to_ts")
    repo = PostgresRepo()
    return ok(build_state_compare(repo=repo, from_ts=from_dt, to_ts=to_dt, symbol=symbol))
