from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ai_invest.execution.upbit_private import UpbitPrivateApiError, UpbitPrivateClient
from ai_invest.storage.postgres import connect_postgres, get_postgres_connect_timeout_sec, to_psycopg_dsn


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


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, bool):
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        return float(text) if text else float(default)
    except Exception:
        return float(default)


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


def _extract_upbit_available_balance(accounts: Sequence[Mapping[str, Any]], *, currency: str) -> float:
    target = str(currency or "").strip().upper()
    for row in accounts:
        if str(row.get("currency") or "").strip().upper() != target:
            continue
        balance = _as_float(row.get("balance"), default=0.0)
        locked = _as_float(row.get("locked"), default=0.0)
        return max(0.0, float(balance) - float(locked))
    return 0.0


def _extract_upbit_min_total(chance: Mapping[str, Any], *, side: str) -> float | None:
    market = chance.get("market") if isinstance(chance.get("market"), Mapping) else {}
    side_map = market.get(str(side).strip().lower()) if isinstance(market, Mapping) else {}
    if not isinstance(side_map, Mapping):
        return None
    value = side_map.get("min_total")
    min_total = _as_float(value, default=-1.0)
    return None if min_total < 0.0 else float(min_total)


def _extract_upbit_side_types(chance: Mapping[str, Any], *, side: str) -> list[str]:
    market = chance.get("market") if isinstance(chance.get("market"), Mapping) else {}
    keys = [f"{str(side).strip().lower()}_types", "order_types"]
    for key in keys:
        raw = market.get(key) if isinstance(market, Mapping) else None
        values = _as_list(raw)
        if values:
            return [str(x).strip().lower() for x in values if str(x).strip()]
    return []


def _probe_upbit_broker(
    *,
    symbols: Sequence[str],
    dynamic_enabled: bool,
    min_order_krw: float,
) -> list[PreflightCheck]:
    checks: list[PreflightCheck] = []
    try:
        client = UpbitPrivateClient.from_env()
        accounts = client.get_accounts()
    except (UpbitPrivateApiError, RuntimeError) as exc:
        return [
            PreflightCheck(
                name="broker.connectivity",
                ok=False,
                detail=f"Upbit private probe failed: {str(exc)[:180]}",
            )
        ]

    checks.append(
        PreflightCheck(
            name="broker.connectivity",
            ok=True,
            detail=f"Upbit private auth ok; accounts={len(accounts)}",
        )
    )

    available_krw = _extract_upbit_available_balance(accounts, currency="KRW")
    checks.append(
        PreflightCheck(
            name="broker.krw_balance",
            ok=available_krw >= float(min_order_krw),
            detail=f"available_krw={available_krw:.0f}, min_order_krw={float(min_order_krw):.0f}",
            blocking=False,
        )
    )

    static_symbols = [str(sym).strip().upper() for sym in list(symbols or []) if str(sym).strip()]
    if not static_symbols:
        checks.append(
            PreflightCheck(
                name="broker.order_chance",
                ok=bool(dynamic_enabled),
                detail=(
                    "skipped symbol-level chance probe for dynamic universe"
                    if dynamic_enabled
                    else "no static symbols available for order chance probe"
                ),
                blocking=not bool(dynamic_enabled),
            )
        )
        return checks

    details: list[str] = []
    chance_ok = True
    for symbol in static_symbols[:3]:
        try:
            chance = client.get_order_chance(market=symbol)
        except (UpbitPrivateApiError, RuntimeError) as exc:
            chance_ok = False
            details.append(f"{symbol}: {str(exc)[:120]}")
            continue
        bid_types = _extract_upbit_side_types(chance, side="bid")
        min_total = _extract_upbit_min_total(chance, side="bid")
        if min_total is not None and float(min_order_krw) < float(min_total):
            chance_ok = False
            details.append(
                f"{symbol}: rule min_order_krw={float(min_order_krw):.0f} < exchange_min_total={float(min_total):.0f}"
            )
        else:
            min_total_txt = f"{float(min_total):.0f}" if min_total is not None else "n/a"
            order_types_txt = ",".join(bid_types[:4]) if bid_types else "unknown"
            details.append(f"{symbol}: exchange_min_total={min_total_txt}, bid_types={order_types_txt}")

    checks.append(
        PreflightCheck(
            name="broker.order_chance",
            ok=bool(chance_ok),
            detail="; ".join(details[:3]) if details else "no symbols probed",
        )
    )
    return checks


