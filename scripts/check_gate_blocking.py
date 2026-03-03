#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.storage.postgres import PostgresRepo  # noqa: E402

load_dotenv()


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return bool(value)
    if value is None:
        return bool(default)
    s = str(value).strip().lower()
    if not s:
        return bool(default)
    return s in {"1", "true", "yes", "y", "on"}


def _as_ts_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return float(part) / float(total) * 100.0


def main() -> int:
    p = argparse.ArgumentParser(description="Check if SAFE decision gates are over-blocking entries.")
    p.add_argument("--hours", type=int, default=24, help="Lookback window in hours.")
    p.add_argument("--limit", type=int, default=40000, help="Max SAFE decisions to scan.")
    p.add_argument("--rules", type=Path, default=Path("rules.yaml"), help="Rules file path.")
    args = p.parse_args()

    lookback_h = max(1, int(args.hours))
    max_rows = max(100, int(args.limit))
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_h)

    rules_raw: dict[str, Any] = {}
    try:
        rules_raw = yaml.safe_load(args.rules.read_text(encoding="utf-8")) or {}
    except Exception:
        rules_raw = {}
    cfg = (rules_raw.get("cost_guard") or {}) if isinstance(rules_raw, Mapping) else {}
    spread_limit = float(cfg.get("max_spread_bps_entry") or 0.0)

    repo = PostgresRepo()
    rows = repo.fetch_decisions(judge_type="SAFE", limit=max_rows)

    total = 0
    actions = {"BUY": 0, "SELL": 0, "HOLD": 0, "PAUSE": 0}
    reasons: dict[str, int] = {}
    gate_counts = {
        "regime_blocked": 0,
        "risk_veto": 0,
        "ops_veto": 0,
        "market_edge_blocked": 0,
        "market_cost_blocked": 0,
    }
    for row in rows:
        ts = _as_ts_utc(row.get("ts"))
        if ts is None or ts < since:
            continue
        total += 1
        action = str(row.get("action") or "").strip().upper()
        if action in actions:
            actions[action] += 1
        for rc in [str(x).strip().upper() for x in list(row.get("selected_reasons") or []) if str(x).strip()]:
            reasons[rc] = int(reasons.get(rc, 0) + 1)
        gates = dict(row.get("gates") or {}) if isinstance(row.get("gates"), Mapping) else {}
        if _as_bool(gates.get("regime_trade_allowed"), default=True) is False:
            gate_counts["regime_blocked"] += 1
        if _as_bool(gates.get("risk_veto"), default=False):
            gate_counts["risk_veto"] += 1
        if _as_bool(gates.get("ops_veto"), default=False):
            gate_counts["ops_veto"] += 1
        if _as_bool(gates.get("market_edge_gate_blocked"), default=False):
            gate_counts["market_edge_blocked"] += 1
        if _as_bool(gates.get("market_cost_gate_blocked"), default=False):
            gate_counts["market_cost_blocked"] += 1

    hold_ratio = _pct(actions["HOLD"], total)
    buy_ratio = _pct(actions["BUY"], total)
    pause_ratio = _pct(actions["PAUSE"], total)
    over_blocked = bool(total >= 100 and hold_ratio >= 95.0 and buy_ratio <= 1.5)

    ranked_reasons = sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:8]
    print(f"[window] last={lookback_h}h total={total}")
    print(f"[actions] BUY={actions['BUY']} ({buy_ratio:.2f}%) HOLD={actions['HOLD']} ({hold_ratio:.2f}%) PAUSE={actions['PAUSE']} ({pause_ratio:.2f}%) SELL={actions['SELL']}")
    print(f"[gates] {gate_counts}")
    print("[reasons-top]")
    for code, cnt in ranked_reasons:
        print(f"- {code}: {cnt} ({_pct(cnt, total):.2f}%)")

    print("[verdict]")
    if over_blocked:
        print("OVER_BLOCKED: HOLD 비중이 과도합니다. entry_alpha/edge/spread 정책 재조정이 필요합니다.")
    else:
        print("NORMAL_RANGE: 과차단 임계치를 넘지 않았습니다.")

    if spread_limit > 0:
        spread_hits = reasons.get("RG_SPREAD_TOO_WIDE", 0)
        print(
            f"[hint] spread_limit={spread_limit:.2f}bps, RG_SPREAD_TOO_WIDE share={_pct(spread_hits, total):.2f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
