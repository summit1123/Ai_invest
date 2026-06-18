from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STOP_REQUEST_PATH = ROOT / "runtime" / "orchestrator_stop_request.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_utc_iso() -> str:
    return _utcnow().isoformat()


def _current_process_info() -> dict[str, Any]:
    return {
        "pid": int(os.getpid()),
        "ppid": int(os.getppid()),
        "argv": [str(x) for x in list(sys.argv or [])[:16]],
        "cwd": str(Path.cwd()),
    }


def _request_path(path: str | Path | None = None) -> Path:
    if path is None:
        return DEFAULT_STOP_REQUEST_PATH
    return Path(path)


def write_stop_request(
    *,
    source: str,
    reason: str,
    target_pid: int | None = None,
    extra: Mapping[str, Any] | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    request = {
        "ts_utc": _now_utc_iso(),
        "source": str(source or "unknown"),
        "reason": str(reason or "unspecified"),
        "target_pid": int(target_pid) if target_pid is not None else None,
        "requester": _current_process_info(),
        "extra": dict(extra or {}),
    }
    req_path = _request_path(path)
    req_path.parent.mkdir(parents=True, exist_ok=True)
    req_path.write_text(json.dumps(request, ensure_ascii=True, indent=2), encoding="utf-8")
    return request


def read_stop_request(*, path: str | Path | None = None) -> dict[str, Any] | None:
    req_path = _request_path(path)
    if not req_path.exists():
        return None
    try:
        raw = json.loads(req_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return dict(raw) if isinstance(raw, Mapping) else None


def consume_stop_request(
    *,
    path: str | Path | None = None,
    max_age_seconds: float = 30.0,
) -> dict[str, Any] | None:
    req_path = _request_path(path)
    request = read_stop_request(path=req_path)
    if request is None:
        return None
    try:
        ts = datetime.fromisoformat(str(request.get("ts_utc") or "").replace("Z", "+00:00"))
        age_seconds = max(0.0, (_utcnow() - ts.astimezone(timezone.utc)).total_seconds())
        if age_seconds > float(max_age_seconds):
            request = None
    except Exception:
        request = None
    try:
        req_path.unlink(missing_ok=True)
    except Exception:
        pass
    return request
