from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _as_float(value: Any, *, default: float) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s else float(default)
    except Exception:
        return float(default)


def _as_int(value: Any, *, default: int) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, bool):
            return int(default)
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return int(round(value))
        s = str(value).strip()
        return int(float(s)) if s else int(default)
    except Exception:
        return int(default)


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


@dataclass(frozen=True)
class CapitalPolicyProfile:
    enabled: bool
    tier_name: str
    equity_krw: float
    max_target_position_pct: float
    max_position_pct_per_symbol: float
    cooldown_minutes_after_trigger: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "tier_name": str(self.tier_name),
            "equity_krw": float(self.equity_krw),
            "max_target_position_pct": float(self.max_target_position_pct),
            "max_position_pct_per_symbol": float(self.max_position_pct_per_symbol),
            "cooldown_minutes_after_trigger": int(self.cooldown_minutes_after_trigger),
        }


def resolve_capital_policy(
    *,
    rules_raw: Mapping[str, Any],
    equity_krw: float,
    default_target_position_pct: float,
    max_position_pct_per_symbol: float,
    cooldown_minutes_after_trigger: int,
) -> CapitalPolicyProfile:
    equity = max(0.0, float(equity_krw))
    base_max_pos = max(0.0, float(max_position_pct_per_symbol))
    base_target = max(0.0, min(float(default_target_position_pct), base_max_pos))
    base_cooldown = max(0, int(cooldown_minutes_after_trigger))

    gov = rules_raw.get("governance") if isinstance(rules_raw, Mapping) else None
    cp = (gov or {}).get("capital_policy") if isinstance(gov, Mapping) else None
    enabled = _as_bool((cp or {}).get("enabled") if isinstance(cp, Mapping) else None, default=False)

    if not isinstance(cp, Mapping) or not enabled:
        return CapitalPolicyProfile(
            enabled=False,
            tier_name="default",
            equity_krw=equity,
            max_target_position_pct=base_target,
            max_position_pct_per_symbol=base_max_pos,
            cooldown_minutes_after_trigger=base_cooldown,
        )

    tiers_raw = cp.get("tiers")
    if not isinstance(tiers_raw, list) or not tiers_raw:
        return CapitalPolicyProfile(
            enabled=False,
            tier_name="default",
            equity_krw=equity,
            max_target_position_pct=base_target,
            max_position_pct_per_symbol=base_max_pos,
            cooldown_minutes_after_trigger=base_cooldown,
        )

    parsed: list[dict[str, Any]] = []
    for i, row in enumerate(tiers_raw):
        if not isinstance(row, Mapping):
            continue
        min_eq = max(0.0, _as_float(row.get("min_equity_krw"), default=0.0))
        parsed.append(
            {
                "name": str(row.get("name") or f"tier_{i + 1}"),
                "min_equity_krw": min_eq,
                "max_target_position_pct": _as_float(row.get("max_target_position_pct"), default=base_target),
                "max_position_pct_per_symbol": _as_float(row.get("max_position_pct_per_symbol"), default=base_max_pos),
                "cooldown_minutes_after_trigger": _as_int(
                    row.get("cooldown_minutes_after_trigger"), default=base_cooldown
                ),
            }
        )

    if not parsed:
        return CapitalPolicyProfile(
            enabled=False,
            tier_name="default",
            equity_krw=equity,
            max_target_position_pct=base_target,
            max_position_pct_per_symbol=base_max_pos,
            cooldown_minutes_after_trigger=base_cooldown,
        )

    parsed.sort(key=lambda x: float(x.get("min_equity_krw") or 0.0))
    chosen = parsed[0]
    for tier in parsed:
        if equity >= float(tier.get("min_equity_krw") or 0.0):
            chosen = tier
        else:
            break

    tier_max_pos = max(0.0, float(chosen.get("max_position_pct_per_symbol") or base_max_pos))
    eff_max_pos = min(base_max_pos, tier_max_pos)

    tier_max_target = max(0.0, float(chosen.get("max_target_position_pct") or base_target))
    eff_target = min(base_target, tier_max_target, eff_max_pos)

    tier_cd = max(0, int(chosen.get("cooldown_minutes_after_trigger") or base_cooldown))
    eff_cd = max(base_cooldown, tier_cd)

    return CapitalPolicyProfile(
        enabled=True,
        tier_name=str(chosen.get("name") or "tier"),
        equity_krw=equity,
        max_target_position_pct=eff_target,
        max_position_pct_per_symbol=eff_max_pos,
        cooldown_minutes_after_trigger=eff_cd,
    )
