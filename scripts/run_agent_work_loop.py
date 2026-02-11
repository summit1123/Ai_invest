#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
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
    p.add_argument(
        "--align-to-clock",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="true면 interval 경계(정시) 기준으로 실행",
    )
    p.add_argument(
        "--run-immediate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="루프 시작 직후 1회 즉시 실행 여부",
    )
    p.add_argument(
        "--agent",
        type=str,
        default="all",
        choices=["all", "research", "quant", "risk", "ops"],
        help="target agent worker (all|research|quant|risk|ops)",
    )
    p.add_argument("--meeting-context", type=str, default="", help="optional context tag (e.g. slot_key)")
    args = p.parse_args()

    load_dotenv()
    rules_raw: dict[str, Any] = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))
    repo = PostgresRepo()
    agent_map = {
        "all": ["research_agent", "quant_strategist", "risk_manager", "ops_manager"],
        "research": ["research_agent"],
        "quant": ["quant_strategist"],
        "risk": ["risk_manager"],
        "ops": ["ops_manager"],
    }
    selected = agent_map[str(args.agent).strip().lower()]

    if args.once:
        out = run_agent_work_cycle(
            repo=repo,
            rules_raw=rules_raw,
            meeting_context=args.meeting_context.strip() or None,
            selected_agents=selected,
        )
        print(f"[완료] agent prework once: cycle_key={out.cycle_key}, reports={out.report_ids}")
        return 0

    interval_sec = max(1.0, float(args.sleep_sec))

    def _next_aligned(ts: float) -> float:
        return (math.floor(ts / interval_sec) + 1.0) * interval_sec

    print(
        f"[시작] agent prework loop running: agent={args.agent}, interval={interval_sec:.1f}s, align={bool(args.align_to_clock)}",
        flush=True,
    )
    now_ts = time.time()
    if bool(args.run_immediate):
        next_run_ts = now_ts
    else:
        next_run_ts = _next_aligned(now_ts) if bool(args.align_to_clock) else (now_ts + interval_sec)

    while True:
        wait_sec = float(next_run_ts - time.time())
        if wait_sec > 0:
            time.sleep(wait_sec)
        try:
            out = run_agent_work_cycle(
                repo=repo,
                rules_raw=rules_raw,
                meeting_context=args.meeting_context.strip() or None,
                selected_agents=selected,
            )
            print(f"[사이클] {out.cycle_key} reports={out.report_ids}")
        except Exception as exc:
            print(f"[경고] agent prework loop error: {exc}")
        now_ts = time.time()
        next_run_ts = _next_aligned(now_ts) if bool(args.align_to_clock) else (now_ts + interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
