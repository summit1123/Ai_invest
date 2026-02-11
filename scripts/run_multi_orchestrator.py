#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS_FILE = ROOT / "runtime" / "orchestrator_status.json"


def _load_rules() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8")) or {}


def _build_commands(
    *,
    python_bin: str,
    decision_interval_sec: float,
    work_interval_sec: float,
    governance_sleep_sec: float,
    review_sleep_sec: float,
    enable_paper: bool,
    enable_work: bool,
    enable_governance: bool,
    enable_review: bool,
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
    if enable_review:
        cmds.append(
            (
                "review_loop",
                [
                    python_bin,
                    str(ROOT / "scripts" / "run_review_loop.py"),
                    "--sleep-sec",
                    str(float(review_sleep_sec)),
                ],
            )
        )
    return cmds


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_status_file(status_file: Path, data: dict[str, Any]) -> None:
    status_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = status_file.with_suffix(status_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(status_file)


def main() -> int:
    p = argparse.ArgumentParser(description="Run unified multi-agent orchestrator (paper/work/governance loops).")
    p.add_argument("--decision-interval-sec", type=float, default=-1.0, help="paper loop decision interval")
    p.add_argument("--work-interval-sec", type=float, default=1800.0, help="prework loop interval")
    p.add_argument("--governance-sleep-sec", type=float, default=30.0, help="governance scheduler check interval")
    p.add_argument("--review-sleep-sec", type=float, default=60.0, help="daily/weekly review loop interval")
    p.add_argument("--restart-delay-sec", type=float, default=5.0, help="delay before restarting a crashed worker")
    p.add_argument("--status-file", type=str, default=str(DEFAULT_STATUS_FILE), help="orchestrator status json path")
    p.add_argument("--no-paper", action="store_true", help="disable paper loop worker")
    p.add_argument("--no-work", action="store_true", help="disable prework loop worker")
    p.add_argument("--no-governance", action="store_true", help="disable governance loop worker")
    p.add_argument("--no-review", action="store_true", help="disable review loop worker")
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
        review_sleep_sec=float(args.review_sleep_sec),
        enable_paper=not bool(args.no_paper),
        enable_work=not bool(args.no_work),
        enable_governance=not bool(args.no_governance),
        enable_review=not bool(args.no_review),
    )

    if not commands:
        print("[중단] 실행할 워커가 없습니다. --no-* 옵션을 확인하세요.")
        return 2

    print("[설정] unified orchestrator workers")
    for name, cmd in commands:
        print(f"- {name}: {' '.join(cmd)}", flush=True)

    if args.dry_run:
        return 0

    status_file = Path(str(args.status_file))
    procs: dict[str, subprocess.Popen[str]] = {}
    worker_state: dict[str, dict[str, Any]] = {}
    stopping = False

    def _start_worker(name: str, cmd: list[str]) -> None:
        print(f"[시작] {name}", flush=True)
        procs[name] = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=None,
            stderr=None,
            text=True,
        )
        st = worker_state.get(name) or {"restarts": 0}
        st["pid"] = int(procs[name].pid)
        st["alive"] = True
        st["last_started_at"] = _now_utc_iso()
        st["last_exit_code"] = None
        worker_state[name] = st

    def _snapshot_status() -> dict[str, Any]:
        for name, proc in list(procs.items()):
            code = proc.poll()
            st = worker_state.get(name) or {}
            st["pid"] = int(proc.pid)
            st["alive"] = code is None
            if code is not None:
                st["last_exit_code"] = int(code)
                if not st.get("last_exited_at"):
                    st["last_exited_at"] = _now_utc_iso()
            worker_state[name] = st
        return {
            "ts_utc": _now_utc_iso(),
            "stopping": bool(stopping),
            "workers": worker_state,
        }

    def _stop_all(sig: int | None = None, _frame=None) -> None:
        nonlocal stopping
        stopping = True
        print(f"[종료] signal={sig}", flush=True)
        try:
            _write_status_file(status_file, _snapshot_status())
        except Exception:
            pass
        for name, proc in list(procs.items()):
            if proc.poll() is None:
                print(f"[종료요청] {name} pid={proc.pid}", flush=True)
                proc.terminate()
        t0 = time.time()
        while time.time() - t0 < 8.0:
            if all(proc.poll() is not None for proc in procs.values()):
                break
            time.sleep(0.2)
        for name, proc in list(procs.items()):
            if proc.poll() is None:
                print(f"[강제종료] {name} pid={proc.pid}", flush=True)
                proc.kill()
        try:
            _write_status_file(status_file, _snapshot_status())
        except Exception:
            pass

    signal.signal(signal.SIGINT, _stop_all)
    signal.signal(signal.SIGTERM, _stop_all)

    cmd_map = {name: cmd for name, cmd in commands}
    for name, cmd in commands:
        _start_worker(name, cmd)
    _write_status_file(status_file, _snapshot_status())

    try:
        while not stopping:
            time.sleep(1.0)
            for name, proc in list(procs.items()):
                code = proc.poll()
                if code is None:
                    continue
                if stopping:
                    break
                st = worker_state.get(name) or {}
                st["alive"] = False
                st["last_exit_code"] = int(code)
                st["last_exited_at"] = _now_utc_iso()
                st["restarts"] = int(st.get("restarts") or 0) + 1
                worker_state[name] = st
                print(
                    f"[재기동] {name} exited code={code}, restarting in {float(args.restart_delay_sec):.1f}s",
                    flush=True,
                )
                time.sleep(float(args.restart_delay_sec))
                _start_worker(name, cmd_map[name])
            _write_status_file(status_file, _snapshot_status())
    finally:
        _stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
