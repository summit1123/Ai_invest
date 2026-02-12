from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


def _parse_dt(value: Any) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s else float(default)
    except Exception:
        return float(default)


@dataclass(frozen=True)
class PositionState:
    entry_price: float | None
    entry_ts: datetime | None
    hwm_price: float | None
    strategy_tag: str | None
    cooldown_until: datetime | None
    last_exit_reason: str | None

    def as_meta_patch(self) -> dict[str, Any]:
        return {
            "entry_price": float(self.entry_price) if self.entry_price is not None else None,
            "entry_ts": _to_iso_utc(self.entry_ts),
            "hwm_price": float(self.hwm_price) if self.hwm_price is not None else None,
            "strategy_tag": self.strategy_tag,
            "cooldown_until": _to_iso_utc(self.cooldown_until),
            "last_exit_reason": self.last_exit_reason,
        }

    def to_context(self, *, now: datetime) -> dict[str, Any]:
        cooldown_active = False
        cooldown_sec = 0.0
        if self.cooldown_until is not None:
            cooldown_active = now < self.cooldown_until
            cooldown_sec = max(0.0, (self.cooldown_until - now).total_seconds())
        return {
            "entry_price": self.entry_price,
            "entry_ts": _to_iso_utc(self.entry_ts),
            "hwm_price": self.hwm_price,
            "strategy_tag": self.strategy_tag,
            "cooldown_until": _to_iso_utc(self.cooldown_until),
            "cooldown_active": bool(cooldown_active),
            "cooldown_remaining_sec": float(cooldown_sec),
            "last_exit_reason": self.last_exit_reason,
        }


def parse_position_state(meta: Mapping[str, Any] | None) -> PositionState:
    m = dict(meta or {})
    entry_price = _as_float(m.get("entry_price"), default=0.0)
    hwm_price = _as_float(m.get("hwm_price"), default=0.0)
    return PositionState(
        entry_price=(None if entry_price <= 0 else float(entry_price)),
        entry_ts=_parse_dt(m.get("entry_ts") or m.get("opened_at")),
        hwm_price=(None if hwm_price <= 0 else float(hwm_price)),
        strategy_tag=(str(m.get("strategy_tag") or "").strip().upper() or None),
        cooldown_until=_parse_dt(m.get("cooldown_until")),
        last_exit_reason=(str(m.get("last_exit_reason") or "").strip().upper() or None),
    )


def with_hwm_update(*, state: PositionState, last_price: float) -> PositionState:
    px = float(last_price)
    if px <= 0:
        return state
    if state.hwm_price is None:
        return PositionState(
            entry_price=state.entry_price,
            entry_ts=state.entry_ts,
            hwm_price=px,
            strategy_tag=state.strategy_tag,
            cooldown_until=state.cooldown_until,
            last_exit_reason=state.last_exit_reason,
        )
    if px <= float(state.hwm_price):
        return state
    return PositionState(
        entry_price=state.entry_price,
        entry_ts=state.entry_ts,
        hwm_price=px,
        strategy_tag=state.strategy_tag,
        cooldown_until=state.cooldown_until,
        last_exit_reason=state.last_exit_reason,
    )

