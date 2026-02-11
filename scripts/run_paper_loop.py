#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402

load_dotenv()

from ai_invest.runtime.paper_loop import run_paper_loop  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cycles", type=int, default=1)
    p.add_argument("--sleep-sec", type=float, default=0.0)
    args = p.parse_args()

    run_paper_loop(cycles=args.cycles, sleep_sec=args.sleep_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
