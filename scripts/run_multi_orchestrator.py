#!/usr/bin/env python3
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_rules() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8")) or {}


def _build_commands(
    *,
    python_bin: str,
    decision_interval_sec: float,
    work_interval_sec: float,
    governance_sleep_sec: float,
    enable_paper: bool,
    enable_work: bool,
    enable_governance: bool,
) -> list[tuple[str, list[str]]]:
    cmds: list[tuple[str, list[str]]] = []
    if enable_paper:
        cmds.append(
            (
                "paper_loop",
                [
                    python_bin,
                    str(ROOT / "scripts" / "run_paper_loop.py"),
                    "--cycles",
                    "1000000000",
                    "--sleep-sec",
                    str(float(decision_interval_sec)),
                ],
            )
        )
    if enable_work:
        cmds.append(
            (
                "work_loop",
                [
                    python_bin,
                    str(ROOT / "scripts" / "run_agent_work_loop.py"),
                    "--sleep-sec",
                    str(float(work_interval_sec)),
                ],
            )
        )
    if enable_governance:
        cmds.append(
            (
                "governance_loop",
                [
                    python_bin,
                    str(ROOT / "scripts" / "run_governance_loop.py"),
                    "--sleep-sec",
                    str(float(governance_sleep_sec)),
                ],
            )
        )
    return cmds


def main() -> int:
    p = argparse.ArgumentParser(description="Run unified multi-agent orchestrator (paper/work/governance loops).")
    p.add_argument("--decision-interval-sec", type=float, default=-1.0, help="paper loop decision interval")
    p.add_argument("--work-interval-sec", type=float, default=1800.0, help="prework loop interval")
    p.add_argument("--governance-sleep-sec", type=float, default=30.0, help="governance scheduler check interval")
    p.add_argument("--restart-delay-sec", type=float, default=5.0, help="delay before restarting a crashed worker")
    p.add_argument("--no-paper", action="store_true", help="disable paper loop worker")
    p.add_argument("--no-work", action="store_true", help="disable prework loop worker")
    p.add_argument("--no-governance", action="store_true", help="disable governance loop worker")
    p.add_argument("--dry-run", action="store_true", help="print commands and exit")
    args = p.parse_args()

    rules = _load_rules()
    default_decision_interval = float(((rules.get("scheduling") or {}).get("decision_interval_sec") or 15))
    decision_interval = float(args.decision_interval_sec if args.decision_interval_sec > 0 else default_decision_interval)

    commands = _build_commands(
        python_bin=sys.executable,
        decision_interval_sec=decision_interval,
        work_interval_sec=float(args.work_interval_sec),
        governance_sleep_sec=float(args.governance_sleep_sec),
        enable_paper=not bool(args.no_paper),
        enable_work=not bool(args.no_work),
        enable_governance=not bool(args.no_governance),
    )

    if not commands:
        print("[중단] 실행할 워커가 없습니다. --no-* 옵션을 확인하세요.")
        return 2

    print("[설정] unified orchestrator workers")
    for name, cmd in commands:
        print(f"- {name}: {' '.join(cmd)}")

    if args.dry_run:
        return 0

    procs: dict[str, subprocess.Popen[str]] = {}
    stopping = False

    def _start_worker(name: str, cmd: list[str]) -> None:
        print(f"[시작] {name}")
        procs[name] = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=None,
            stderr=None,
            text=True,
        )

    def _stop_all(sig: int | None = None, _frame=None) -> None:
        nonlocal stopping
        stopping = True
        print(f"[종료] signal={sig}")
        for name, proc in list(procs.items()):
            if proc.poll() is None:
                print(f"[종료요청] {name} pid={proc.pid}")
                proc.terminate()
        t0 = time.time()
        while time.time() - t0 < 8.0:
            if all(proc.poll() is not None for proc in procs.values()):
                break
            time.sleep(0.2)
        for name, proc in list(procs.items()):
            if proc.poll() is None:
                print(f"[강제종료] {name} pid={proc.pid}")
                proc.kill()

    signal.signal(signal.SIGINT, _stop_all)
    signal.signal(signal.SIGTERM, _stop_all)

    cmd_map = {name: cmd for name, cmd in commands}
    for name, cmd in commands:
        _start_worker(name, cmd)

    try:
        while not stopping:
            time.sleep(1.0)
            for name, proc in list(procs.items()):
                code = proc.poll()
                if code is None:
                    continue
                if stopping:
                    break
                print(f"[재기동] {name} exited code={code}, restarting in {float(args.restart_delay_sec):.1f}s")
                time.sleep(float(args.restart_delay_sec))
                _start_worker(name, cmd_map[name])
    finally:
        _stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