def build_startup_preflight(
    *,
    rules_raw: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
    require_trading: bool = True,
    probe_postgres: bool = True,
    probe_broker: bool = False,
) -> PreflightReport:
    env_map = dict(os.environ if env is None else env)
    universe = (rules_raw.get("universe") or {}) if isinstance(rules_raw, Mapping) else {}
    governance = (rules_raw.get("governance") or {}) if isinstance(rules_raw, Mapping) else {}
    activation_gate = (governance.get("activation_gate") or {}) if isinstance(governance, Mapping) else {}
    micro_mode = (governance.get("micro_mode") or {}) if isinstance(governance, Mapping) else {}
    live_data_collection = (
        (activation_gate.get("live_data_collection") or {}) if isinstance(activation_gate, Mapping) else {}
    )
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
    postgres_dsn = str(env_map.get("POSTGRES_DSN", "")).strip()
    postgres_present = bool(postgres_dsn)
    checks.append(
        PreflightCheck(
            name="storage.postgres",
            ok=postgres_present,
            detail="POSTGRES_DSN present" if postgres_present else "POSTGRES_DSN missing",
        )
    )
    if postgres_present and probe_postgres:
        timeout = get_postgres_connect_timeout_sec(env=env_map)
        try:
            with connect_postgres(to_psycopg_dsn(postgres_dsn), connect_timeout_sec=timeout) as conn:
                with conn.cursor() as cur:
                    cur.execute("select 1")
                    cur.fetchone()
            checks.append(
                PreflightCheck(
                    name="storage.postgres_connectivity",
                    ok=True,
                    detail=f"postgres connect ok (timeout={timeout}s)",
                )
            )
        except Exception as exc:
            checks.append(
                PreflightCheck(
                    name="storage.postgres_connectivity",
                    ok=False,
                    detail=f"postgres connect failed within {timeout}s: {str(exc)[:180]}",
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
        allow_live_exploration = _as_bool(micro_mode.get("allow_live_exploration"), default=False)
        profit_floor_bps = _as_float(micro_mode.get("live_profit_floor_bps"), default=0.0)
        profit_required_margin_bps = _as_float(micro_mode.get("live_profit_required_margin_bps"), default=0.0)
        live_learning_enabled = bool(
            allow_live_exploration
            and _as_bool(live_data_collection.get("enabled"), default=False)
            and _as_bool(live_data_collection.get("exploration_enabled"), default=False)
        )
        learning_target_pct = _as_float(live_data_collection.get("target_position_pct"), default=0.0)
        learning_min_after_cost_bps = _as_float(
            live_data_collection.get("min_predicted_after_cost_bps"),
            default=_as_float(micro_mode.get("live_min_predicted_after_cost_bps"), default=0.0),
        )
        profit_first_ok = bool(
            (not allow_live_exploration)
            and float(profit_floor_bps) > 0.0
            and float(profit_required_margin_bps) >= 0.0
        )
        live_learning_ok = bool(
            live_learning_enabled
            and float(learning_target_pct) > 0.0
            and float(learning_min_after_cost_bps) <= 0.0
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
        checks.append(
            PreflightCheck(
                name="live.runtime_policy",
                ok=bool(profit_first_ok or live_learning_ok),
                detail=(
                    f"profit-first={'yes' if profit_first_ok else 'no'}, "
                    f"live-learning={'yes' if live_learning_ok else 'no'}, "
                    f"profit_floor_bps={float(profit_floor_bps):.2f}, "
                    f"required_margin_bps={float(profit_required_margin_bps):.2f}, "
                    f"learning_target_pct={float(learning_target_pct):.2f}, "
                    f"learning_min_after_cost_bps={float(learning_min_after_cost_bps):.2f}"
                ),
            )
        )
        checks.append(
            PreflightCheck(
                name="live.universe_focus",
                ok=bool(dynamic_enabled) or len(symbols) >= 2,
                detail=(
                    f"single static symbol live universe: {symbols[:1]}"
                    if (not dynamic_enabled and len(symbols) == 1)
                    else (f"static symbols={symbols[:3]}" if symbols else "dynamic universe enabled")
                ),
                blocking=False,
            )
        )
        if probe_broker and has_broker_keys:
            checks.extend(
                _probe_upbit_broker(
                    symbols=symbols,
                    dynamic_enabled=dynamic_enabled,
                    min_order_krw=min_order_krw,
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
