#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_STATUS_FILE = ROOT / "runtime" / "orchestrator_status.json"

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.runtime.orchestrator_stop_trace import consume_stop_request  # noqa: E402
from ai_invest.runtime.orchestrator_state import (  # noqa: E402
    orchestrator_status_signature,
    persist_orchestrator_status_event,
)
from ai_invest.runtime.preflight import build_startup_preflight, format_preflight_report  # noqa: E402
from ai_invest.storage.postgres import PostgresConfigError, PostgresRepo  # noqa: E402

load_dotenv()


def _load_rules() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8")) or {}


def _build_commands(
    *,
    python_bin: str,
    decision_interval_sec: float,
    research_interval_sec: float,
    quant_interval_sec: float,
    risk_interval_sec: float,
    ops_interval_sec: float,
    governance_sleep_sec: float,
    review_sleep_sec: float,
    adaptive_sleep_sec: float,
    enable_paper: bool,
    enable_work: bool,
    enable_governance: bool,
    enable_review: bool,
    enable_adaptive: bool,
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
                "research_work_loop",
                [
                    python_bin,
                    str(ROOT / "scripts" / "run_agent_work_loop.py"),
                    "--agent",
                    "research",
                    "--align-to-clock",
                    "--sleep-sec",
                    str(float(research_interval_sec)),
                ],
            )
        )
        cmds.append(
            (
                "quant_work_loop",
                [
                    python_bin,
                    str(ROOT / "scripts" / "run_agent_work_loop.py"),
                    "--agent",
                    "quant",
                    "--align-to-clock",
                    "--sleep-sec",
                    str(float(quant_interval_sec)),
                ],
            )
        )
        cmds.append(
            (
                "risk_work_loop",
                [
                    python_bin,
                    str(ROOT / "scripts" / "run_agent_work_loop.py"),
                    "--agent",
                    "risk",
                    "--align-to-clock",
                    "--sleep-sec",
                    str(float(risk_interval_sec)),
                ],
            )
        )
        cmds.append(
            (
                "ops_work_loop",
                [
                    python_bin,
                    str(ROOT / "scripts" / "run_agent_work_loop.py"),
                    "--agent",
                    "ops",
                    "--align-to-clock",
                    "--sleep-sec",
                    str(float(ops_interval_sec)),
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
    if enable_adaptive:
        cmds.append(
            (
                "adaptive_tuning_loop",
                [
                    python_bin,
                    str(ROOT / "scripts" / "run_adaptive_tuning_loop.py"),
                    "--align-to-clock",
                    "--sleep-sec",
                    str(float(adaptive_sleep_sec)),
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


def _worker_creationflags() -> int:
    if sys.platform != "win32":
        return 0
    create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    create_new_process_group = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return int(create_no_window | create_new_process_group)


def _stop_request_targets_current_process(
    stop_request: Mapping[str, Any] | None,
    *,
    pid: int | None = None,
) -> bool:
    if not isinstance(stop_request, Mapping):
        return False
    target_pid = stop_request.get("target_pid")
    if target_pid is None:
        return True
    try:
        return int(target_pid) == int(os.getpid() if pid is None else pid)
    except Exception:
        return False


def main() -> int:
    p = argparse.ArgumentParser(description="Run unified multi-agent orchestrator (paper/work/governance loops).")
    p.add_argument("--decision-interval-sec", type=float, default=-1.0, help="paper loop decision interval")
    p.add_argument("--work-interval-sec", type=float, default=-1.0, help="legacy: apply same interval to all work loops")
    p.add_argument("--research-interval-sec", type=float, default=-1.0, help="research worker interval")
    p.add_argument("--quant-interval-sec", type=float, default=-1.0, help="quant worker interval")
    p.add_argument("--risk-interval-sec", type=float, default=-1.0, help="risk worker interval")
    p.add_argument("--ops-interval-sec", type=float, default=-1.0, help="ops worker interval")
    p.add_argument("--governance-sleep-sec", type=float, default=30.0, help="governance scheduler check interval")
    p.add_argument("--review-sleep-sec", type=float, default=60.0, help="daily/weekly review loop interval")
    p.add_argument("--adaptive-sleep-sec", type=float, default=-1.0, help="adaptive tuning worker interval")
    p.add_argument("--restart-delay-sec", type=float, default=5.0, help="delay before restarting a crashed worker")
    p.add_argument("--status-file", type=str, default=str(DEFAULT_STATUS_FILE), help="orchestrator status json path")
    p.add_argument("--no-paper", action="store_true", help="disable paper loop worker")
    p.add_argument("--no-work", action="store_true", help="disable prework loop worker")
    p.add_argument("--no-governance", action="store_true", help="disable governance loop worker")
    p.add_argument("--no-review", action="store_true", help="disable review loop worker")
    p.add_argument("--no-adaptive", action="store_true", help="disable adaptive tuning worker")
    p.add_argument("--skip-preflight", action="store_true", help="skip startup preflight checks")
    p.add_argument("--dry-run", action="store_true", help="print commands and exit")
    args = p.parse_args()

    rules = _load_rules()
    default_decision_interval = float(((rules.get("scheduling") or {}).get("decision_interval_sec") or 15))
    decision_interval = float(args.decision_interval_sec if args.decision_interval_sec > 0 else default_decision_interval)
    workers_cfg = rules.get("workers") or {}
    legacy_work_interval = float(args.work_interval_sec)
    default_research = float((workers_cfg.get("research_interval_sec") or (legacy_work_interval if legacy_work_interval > 0 else 3600)))
    default_quant = float((workers_cfg.get("quant_interval_sec") or (legacy_work_interval if legacy_work_interval > 0 else 3600)))
    default_risk = float((workers_cfg.get("risk_interval_sec") or (legacy_work_interval if legacy_work_interval > 0 else 3600)))
    default_ops = float((workers_cfg.get("ops_interval_sec") or (legacy_work_interval if legacy_work_interval > 0 else 3600)))
    tuning_cfg = rules.get("adaptive_tuning") or {}
    adaptive_enabled_default = bool(tuning_cfg.get("enabled", False))
    default_adaptive = float((tuning_cfg.get("interval_sec") or 3600))
    research_interval = float(args.research_interval_sec if args.research_interval_sec > 0 else default_research)
    quant_interval = float(args.quant_interval_sec if args.quant_interval_sec > 0 else default_quant)
    risk_interval = float(args.risk_interval_sec if args.risk_interval_sec > 0 else default_risk)
    ops_interval = float(args.ops_interval_sec if args.ops_interval_sec > 0 else default_ops)
    adaptive_interval = float(args.adaptive_sleep_sec if args.adaptive_sleep_sec > 0 else default_adaptive)

    commands = _build_commands(
        python_bin=sys.executable,
        decision_interval_sec=decision_interval,
        research_interval_sec=research_interval,
        quant_interval_sec=quant_interval,
        risk_interval_sec=risk_interval,
        ops_interval_sec=ops_interval,
        governance_sleep_sec=float(args.governance_sleep_sec),
        review_sleep_sec=float(args.review_sleep_sec),
        adaptive_sleep_sec=float(adaptive_interval),
        enable_paper=not bool(args.no_paper),
        enable_work=not bool(args.no_work),
        enable_governance=not bool(args.no_governance),
        enable_review=not bool(args.no_review),
        enable_adaptive=(not bool(args.no_adaptive)) and bool(adaptive_enabled_default),
    )

    if not commands:
        print("[stop] no workers selected; check --no-* flags", flush=True)
        return 2

    if not bool(args.dry_run) and not bool(args.skip_preflight):
        report = build_startup_preflight(
            rules_raw=rules,
            require_trading=not bool(args.no_paper),
            probe_broker=True,
        )
        for line in format_preflight_report(report):
            print(line, flush=True)
        if not report.ok:
            return 2

    print("[config] unified orchestrator workers", flush=True)
    for name, cmd in commands:
        print(f"- {name}: {' '.join(cmd)}", flush=True)

    if args.dry_run:
        return 0

    status_file = Path(str(args.status_file))
    procs: dict[str, subprocess.Popen[str]] = {}
    worker_state: dict[str, dict[str, Any]] = {}
    stopping = False
    last_status_signature: str | None = None
    last_stop_request: dict[str, Any] | None = None
    try:
        status_repo: PostgresRepo | None = PostgresRepo()
    except PostgresConfigError as exc:
        status_repo = None
        print(f"[warn] orchestrator status persistence disabled: {exc}", flush=True)

    def _start_worker(name: str, cmd: list[str]) -> None:
        print(f"[start] {name}", flush=True)
        procs[name] = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=_worker_creationflags(),
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
            "last_stop_request": dict(last_stop_request) if isinstance(last_stop_request, dict) else None,
        }

    def _persist_status_snapshot(snapshot: dict[str, Any]) -> None:
        nonlocal last_status_signature
        signature = orchestrator_status_signature(snapshot)
        if signature == last_status_signature:
            return
        last_status_signature = signature
        if status_repo is None:
            return
        try:
            persist_orchestrator_status_event(
                repo=status_repo,
                status=snapshot,
                source_status_file=status_file,
            )
        except Exception as exc:
            print(f"[warn] failed to persist orchestrator status: {exc}", flush=True)

    def _stop_all(
        sig: int | None = None,
        _frame=None,
        *,
        stop_request: Mapping[str, Any] | None = None,
    ) -> None:
        nonlocal stopping, last_stop_request
        stopping = True
        resolved_stop_request = stop_request
        if resolved_stop_request is None:
            pending_stop_request = consume_stop_request(max_age_seconds=60.0)
            if _stop_request_targets_current_process(pending_stop_request):
                resolved_stop_request = pending_stop_request
            elif pending_stop_request is not None:
                print(
                    f"[stop] ignoring stop_request target_pid={pending_stop_request.get('target_pid')}",
                    flush=True,
                )
        if resolved_stop_request is None and isinstance(last_stop_request, dict):
            resolved_stop_request = dict(last_stop_request)
        if resolved_stop_request is None:
            resolved_stop_request = {
                "ts_utc": _now_utc_iso(),
                "source": "signal",
                "reason": "external_signal",
                "signal": int(sig) if sig is not None else None,
            }
        else:
            resolved_stop_request = {
                **dict(resolved_stop_request),
                "signal": int(sig) if sig is not None else resolved_stop_request.get("signal"),
            }
        last_stop_request = dict(resolved_stop_request)
        print(f"[stop] signal={sig} stop_request={json.dumps(last_stop_request, ensure_ascii=True)}", flush=True)
        try:
            snapshot = _snapshot_status()
            _write_status_file(status_file, snapshot)
            _persist_status_snapshot(snapshot)
        except Exception:
            pass
        for name, proc in list(procs.items()):
            if proc.poll() is None:
                print(f"[terminate] {name} pid={proc.pid}", flush=True)
                proc.terminate()
        t0 = time.time()
        while time.time() - t0 < 8.0:
            if all(proc.poll() is not None for proc in procs.values()):
                break
            time.sleep(0.2)
        for name, proc in list(procs.items()):
            if proc.poll() is None:
                print(f"[kill] {name} pid={proc.pid}", flush=True)
                proc.kill()
        try:
            snapshot = _snapshot_status()
            _write_status_file(status_file, snapshot)
            _persist_status_snapshot(snapshot)
        except Exception:
            pass

    signal.signal(signal.SIGINT, _stop_all)
    signal.signal(signal.SIGTERM, _stop_all)

    cmd_map = {name: cmd for name, cmd in commands}
    for name, cmd in commands:
        _start_worker(name, cmd)
    snapshot = _snapshot_status()
    _write_status_file(status_file, snapshot)
    _persist_status_snapshot(snapshot)

    try:
        while not stopping:
            pending_stop_request = consume_stop_request(max_age_seconds=60.0)
            if pending_stop_request is not None:
                if _stop_request_targets_current_process(pending_stop_request):
                    _stop_all(stop_request=pending_stop_request)
                    continue
                print(
                    f"[warn] ignored stop_request for target_pid={pending_stop_request.get('target_pid')}",
                    flush=True,
                )
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
                    f"[restart] {name} exited code={code}, restarting in {float(args.restart_delay_sec):.1f}s",
                    flush=True,
                )
                time.sleep(float(args.restart_delay_sec))
                _start_worker(name, cmd_map[name])
            snapshot = _snapshot_status()
            _write_status_file(status_file, snapshot)
            _persist_status_snapshot(snapshot)
    finally:
        if any(proc.poll() is None for proc in procs.values()):
            _stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
