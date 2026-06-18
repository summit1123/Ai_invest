from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ai_invest.storage.postgres import DbEvent, PostgresRepo


ORCHESTRATOR_EVENT_TYPE = "ORCHESTRATOR_STATUS"
ORCHESTRATOR_ENTITY_TYPE = "orchestrator"
ORCHESTRATOR_ENTITY_ID = "multi_orchestrator"


def _parse_ts(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_orchestrator_status(status: Mapping[str, Any] | None) -> dict[str, Any]:
    src = status if isinstance(status, Mapping) else {}
    workers_raw = src.get("workers") if isinstance(src.get("workers"), Mapping) else {}
    stop_request_raw = src.get("last_stop_request") if isinstance(src.get("last_stop_request"), Mapping) else None
    workers: dict[str, dict[str, Any]] = {}
    for name, raw in sorted(workers_raw.items(), key=lambda item: str(item[0])):
        state = raw if isinstance(raw, Mapping) else {}
        workers[str(name)] = {
            "pid": int(state["pid"]) if state.get("pid") is not None else None,
            "alive": bool(state.get("alive")),
            "last_started_at": state.get("last_started_at"),
            "last_exit_code": int(state["last_exit_code"]) if state.get("last_exit_code") is not None else None,
            "last_exited_at": state.get("last_exited_at"),
            "restarts": int(state.get("restarts") or 0),
        }
    return {
        "ts_utc": str(src.get("ts_utc") or ""),
        "stopping": bool(src.get("stopping")),
        "last_stop_request": dict(stop_request_raw) if isinstance(stop_request_raw, Mapping) else None,
        "workers": workers,
    }


def orchestrator_status_signature(status: Mapping[str, Any] | None) -> str:
    normalized = normalize_orchestrator_status(status)
    payload = {
        "stopping": normalized.get("stopping"),
        "last_stop_request": normalized.get("last_stop_request"),
        "workers": normalized.get("workers"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def build_orchestrator_summary(
    status: Mapping[str, Any] | None,
    *,
    source: str | None = None,
    exists: bool = True,
) -> dict[str, Any]:
    normalized = normalize_orchestrator_status(status)
    workers = normalized.get("workers") if isinstance(normalized.get("workers"), Mapping) else {}
    alive_workers: list[str] = []
    dead_workers: list[str] = []
    restart_counts: dict[str, int] = {}
    for name, raw in workers.items():
        state = raw if isinstance(raw, Mapping) else {}
        if bool(state.get("alive")):
            alive_workers.append(str(name))
        else:
            dead_workers.append(str(name))
        restart_counts[str(name)] = int(state.get("restarts") or 0)
    return {
        "source": source,
        "exists": bool(exists),
        "running": bool(alive_workers),
        "ts_utc": normalized.get("ts_utc"),
        "stopping": bool(normalized.get("stopping")),
        "last_stop_request": (
            dict(normalized.get("last_stop_request"))
            if isinstance(normalized.get("last_stop_request"), Mapping)
            else None
        ),
        "alive_workers": alive_workers,
        "dead_workers": dead_workers,
        "worker_count": len(workers),
        "restart_counts": restart_counts,
        "workers": dict(workers),
    }


def persist_orchestrator_status_event(
    *,
    repo: PostgresRepo,
    status: Mapping[str, Any],
    source_status_file: Path | None = None,
) -> None:
    normalized = normalize_orchestrator_status(status)
    payload = dict(normalized)
    payload["status_signature"] = orchestrator_status_signature(normalized)
    if source_status_file is not None:
        payload["source_status_file"] = str(source_status_file)
    repo.insert_event(
        DbEvent(
            event_id=uuid.uuid4(),
            ts=_parse_ts(normalized.get("ts_utc")),
            event_type=ORCHESTRATOR_EVENT_TYPE,
            entity_type=ORCHESTRATOR_ENTITY_TYPE,
            entity_id=ORCHESTRATOR_ENTITY_ID,
            run_id=None,
            rule_version_id=None,
            payload=payload,
        )
    )
