from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATUS_PATH = ROOT / "runtime" / "orchestrator_status.json"
DEFAULT_LOG_PATH = ROOT / "logs" / "orchestrator.autostart.log"


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _status_workers_alive(status_path: Path) -> bool:
    try:
        if not status_path.exists():
            return False
        raw = json.loads(status_path.read_text(encoding="utf-8"))
        workers = raw.get("workers") if isinstance(raw, Mapping) else None
        if not isinstance(workers, Mapping):
            return False
        for _, st in workers.items():
            if not isinstance(st, Mapping):
                continue
            pid = st.get("pid")
            alive_flag = bool(st.get("alive"))
            try:
                pid_i = int(pid)
            except Exception:
                continue
            if alive_flag and _pid_alive(pid_i):
                return True
        return False
    except Exception:
        return False


@dataclass
class OrchestratorAutostartState:
    enabled: bool
    started_here: bool
    reason: str
    status_path: Path
    log_path: Path
    proc: subprocess.Popen[str] | None = None
    log_fp: IO[str] | None = None


def _autostart_enabled() -> bool:
    # Default ON for runtime convenience, but OFF under pytest unless explicitly forced.
    in_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    default_enabled = not in_pytest
    return _parse_bool(os.environ.get("APP_AUTOSTART_ORCHESTRATOR"), default=default_enabled)


def maybe_start_orchestrator() -> OrchestratorAutostartState:
    status_path = Path(os.environ.get("ORCHESTRATOR_STATUS_PATH", str(DEFAULT_STATUS_PATH)))
    log_path = Path(os.environ.get("APP_AUTOSTART_LOG_PATH", str(DEFAULT_LOG_PATH)))

    if not _autostart_enabled():
        return OrchestratorAutostartState(
            enabled=False,
            started_here=False,
            reason="autostart disabled",
            status_path=status_path,
            log_path=log_path,
        )

    force_start = _parse_bool(os.environ.get("APP_AUTOSTART_FORCE"), default=False)
    if (not force_start) and _status_workers_alive(status_path):
        return OrchestratorAutostartState(
            enabled=True,
            started_here=False,
            reason="external orchestrator already running",
            status_path=status_path,
            log_path=log_path,
        )

    cmd = [sys.executable, str(ROOT / "scripts" / "run_multi_orchestrator.py")]

    log_path.parent.mkdir(parents=True, exist_ok=True)
    fp = log_path.open("a", encoding="utf-8")
    fp.write("\n=== autostart begin ===\n")
    fp.flush()

    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=fp,
        stderr=fp,
        text=True,
        start_new_session=True,
    )

    return OrchestratorAutostartState(
        enabled=True,
        started_here=True,
        reason="started",
        status_path=status_path,
        log_path=log_path,
        proc=proc,
        log_fp=fp,
    )


def stop_orchestrator(state: OrchestratorAutostartState | None) -> None:
    if state is None:
        return
    proc = state.proc
    if proc is not None and state.started_here:
        try:
            if proc.poll() is None:
                try:
                    # Because start_new_session=True, signal the whole process group.
                    os.killpg(proc.pid, signal.SIGTERM)
                except Exception:
                    proc.terminate()

                t0 = time.time()
                while time.time() - t0 < 8.0 and proc.poll() is None:
                    time.sleep(0.2)

                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        proc.kill()
        except Exception:
            pass

    if state.log_fp is not None:
        try:
            state.log_fp.write("=== autostart end ===\n")
            state.log_fp.flush()
            state.log_fp.close()
        except Exception:
            pass
