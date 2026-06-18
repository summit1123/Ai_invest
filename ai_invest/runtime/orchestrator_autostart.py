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

from ai_invest.runtime.orchestrator_stop_trace import write_stop_request

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
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            process_query_limited_information = 0x1000
            still_active = 259
            handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return int(exit_code.value) == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
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


def _autostart_creationflags() -> int:
    if sys.platform != "win32":
        return 0
    create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    create_new_process_group = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    detached_process = int(getattr(subprocess, "DETACHED_PROCESS", 0))
    return int(create_no_window | create_new_process_group | detached_process)


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
        stdin=subprocess.DEVNULL,
        stdout=fp,
        stderr=fp,
        text=True,
        start_new_session=True,
        creationflags=_autostart_creationflags(),
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
                write_stop_request(
                    source="autostart",
                    reason="parent_shutdown",
                    target_pid=proc.pid,
                    extra={
                        "started_here": bool(state.started_here),
                        "enabled": bool(state.enabled),
                        "status_path": str(state.status_path),
                        "log_path": str(state.log_path),
                    },
                )
                if sys.platform == "win32":
                    try:
                        # The orchestrator polls for stop_request files, so give it time to exit cleanly.
                        proc.wait(timeout=8.0)
                    except subprocess.TimeoutExpired:
                        proc.terminate()
                        try:
                            proc.wait(timeout=8.0)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                else:
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
