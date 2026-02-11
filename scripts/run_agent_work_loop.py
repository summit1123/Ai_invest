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
from ai_invest.storage.postgres import PostgresRepo  # noqa: E402
from ai_invest.work.agent_work_loop import run_agent_work_cycle  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Run agent prework loop (research/quant/risk/ops).")
    p.add_argument("--sleep-sec", type=float, default=1800.0, help="loop interval (default 30m)")
    p.add_argument("--once", action="store_true", help="run once and exit")
    p.add_argument("--meeting-context", type=str, default="", help="optional context tag (e.g. slot_key)")
    args = p.parse_args()

    load_dotenv()
    rules_raw: dict[str, Any] = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))
    repo = PostgresRepo()

    if args.once:
        out = run_agent_work_cycle(repo=repo, rules_raw=rules_raw, meeting_context=args.meeting_context.strip() or None)
        print(f"[완료] agent prework once: cycle_key={out.cycle_key}, reports={out.report_ids}")
        return 0

    print("[시작] agent prework loop running")
    while True:
        try:
            out = run_agent_work_cycle(repo=repo, rules_raw=rules_raw, meeting_context=args.meeting_context.strip() or None)
            print(f"[사이클] {out.cycle_key} reports={out.report_ids}")
        except Exception as exc:
            print(f"[경고] agent prework loop error: {exc}")
        time.sleep(float(args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())

