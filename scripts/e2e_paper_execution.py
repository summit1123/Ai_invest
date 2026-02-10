#!/usr/bin/env python3
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.config.rules_loader import load_rules  # noqa: E402
from ai_invest.execution.paper_execution import PaperExecutor  # noqa: E402
from ai_invest.market_data.upbit_public import MarketSnapshot  # noqa: E402
from ai_invest.storage.postgres import PostgresRepo  # noqa: E402


def main() -> int:
    load_dotenv()
    rules = load_rules("rules.yaml")
    repo = PostgresRepo()
    ex = PaperExecutor(repo)

    run_id = uuid.uuid4()
    rule_version_id = uuid.uuid4()
    decision_id = uuid.uuid4()

    snap = MarketSnapshot(
        ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        symbol=rules.universe.symbols[0],
        last_price=100.0,
        best_bid=99.0,
        best_ask=101.0,
    )

    buy = ex.execute(
        run_id=run_id,
        rule_version_id=rule_version_id,
        decision_id=decision_id,
        action="BUY",
        snapshot=snap,
        rules=rules,
    )
    if buy is None:
        print("[실패] BUY execution returned None")
        return 2

    sell = ex.execute(
        run_id=run_id,
        rule_version_id=rule_version_id,
        decision_id=uuid.uuid4(),
        action="SELL",
        snapshot=snap,
        rules=rules,
    )
    if sell is None:
        print("[실패] SELL execution returned None (position not detected?)")
        return 2

    print("[성공] paper BUY+SELL executed")
    print(f"- buy.order_id={buy.order_id}, buy.fill_id={buy.fill_id}")
    print(f"- sell.order_id={sell.order_id}, sell.fill_id={sell.fill_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

