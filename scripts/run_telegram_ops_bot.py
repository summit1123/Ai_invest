#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402

load_dotenv()

from ai_invest.notifications.telegram_ops_bot import (  # noqa: E402
    load_telegram_ops_bot_config,
    run_telegram_ops_bot,
)


def main() -> int:
    p = argparse.ArgumentParser(description="Run read-only Telegram ops bot.")
    p.add_argument("--status-path", type=str, default="runtime/orchestrator_status.json")
    p.add_argument("--poll-timeout-sec", type=int, default=30)
    args = p.parse_args()

    config = load_telegram_ops_bot_config(
        status_path=Path(str(args.status_path)),
        poll_timeout_sec=int(args.poll_timeout_sec),
    )
    run_telegram_ops_bot(config=config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
