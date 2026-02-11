#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402

load_dotenv()

from ai_invest.meetings.governance_meeting import maybe_run_scheduled_governance_meeting  # noqa: E402
from ai_invest.notifications.service import NotificationService  # noqa: E402
from ai_invest.storage.postgres import PostgresRepo  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Run governance loop (scheduled multi-agent meeting + trade plan).")
    p.add_argument("--sleep-sec", type=float, default=30.0)
    p.add_argument("--once", action="store_true", help="Run a single check cycle and exit.")
    p.add_argument("--force", action="store_true", help="Ignore schedule window and create a meeting/plan now.")
    args = p.parse_args()

    rules_raw: dict[str, Any] = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))

    repo = PostgresRepo()
    notifier = NotificationService(repo)

    if args.once:
        slot = maybe_run_scheduled_governance_meeting(repo=repo, notifier=notifier, rules_raw=rules_raw, force=bool(args.force))
        print(f"[완료] once done. slot={slot}")
        return 0

    print("[시작] governance loop running (meeting schedule + trade plan)")
    while True:
        try:
            slot = maybe_run_scheduled_governance_meeting(repo=repo, notifier=notifier, rules_raw=rules_raw, force=bool(args.force))
            if slot:
                print(f"[트리거] meeting slot processed: {slot}")
        except Exception as exc:
            # Fail-open for scheduler loop (should not kill the process).
            print(f"[경고] governance loop error: {exc}")
        time.sleep(float(args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())
