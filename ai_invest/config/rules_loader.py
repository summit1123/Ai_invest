from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


class RulesValidationError(ValueError):
    """Raised when rules.yaml violates the contract."""


def _dot_get(payload: Mapping[str, Any], path: str) -> Any:
    node: Any = payload
    for key in path.split("."):
        if not isinstance(node, Mapping) or key not in node:
            raise RulesValidationError(f"Missing required key: {path}")
        node = node[key]
    return node


def _as_float(payload: Mapping[str, Any], path: str) -> float:
    value = _dot_get(payload, path)
    if not isinstance(value, (int, float)):
        raise RulesValidationError(f"Expected number at {path}, got {type(value).__name__}")
    return float(value)


def _as_int(payload: Mapping[str, Any], path: str) -> int:
    value = _dot_get(payload, path)
    if not isinstance(value, int):
        raise RulesValidationError(f"Expected int at {path}, got {type(value).__name__}")
    return value


def _as_str(payload: Mapping[str, Any], path: str) -> str:
    value = _dot_get(payload, path)
    if not isinstance(value, str) or not value:
        raise RulesValidationError(f"Expected non-empty string at {path}")
    return value


def _as_bool(payload: Mapping[str, Any], path: str) -> bool:
    value = _dot_get(payload, path)
    if not isinstance(value, bool):
        raise RulesValidationError(f"Expected bool at {path}, got {type(value).__name__}")
    return value


@dataclass(frozen=True)
class UniverseConfig:
    market: str
    symbols: tuple[str, ...]
    trade_side: str
    mode: str
    max_open_positions: int


@dataclass(frozen=True)
class RiskConfig:
    max_risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_weekly_loss_pct: float
    max_position_pct_per_symbol: float
    max_total_exposure_pct: float
    consecutive_loss_cooldown_count: int
    cooldown_minutes_after_trigger: int
    min_hold_seconds: int


@dataclass(frozen=True)
class CostGuardConfig:
    max_spread_bps_entry: float
    max_predicted_slippage_bps: float
    max_total_cost_bps: float
    min_expected_edge_bps: float
    entry_cost_buffer_bps: float


@dataclass(frozen=True)
class StopPolicyConfig:
    mode: str
    atr_window: int
    atr_stop_mult: float
    atr_trail_mult: float
    hard_stop_pct: float
    take_profit_partial_pct: float
    time_stop_minutes: int
    break_even_after_rr: float
    include_tax_in_realtime_stop: bool
    include_fee_in_realtime_stop: bool


@dataclass(frozen=True)
class ExecutionConfig:
    order_style: str
    default_ord_type: str
    default_time_in_force: str
    post_only_timeout_sec: int
    reprice_interval_sec: int
    max_reprice_count: int
    fallback_to_market: bool
    min_order_krw: int
    max_submit_retries: int
    cancel_on_timeout_sec: int


@dataclass(frozen=True)
class RulesConfig:
    version: str
    as_of_date: str
    universe: UniverseConfig
    execution: ExecutionConfig
    risk: RiskConfig
    cost_guard: CostGuardConfig
    stop_policy: StopPolicyConfig
    raw: Mapping[str, Any]


