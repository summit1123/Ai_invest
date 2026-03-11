from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return bool(value)
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str
    blocking: bool = True


@dataclass(frozen=True)
class PreflightReport:
    mode: str
    require_trading: bool
    checks: list[PreflightCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok or not check.blocking for check in self.checks)


def build_startup_preflight(
    *,
    rules_raw: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
    require_trading: bool = True,
) -> PreflightReport:
    env_map = dict(os.environ if env is None else env)
    universe = (rules_raw.get("universe") or {}) if isinstance(rules_raw, Mapping) else {}
    governance = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    activation_gate = (governance.get("activation_gate") or {}) if isinstance(governance, Mapping) else {}
    execution = (rules_raw.get("execution") or {}) if isinstance(rules_raw, Mapping) else {}
    dynamic_cfg = (universe.get("dynamic") or {}) if isinstance(universe, Mapping) else {}

    mode = str(universe.get("mode") or "paper").strip().lower() or "paper"
    symbols = _as_list(universe.get("symbols"))
    dynamic_enabled = _as_bool(dynamic_cfg.get("enabled"), default=False)
    min_order_krw = float(execution.get("min_order_krw") or 0.0)

    checks: list[PreflightCheck] = []
    checks.append(
        PreflightCheck(
            name="runtime.mode",
            ok=mode in {"paper", "live"},
            detail=f"mode={mode}",
        )
    )
    checks.append(
        PreflightCheck(
            name="storage.postgres",
            ok=bool(str(env_map.get("POSTGRES_DSN", "")).strip()),
            detail="POSTGRES_DSN present" if str(env_map.get("POSTGRES_DSN", "")).strip() else "POSTGRES_DSN missing",
        )
    )
    checks.append(
        PreflightCheck(
            name="universe.selection",
            ok=bool(symbols) or bool(dynamic_enabled),
            detail=(
                f"symbols={symbols}"
                if symbols
                else ("dynamic universe enabled" if dynamic_enabled else "no static symbols and dynamic universe disabled")
            ),
        )
    )

    if require_trading:
        checks.append(
            PreflightCheck(
                name="execution.min_order",
                ok=min_order_krw > 0.0,
                detail=f"min_order_krw={min_order_krw:.0f}",
            )
        )

    if require_trading and mode == "live":
        live_execution_enabled = _as_bool(activation_gate.get("live_execution_enabled"), default=False)
        env_live_enabled = _as_bool(env_map.get("ENABLE_LIVE_TRADING"), default=False)
        has_broker_keys = bool(str(env_map.get("UPBIT_ACCESS_KEY", "")).strip()) and bool(
            str(env_map.get("UPBIT_SECRET_KEY", "")).strip()
        )
        checks.append(
            PreflightCheck(
                name="live.activation_gate",
                ok=live_execution_enabled,
                detail="live_execution_enabled=true" if live_execution_enabled else "live_execution_enabled=false",
            )
        )
        checks.append(
            PreflightCheck(
                name="live.env_flag",
                ok=env_live_enabled,
                detail="ENABLE_LIVE_TRADING=true" if env_live_enabled else "ENABLE_LIVE_TRADING missing or false",
            )
        )
        checks.append(
            PreflightCheck(
                name="broker.credentials",
                ok=has_broker_keys,
                detail="Upbit credentials present" if has_broker_keys else "Upbit credentials missing",
            )
        )

    telegram_ready = bool(str(env_map.get("TELEGRAM_BOT_TOKEN", "")).strip()) and bool(
        str(env_map.get("TELEGRAM_CHAT_ID_OPS", "")).strip()
    )
    checks.append(
        PreflightCheck(
            name="notifications.telegram",
            ok=telegram_ready,
            detail="Telegram ops channel configured" if telegram_ready else "Telegram ops channel not configured",
            blocking=False,
        )
    )

    return PreflightReport(
        mode=mode,
        require_trading=bool(require_trading),
        checks=checks,
    )


def format_preflight_report(report: PreflightReport) -> list[str]:
    lines = [
        f"[preflight] mode={report.mode} trading={'on' if report.require_trading else 'off'} "
        f"status={'OK' if report.ok else 'FAIL'}"
    ]
    for check in report.checks:
        if check.ok:
            prefix = "OK"
        elif check.blocking:
            prefix = "FAIL"
        else:
            prefix = "WARN"
        lines.append(f"[{prefix}] {check.name}: {check.detail}")
    return lines
