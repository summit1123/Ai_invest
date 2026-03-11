#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.runtime.preflight import build_startup_preflight, format_preflight_report  # noqa: E402

load_dotenv()

from ai_invest.runtime.paper_loop import run_paper_loop  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cycles", type=int, default=1)
    p.add_argument("--sleep-sec", type=float, default=0.0)
    p.add_argument("--skip-preflight", action="store_true")
    args = p.parse_args()

    if not bool(args.skip_preflight):
        rules_raw = yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8")) or {}
        report = build_startup_preflight(rules_raw=rules_raw, require_trading=True)
        for line in format_preflight_report(report):
            print(line, flush=True)
        if not report.ok:
            return 2

    run_paper_loop(cycles=args.cycles, sleep_sec=args.sleep_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