def validate_rules(payload: Mapping[str, Any]) -> None:
    required_keys = (
        "version",
        "as_of_date",
        "universe.market",
        "universe.symbols",
        "universe.trade_side",
        "universe.mode",
        "execution.order_style",
        "execution.default_ord_type",
        "execution.default_time_in_force",
        "risk.max_risk_per_trade_pct",
        "risk.max_daily_loss_pct",
        "risk.max_weekly_loss_pct",
        "risk.min_hold_seconds",
        "cost_guard.max_spread_bps_entry",
        "cost_guard.max_predicted_slippage_bps",
        "cost_guard.max_total_cost_bps",
        "cost_guard.min_expected_edge_bps",
        "stop_policy.hard_stop_pct",
        "stop_policy.include_tax_in_realtime_stop",
        "stop_policy.include_fee_in_realtime_stop",
    )
    for key in required_keys:
        _dot_get(payload, key)

    trade_side = _as_str(payload, "universe.trade_side")
    if trade_side not in {"long_only"}:
        raise RulesValidationError(f"Unsupported universe.trade_side: {trade_side}")

    mode = _as_str(payload, "universe.mode")
    if mode not in {"paper", "live"}:
        raise RulesValidationError(f"Unsupported universe.mode: {mode}")

    order_style = _as_str(payload, "execution.order_style")
    if order_style not in {"limit_post_only_first"}:
        raise RulesValidationError(f"Unsupported execution.order_style: {order_style}")

    ord_type = _as_str(payload, "execution.default_ord_type")
    if ord_type not in {"limit", "market"}:
        raise RulesValidationError(f"Unsupported execution.default_ord_type: {ord_type}")

    tif = _as_str(payload, "execution.default_time_in_force")
    if tif not in {"post_only", "ioc", "fok"}:
        raise RulesValidationError(f"Unsupported execution.default_time_in_force: {tif}")

    symbols = _dot_get(payload, "universe.symbols")
    if not isinstance(symbols, list) or not symbols or not all(isinstance(s, str) for s in symbols):
        raise RulesValidationError("universe.symbols must be a non-empty string list")

    max_risk = _as_float(payload, "risk.max_risk_per_trade_pct")
    max_daily = _as_float(payload, "risk.max_daily_loss_pct")
    max_weekly = _as_float(payload, "risk.max_weekly_loss_pct")
    max_symbol_exposure = _as_float(payload, "risk.max_position_pct_per_symbol")
    max_total_exposure = _as_float(payload, "risk.max_total_exposure_pct")

    for name, value in (
        ("risk.max_risk_per_trade_pct", max_risk),
        ("risk.max_daily_loss_pct", max_daily),
        ("risk.max_weekly_loss_pct", max_weekly),
        ("risk.max_position_pct_per_symbol", max_symbol_exposure),
        ("risk.max_total_exposure_pct", max_total_exposure),
    ):
        if value <= 0:
            raise RulesValidationError(f"{name} must be > 0")

    if max_risk > max_daily:
        raise RulesValidationError("risk.max_risk_per_trade_pct must be <= risk.max_daily_loss_pct")
    if max_daily > max_weekly:
        raise RulesValidationError("risk.max_daily_loss_pct must be <= risk.max_weekly_loss_pct")
    if max_symbol_exposure > max_total_exposure:
        raise RulesValidationError(
            "risk.max_position_pct_per_symbol must be <= risk.max_total_exposure_pct"
        )

    min_hold_seconds = _as_int(payload, "risk.min_hold_seconds")
    if min_hold_seconds < 0:
        raise RulesValidationError("risk.min_hold_seconds must be >= 0")

    max_spread = _as_float(payload, "cost_guard.max_spread_bps_entry")
    max_slippage = _as_float(payload, "cost_guard.max_predicted_slippage_bps")
    max_total_cost = _as_float(payload, "cost_guard.max_total_cost_bps")
    min_edge = _as_float(payload, "cost_guard.min_expected_edge_bps")

    for name, value in (
        ("cost_guard.max_spread_bps_entry", max_spread),
        ("cost_guard.max_predicted_slippage_bps", max_slippage),
        ("cost_guard.max_total_cost_bps", max_total_cost),
        ("cost_guard.min_expected_edge_bps", min_edge),
    ):
        if value <= 0:
            raise RulesValidationError(f"{name} must be > 0")

    if max_spread > max_total_cost:
        raise RulesValidationError("cost_guard.max_spread_bps_entry must be <= cost_guard.max_total_cost_bps")
    if max_slippage > max_total_cost:
        raise RulesValidationError(
            "cost_guard.max_predicted_slippage_bps must be <= cost_guard.max_total_cost_bps"
        )
    if min_edge <= max_total_cost:
        raise RulesValidationError(
            "cost_guard.min_expected_edge_bps must be > cost_guard.max_total_cost_bps"
        )

    hard_stop = _as_float(payload, "stop_policy.hard_stop_pct")
    if hard_stop <= 0:
        raise RulesValidationError("stop_policy.hard_stop_pct must be > 0")
    _as_bool(payload, "stop_policy.include_tax_in_realtime_stop")
    _as_bool(payload, "stop_policy.include_fee_in_realtime_stop")

    decision_interval = _as_int(payload, "scheduling.decision_interval_sec")
    if decision_interval <= 0:
        raise RulesValidationError("scheduling.decision_interval_sec must be > 0")

    _as_str(payload, "settlement.timezone")


def load_rules(path: str | Path) -> RulesConfig:
    rules_path = Path(path)
    if not rules_path.exists():
        raise FileNotFoundError(f"rules file does not exist: {rules_path}")

    with rules_path.open("r", encoding="utf-8") as fp:
        payload = yaml.safe_load(fp)

    if not isinstance(payload, Mapping):
        raise RulesValidationError("rules.yaml must be a mapping object")

    validate_rules(payload)

    universe = UniverseConfig(
        market=_as_str(payload, "universe.market"),
        symbols=tuple(_dot_get(payload, "universe.symbols")),
        trade_side=_as_str(payload, "universe.trade_side"),
        mode=_as_str(payload, "universe.mode"),
        max_open_positions=_as_int(payload, "universe.max_open_positions"),
    )
    execution = ExecutionConfig(
        order_style=_as_str(payload, "execution.order_style"),
        default_ord_type=_as_str(payload, "execution.default_ord_type"),
        default_time_in_force=_as_str(payload, "execution.default_time_in_force"),
        post_only_timeout_sec=_as_int(payload, "execution.post_only_timeout_sec"),
        reprice_interval_sec=_as_int(payload, "execution.reprice_interval_sec"),
        max_reprice_count=_as_int(payload, "execution.max_reprice_count"),
        fallback_to_market=_as_bool(payload, "execution.fallback_to_market"),
        min_order_krw=_as_int(payload, "execution.min_order_krw"),
        max_submit_retries=_as_int(payload, "execution.max_submit_retries"),
        cancel_on_timeout_sec=_as_int(payload, "execution.cancel_on_timeout_sec"),
    )
    risk = RiskConfig(
        max_risk_per_trade_pct=_as_float(payload, "risk.max_risk_per_trade_pct"),
        max_daily_loss_pct=_as_float(payload, "risk.max_daily_loss_pct"),
        max_weekly_loss_pct=_as_float(payload, "risk.max_weekly_loss_pct"),
        max_position_pct_per_symbol=_as_float(payload, "risk.max_position_pct_per_symbol"),
        max_total_exposure_pct=_as_float(payload, "risk.max_total_exposure_pct"),
        consecutive_loss_cooldown_count=_as_int(payload, "risk.consecutive_loss_cooldown_count"),
        cooldown_minutes_after_trigger=_as_int(payload, "risk.cooldown_minutes_after_trigger"),
        min_hold_seconds=_as_int(payload, "risk.min_hold_seconds"),
    )
    cost_guard = CostGuardConfig(
        max_spread_bps_entry=_as_float(payload, "cost_guard.max_spread_bps_entry"),
        max_predicted_slippage_bps=_as_float(payload, "cost_guard.max_predicted_slippage_bps"),
        max_total_cost_bps=_as_float(payload, "cost_guard.max_total_cost_bps"),
        min_expected_edge_bps=_as_float(payload, "cost_guard.min_expected_edge_bps"),
        entry_cost_buffer_bps=_as_float(payload, "cost_guard.entry_cost_buffer_bps"),
    )
    stop_policy = StopPolicyConfig(
        mode=_as_str(payload, "stop_policy.mode"),
        atr_window=_as_int(payload, "stop_policy.atr_window"),
        atr_stop_mult=_as_float(payload, "stop_policy.atr_stop_mult"),
        atr_trail_mult=_as_float(payload, "stop_policy.atr_trail_mult"),
        hard_stop_pct=_as_float(payload, "stop_policy.hard_stop_pct"),
        take_profit_partial_pct=_as_float(payload, "stop_policy.take_profit_partial_pct"),
        time_stop_minutes=_as_int(payload, "stop_policy.time_stop_minutes"),
        break_even_after_rr=_as_float(payload, "stop_policy.break_even_after_rr"),
        include_tax_in_realtime_stop=_as_bool(payload, "stop_policy.include_tax_in_realtime_stop"),
        include_fee_in_realtime_stop=_as_bool(payload, "stop_policy.include_fee_in_realtime_stop"),
    )

    return RulesConfig(
        version=_as_str(payload, "version"),
        as_of_date=_as_str(payload, "as_of_date"),
        universe=universe,
        execution=execution,
        risk=risk,
        cost_guard=cost_guard,
        stop_policy=stop_policy,
        raw=payload,
    )

