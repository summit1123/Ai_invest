#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import math
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.dotenv import load_dotenv  # noqa: E402
from ai_invest.storage.postgres import DbEvent, DbRuleVersion, PostgresRepo  # noqa: E402

load_dotenv()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, bool):
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        return float(s) if s else float(default)
    except Exception:
        return float(default)


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        if isinstance(value, bool):
            return int(default)
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return int(value)
        s = str(value).strip()
        return int(float(s)) if s else int(default)
    except Exception:
        return int(default)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / float(len(values)))


def _path_get(payload: Mapping[str, Any], path: str) -> Any:
    node: Any = payload
    for key in str(path).split("."):
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]
    return node


def _path_set(payload: dict[str, Any], path: str, value: Any) -> None:
    keys = str(path).split(".")
    node: dict[str, Any] = payload
    for key in keys[:-1]:
        nxt = node.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            node[key] = nxt
        node = nxt
    node[keys[-1]] = value


def _load_rules_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return dict(raw) if isinstance(raw, Mapping) else {}


def _write_rules_yaml_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(dict(payload), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(path)


@dataclass(frozen=True)
class WindowMetrics:
    trades_count: int
    win_rate_pct: float
    expectancy_after_cost: float
    pnl_after_cost_total: float
    avg_hold_minutes: float
    drawdown_krw: float
    drawdown_ratio: float
    cost_under_ratio: float
    loss_streak_max: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "trades_count": int(self.trades_count),
            "win_rate_pct": float(round(self.win_rate_pct, 4)),
            "expectancy_after_cost": float(round(self.expectancy_after_cost, 4)),
            "pnl_after_cost_total": float(round(self.pnl_after_cost_total, 4)),
            "avg_hold_minutes": float(round(self.avg_hold_minutes, 4)),
            "drawdown_krw": float(round(self.drawdown_krw, 4)),
            "drawdown_ratio": float(round(self.drawdown_ratio, 6)),
            "cost_under_ratio": float(round(self.cost_under_ratio, 6)),
            "loss_streak_max": int(self.loss_streak_max),
        }


def _fetch_trades_between(
    repo: PostgresRepo,
    *,
    start_ts: datetime,
    end_ts: datetime,
) -> list[dict[str, Any]]:
    with repo.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select ts_open, ts_close, realized_pnl, fees_total
            from realized_trades
            where ts_close >= %s and ts_close < %s
            order by ts_close asc
            """,
            (start_ts, end_ts),
        )
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for ts_open, ts_close, realized_pnl, fees_total in rows:
        out.append(
            {
                "ts_open": ts_open,
                "ts_close": ts_close,
                "realized_pnl": _as_float(realized_pnl, default=0.0),
                "fees_total": _as_float(fees_total, default=0.0),
            }
        )
    return out


def _fetch_pnl_daily_between(
    repo: PostgresRepo,
    *,
    start_day: date,
    end_day: date,
) -> list[dict[str, Any]]:
    with repo.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select day, realized_pnl, fees_paid
            from pnl_daily
            where day >= %s and day <= %s
            order by day asc
            """,
            (start_day, end_day),
        )
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for day, realized_pnl, fees_paid in rows:
        out.append(
            {
                "day": str(day),
                "realized_pnl": _as_float(realized_pnl, default=0.0),
                "fees_paid": _as_float(fees_paid, default=0.0),
            }
        )
    return out


def _fetch_outcomes_between(
    repo: PostgresRepo,
    *,
    start_ts: datetime,
    end_ts: datetime,
) -> list[str]:
    with repo.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select error_type
            from decision_outcomes
            where reviewed_at >= %s and reviewed_at < %s
            order by reviewed_at asc
            """,
            (start_ts, end_ts),
        )
        rows = cur.fetchall()
    return [str(error_type or "").strip().upper() for (error_type,) in rows if str(error_type or "").strip()]


def _drawdown_from_pnl_daily(rows: Sequence[Mapping[str, Any]]) -> tuple[float, float]:
    if not rows:
        return 0.0, 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for row in rows:
        net = _as_float(row.get("realized_pnl"), default=0.0) - _as_float(row.get("fees_paid"), default=0.0)
        equity += float(net)
        peak = max(float(peak), float(equity))
        dd = float(peak - equity)
        max_dd = max(float(max_dd), float(dd))
    if peak <= 0.0:
        return float(max_dd), 0.0 if max_dd <= 0 else 1.0
    return float(max_dd), float(max_dd / peak)


def _max_loss_streak(net_pnls: Sequence[float]) -> int:
    streak = 0
    best = 0
    for v in net_pnls:
        if float(v) < 0.0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return int(best)


def _build_window_metrics(
    *,
    trades: Sequence[Mapping[str, Any]],
    pnl_daily_rows: Sequence[Mapping[str, Any]],
    outcomes: Sequence[str],
) -> WindowMetrics:
    net_pnls: list[float] = []
    hold_minutes: list[float] = []
    wins = 0
    for row in trades:
        realized = _as_float(row.get("realized_pnl"), default=0.0)
        fees = _as_float(row.get("fees_total"), default=0.0)
        net = float(realized - fees)
        net_pnls.append(float(net))
        if net > 0.0:
            wins += 1
        ts_open = row.get("ts_open")
        ts_close = row.get("ts_close")
        if isinstance(ts_open, datetime) and isinstance(ts_close, datetime):
            hold_min = max(0.0, (ts_close - ts_open).total_seconds() / 60.0)
            hold_minutes.append(float(hold_min))
    trades_count = len(net_pnls)
    win_rate = (float(wins) / float(trades_count) * 100.0) if trades_count > 0 else 0.0
    expectancy = _mean(net_pnls)
    pnl_total = float(sum(net_pnls)) if net_pnls else 0.0
    avg_hold = _mean(hold_minutes)
    dd_krw, dd_ratio = _drawdown_from_pnl_daily(pnl_daily_rows)
    outcome_total = len(outcomes)
    cost_under = sum(1 for code in outcomes if code == "OC_COST_UNDERESTIMATED")
    cost_under_ratio = (float(cost_under) / float(outcome_total)) if outcome_total > 0 else 0.0
    return WindowMetrics(
        trades_count=int(trades_count),
        win_rate_pct=float(win_rate),
        expectancy_after_cost=float(expectancy),
        pnl_after_cost_total=float(pnl_total),
        avg_hold_minutes=float(avg_hold),
        drawdown_krw=float(dd_krw),
        drawdown_ratio=float(dd_ratio),
        cost_under_ratio=float(cost_under_ratio),
        loss_streak_max=_max_loss_streak(net_pnls),
    )


def _window_score(m: WindowMetrics) -> float:
    ev_score = _clamp(float(m.expectancy_after_cost) / 2000.0, -1.0, 1.0)
    wr_score = _clamp((float(m.win_rate_pct) - 50.0) / 30.0, -1.0, 1.0)
    cost_penalty = _clamp(float(m.cost_under_ratio), 0.0, 1.0)
    dd_penalty = _clamp(float(m.drawdown_ratio) * 4.0, 0.0, 1.0)
    return float(0.55 * ev_score + 0.25 * wr_score - 0.10 * cost_penalty - 0.10 * dd_penalty)


def _determine_regime(short_metrics: WindowMetrics) -> str:
    if short_metrics.trades_count < 5:
        if short_metrics.drawdown_ratio >= 0.2:
            return "SHOCK"
        return "CHOP"
    if short_metrics.drawdown_ratio >= 0.15 or short_metrics.loss_streak_max >= 4:
        return "SHOCK"
    if short_metrics.win_rate_pct >= 55.0 and short_metrics.expectancy_after_cost > 0.0:
        return "TREND"
    return "CHOP"


def _resolve_regime_weights(cfg: Mapping[str, Any], regime: str) -> dict[str, float]:
    default = {
        "TREND": {"execution": 0.35, "short": 0.30, "medium": 0.20, "anchor": 0.15},
        "CHOP": {"execution": 0.25, "short": 0.35, "medium": 0.25, "anchor": 0.15},
        "SHOCK": {"execution": 0.20, "short": 0.25, "medium": 0.20, "anchor": 0.35},
    }
    raw = cfg.get("regime_weights") if isinstance(cfg, Mapping) else {}
    by_regime = raw.get(regime) if isinstance(raw, Mapping) and isinstance(raw.get(regime), Mapping) else default.get(regime, default["CHOP"])
    out = {
        "execution": _as_float(by_regime.get("execution"), default=default["CHOP"]["execution"]),
        "short": _as_float(by_regime.get("short"), default=default["CHOP"]["short"]),
        "medium": _as_float(by_regime.get("medium"), default=default["CHOP"]["medium"]),
        "anchor": _as_float(by_regime.get("anchor"), default=default["CHOP"]["anchor"]),
    }
    total = sum(max(0.0, float(v)) for v in out.values())
    if total <= 0.0:
        return dict(default["CHOP"])
    return {k: float(v) / float(total) for k, v in out.items()}


def _collect_multi_window_metrics(repo: PostgresRepo, *, now: datetime, cfg: Mapping[str, Any]) -> dict[str, WindowMetrics]:
    windows = cfg.get("windows") if isinstance(cfg.get("windows"), Mapping) else {}
    execution_hours = max(1, _as_int(windows.get("execution_hours"), default=6))
    short_days = max(7, _as_int(windows.get("short_days"), default=14))
    medium_days = max(short_days, _as_int(windows.get("medium_days"), default=90))
    anchor_days = max(medium_days, _as_int(windows.get("anchor_days"), default=270))

    def _metrics_for(delta: timedelta) -> WindowMetrics:
        start = now - delta
        trades = _fetch_trades_between(repo, start_ts=start, end_ts=now)
        pnl_rows = _fetch_pnl_daily_between(repo, start_day=start.date(), end_day=now.date())
        outcomes = _fetch_outcomes_between(repo, start_ts=start, end_ts=now)
        return _build_window_metrics(trades=trades, pnl_daily_rows=pnl_rows, outcomes=outcomes)

    return {
        "execution": _metrics_for(timedelta(hours=execution_hours)),
        "short": _metrics_for(timedelta(days=short_days)),
        "medium": _metrics_for(timedelta(days=medium_days)),
        "anchor": _metrics_for(timedelta(days=anchor_days)),
    }


def _weighted_composite_score(*, metrics: Mapping[str, WindowMetrics], weights: Mapping[str, float]) -> float:
    score = 0.0
    for key in ("execution", "short", "medium", "anchor"):
        weight = _as_float(weights.get(key), default=0.0)
        m = metrics.get(key)
        if not isinstance(m, WindowMetrics):
            continue
        score += float(weight) * _window_score(m)
    return float(_clamp(score, -1.0, 1.0))


def _limit_step(base: float, proposed: float, *, max_step_pct: float) -> float:
    step = abs(float(base)) * (float(max_step_pct) / 100.0)
    if step <= 0.0:
        step = max(0.0001, float(max_step_pct) / 100.0)
    lo = float(base) - float(step)
    hi = float(base) + float(step)
    return _clamp(float(proposed), lo, hi)


def _resolve_bounds(cfg: Mapping[str, Any], *, path: str, default_lo: float, default_hi: float) -> tuple[float, float]:
    bounds_map = cfg.get("bounds") if isinstance(cfg.get("bounds"), Mapping) else {}
    raw = bounds_map.get(path) if isinstance(bounds_map, Mapping) else None
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        lo = _as_float(raw[0], default=default_lo)
        hi = _as_float(raw[1], default=default_hi)
        if lo > hi:
            lo, hi = hi, lo
        return float(lo), float(hi)
    return float(default_lo), float(default_hi)


def _build_tuning_patch(
    *,
    rules_raw: Mapping[str, Any],
    tuning_cfg: Mapping[str, Any],
    metrics: Mapping[str, WindowMetrics],
    regime: str,
    composite_score: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    max_step_pct = _as_float(tuning_cfg.get("max_step_pct"), default=10.0)
    max_step_pct = _clamp(max_step_pct, 1.0, 25.0)
    target_trades_exec = max(1, _as_int(tuning_cfg.get("target_trades_in_execution_window"), default=3))
    execution_m = metrics.get("execution") or WindowMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)
    activity_gap = _clamp((float(target_trades_exec) - float(execution_m.trades_count)) / float(target_trades_exec), 0.0, 1.0)

    risk_pressure = _clamp(-float(composite_score), 0.0, 1.0)
    explore_pressure = _clamp(float(composite_score), 0.0, 1.0) * float(activity_gap)
    if regime == "SHOCK":
        risk_pressure = _clamp(risk_pressure + 0.20, 0.0, 1.0)
        explore_pressure = _clamp(explore_pressure * 0.50, 0.0, 1.0)
    elif regime == "TREND":
        explore_pressure = _clamp(explore_pressure + 0.15, 0.0, 1.0)
    else:
        risk_pressure = _clamp(risk_pressure + 0.05, 0.0, 1.0)

    whitelist = [str(x) for x in list(tuning_cfg.get("whitelist_paths") or []) if str(x).strip()]
    if not whitelist:
        whitelist = [
            "strategy.alpha_score.entry_alpha",
            "strategy.alpha_score.cooldown_minutes",
            "governance.micro_mode.min_alpha",
            "governance.micro_mode.max_spread_bps",
            "governance.plan_continuity.min_hold_minutes",
        ]

    current: dict[str, float] = {}
    for path in whitelist:
        current[path] = _as_float(_path_get(rules_raw, path), default=0.0)

    candidates: dict[str, float] = {
        "strategy.alpha_score.entry_alpha": current.get("strategy.alpha_score.entry_alpha", 0.0)
        * (1.0 + 0.08 * risk_pressure - 0.08 * explore_pressure),
        "strategy.alpha_score.cooldown_minutes": current.get("strategy.alpha_score.cooldown_minutes", 0.0)
        * (1.0 + 0.10 * risk_pressure - 0.08 * explore_pressure),
        "governance.micro_mode.min_alpha": current.get("governance.micro_mode.min_alpha", 0.0)
        * (1.0 + 0.08 * risk_pressure - 0.08 * explore_pressure),
        "governance.micro_mode.max_spread_bps": current.get("governance.micro_mode.max_spread_bps", 0.0)
        * (1.0 - 0.10 * risk_pressure + 0.10 * explore_pressure),
        "governance.plan_continuity.min_hold_minutes": current.get("governance.plan_continuity.min_hold_minutes", 0.0)
        * (1.0 + 0.10 * risk_pressure - 0.08 * explore_pressure),
    }

    patch: dict[str, Any] = {}
    for path in whitelist:
        base = _as_float(current.get(path), default=0.0)
        proposed = _as_float(candidates.get(path), default=base)
        if path.endswith("entry_alpha"):
            lo, hi = _resolve_bounds(tuning_cfg, path=path, default_lo=0.10, default_hi=0.95)
            bounded = _clamp(_limit_step(base, proposed, max_step_pct=max_step_pct), lo, hi)
            final = round(float(bounded), 4)
            if abs(final - base) >= 0.0001:
                patch[path] = float(final)
        elif path.endswith("min_alpha"):
            lo, hi = _resolve_bounds(tuning_cfg, path=path, default_lo=0.10, default_hi=0.95)
            bounded = _clamp(_limit_step(base, proposed, max_step_pct=max_step_pct), lo, hi)
            final = round(float(bounded), 4)
            if abs(final - base) >= 0.0001:
                patch[path] = float(final)
        elif path.endswith("max_spread_bps"):
            lo, hi = _resolve_bounds(tuning_cfg, path=path, default_lo=0.5, default_hi=20.0)
            bounded = _clamp(_limit_step(base, proposed, max_step_pct=max_step_pct), lo, hi)
            final = round(float(bounded), 3)
            if abs(final - base) >= 0.001:
                patch[path] = float(final)
        elif path.endswith("cooldown_minutes"):
            lo, hi = _resolve_bounds(tuning_cfg, path=path, default_lo=5.0, default_hi=720.0)
            bounded = _clamp(_limit_step(base, proposed, max_step_pct=max_step_pct), lo, hi)
            final = int(round(bounded))
            if int(round(base)) != int(final):
                patch[path] = int(final)
        elif path.endswith("min_hold_minutes"):
            lo, hi = _resolve_bounds(tuning_cfg, path=path, default_lo=15.0, default_hi=1440.0)
            bounded = _clamp(_limit_step(base, proposed, max_step_pct=max_step_pct), lo, hi)
            final = int(round(bounded))
            if int(round(base)) != int(final):
                patch[path] = int(final)

    diagnostics = {
        "max_step_pct": float(max_step_pct),
        "target_trades_in_execution_window": int(target_trades_exec),
        "execution_trades_count": int(execution_m.trades_count),
        "activity_gap": float(round(activity_gap, 6)),
        "risk_pressure": float(round(risk_pressure, 6)),
        "explore_pressure": float(round(explore_pressure, 6)),
        "regime": str(regime),
        "composite_score": float(round(composite_score, 6)),
    }
    return patch, diagnostics


def _apply_whitelisted_patch(
    *,
    rules_raw: Mapping[str, Any],
    patch: Mapping[str, Any],
    whitelist_paths: Sequence[str],
) -> dict[str, Any]:
    out = copy.deepcopy(dict(rules_raw))
    allowed = {str(x) for x in list(whitelist_paths or []) if str(x).strip()}
    for path, value in dict(patch or {}).items():
        if str(path) not in allowed:
            continue
        _path_set(out, str(path), value)
    return out


def _fetch_latest_rule_version_id(repo: PostgresRepo) -> uuid.UUID | None:
    with repo.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select rule_version_id
            from rule_versions
            order by created_at desc
            limit 1
            """
        )
        row = cur.fetchone()
    if not row:
        return None
    try:
        return row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0]))
    except Exception:
        return None


def _fetch_rule_version_rules(repo: PostgresRepo, *, rule_version_id: str) -> dict[str, Any] | None:
    try:
        rv_id = uuid.UUID(str(rule_version_id))
    except Exception:
        return None
    with repo.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select rules_dsl
            from rule_versions
            where rule_version_id=%s
            """,
            (rv_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    rules_dsl = row[0]
    return dict(rules_dsl) if isinstance(rules_dsl, Mapping) else None


def _ev_degraded(*, recent: float, baseline: float, ratio: float) -> bool:
    r = float(max(0.0, ratio))
    if baseline > 0:
        return recent <= baseline * (1.0 - r)
    if baseline < 0:
        return recent <= baseline * (1.0 + r)
    return recent < 0.0


def _dd_degraded(*, recent: float, baseline: float, ratio: float) -> bool:
    r = float(max(0.0, ratio))
    if baseline > 0:
        return recent >= baseline * (1.0 + r)
    return recent > 0.0


def _should_rollback(
    *,
    recent_ev: float,
    baseline_ev: float,
    recent_dd: float,
    baseline_dd: float,
    ev_ratio: float,
    dd_ratio: float,
) -> dict[str, Any]:
    ev_bad = _ev_degraded(recent=recent_ev, baseline=baseline_ev, ratio=ev_ratio)
    dd_bad = _dd_degraded(recent=recent_dd, baseline=baseline_dd, ratio=dd_ratio)
    return {
        "should_rollback": bool(ev_bad or dd_bad),
        "ev_degraded": bool(ev_bad),
        "drawdown_degraded": bool(dd_bad),
    }


def _emit_event(
    repo: PostgresRepo,
    *,
    event_type: str,
    payload: Mapping[str, Any],
    entity_type: str = "adaptive_tuning",
    entity_id: str = "rules.yaml",
    rule_version_id: uuid.UUID | None = None,
) -> None:
    repo.insert_event(
        DbEvent(
            event_id=uuid.uuid4(),
            ts=_utcnow(),
            event_type=str(event_type),
            entity_type=str(entity_type),
            entity_id=str(entity_id),
            run_id=None,
            rule_version_id=rule_version_id,
            payload=dict(payload),
        )
    )


def _insert_rule_version_snapshot(
    repo: PostgresRepo,
    *,
    created_by: str,
    parent_version: uuid.UUID | None,
    status: str,
    summary: str,
    rules_dsl: Mapping[str, Any],
    diff: Mapping[str, Any] | None,
    backtest_report: Mapping[str, Any] | None,
) -> uuid.UUID:
    rv_id = uuid.uuid4()
    repo.insert_rule_version(
        DbRuleVersion(
            rule_version_id=rv_id,
            created_by=str(created_by),
            parent_version=parent_version,
            status=str(status),
            summary=str(summary),
            rules_dsl=dict(rules_dsl),
            diff=dict(diff or {}),
            backtest_report=dict(backtest_report or {}),
        )
    )
    return rv_id


def _window_metrics_for_range(repo: PostgresRepo, *, start_ts: datetime, end_ts: datetime) -> WindowMetrics:
    trades = _fetch_trades_between(repo, start_ts=start_ts, end_ts=end_ts)
    pnl_rows = _fetch_pnl_daily_between(repo, start_day=start_ts.date(), end_day=end_ts.date())
    outcomes = _fetch_outcomes_between(repo, start_ts=start_ts, end_ts=end_ts)
    return _build_window_metrics(trades=trades, pnl_daily_rows=pnl_rows, outcomes=outcomes)


def _maybe_auto_rollback(
    *,
    repo: PostgresRepo,
    rules_path: Path,
    rules_raw: Mapping[str, Any],
    tuning_cfg: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    rollback_cfg = tuning_cfg.get("rollback_guard") if isinstance(tuning_cfg.get("rollback_guard"), Mapping) else {}
    if not bool(rollback_cfg.get("enabled", True)):
        return False, {"reason": "rollback_guard_disabled"}

    last_apply = repo.fetch_latest_event(event_type="DYNAMIC_RULE_PATCH_APPLIED")
    if not last_apply:
        return False, {"reason": "no_applied_patch"}

    last_revert = repo.fetch_latest_event(event_type="DYNAMIC_RULE_PATCH_REVERTED")
    if last_revert and isinstance(last_revert.get("ts"), datetime) and isinstance(last_apply.get("ts"), datetime):
        if last_revert["ts"] >= last_apply["ts"]:
            return False, {"reason": "already_reverted_after_latest_apply"}

    apply_ts = last_apply.get("ts")
    if not isinstance(apply_ts, datetime):
        return False, {"reason": "invalid_apply_ts"}
    check_after_hours = max(1, _as_int(rollback_cfg.get("check_after_hours"), default=24))
    age_hours = (_utcnow() - apply_ts.astimezone(timezone.utc)).total_seconds() / 3600.0
    if age_hours < float(check_after_hours):
        return False, {"reason": "not_due_yet", "age_hours": float(round(age_hours, 4))}

    baseline_hours = max(24, _as_int(rollback_cfg.get("baseline_hours"), default=72))
    now = _utcnow()
    recent_start = now - timedelta(hours=24)
    baseline_start = now - timedelta(hours=24 + baseline_hours)
    baseline_end = recent_start
    recent_m = _window_metrics_for_range(repo, start_ts=recent_start, end_ts=now)
    baseline_m = _window_metrics_for_range(repo, start_ts=baseline_start, end_ts=baseline_end)

    ev_ratio = _as_float(rollback_cfg.get("ev_degrade_ratio"), default=0.20)
    dd_ratio = _as_float(rollback_cfg.get("drawdown_degrade_ratio"), default=0.30)
    verdict = _should_rollback(
        recent_ev=float(recent_m.expectancy_after_cost),
        baseline_ev=float(baseline_m.expectancy_after_cost),
        recent_dd=float(recent_m.drawdown_krw),
        baseline_dd=float(baseline_m.drawdown_krw),
        ev_ratio=float(ev_ratio),
        dd_ratio=float(dd_ratio),
    )
    if not bool(verdict.get("should_rollback")):
        return False, {
            "reason": "guard_passed",
            "recent": recent_m.to_dict(),
            "baseline": baseline_m.to_dict(),
            **dict(verdict),
        }

    payload = last_apply.get("payload") if isinstance(last_apply.get("payload"), Mapping) else {}
    prev_rule_version_id = str((payload or {}).get("previous_rule_version_id") or "").strip()
    prev_rules = _fetch_rule_version_rules(repo, rule_version_id=prev_rule_version_id) if prev_rule_version_id else None
    if not isinstance(prev_rules, Mapping):
        return False, {"reason": "previous_rule_version_missing", "previous_rule_version_id": prev_rule_version_id}

    _write_rules_yaml_atomic(rules_path, prev_rules)
    parent = _fetch_latest_rule_version_id(repo)
    rollback_rule_version_id = _insert_rule_version_snapshot(
        repo,
        created_by="adaptive_tuning_loop",
        parent_version=parent,
        status="ACTIVE",
        summary=f"adaptive rollback -> {prev_rule_version_id}",
        rules_dsl=prev_rules,
        diff={"rollback_to_rule_version_id": prev_rule_version_id},
        backtest_report={"rollback_guard": {"recent": recent_m.to_dict(), "baseline": baseline_m.to_dict(), **dict(verdict)}},
    )
    _emit_event(
        repo,
        event_type="DYNAMIC_RULE_PATCH_REVERTED",
        rule_version_id=rollback_rule_version_id,
        payload={
            "applied_event_id": str(last_apply.get("event_id") or ""),
            "applied_rule_version_id": str((payload or {}).get("applied_rule_version_id") or ""),
            "previous_rule_version_id": str(prev_rule_version_id),
            "rollback_rule_version_id": str(rollback_rule_version_id),
            "recent_24h": recent_m.to_dict(),
            "baseline_prev_72h": baseline_m.to_dict(),
            "rollback_verdict": dict(verdict),
        },
    )
    return True, {
        "reason": "rollback_applied",
        "rollback_rule_version_id": str(rollback_rule_version_id),
        "recent": recent_m.to_dict(),
        "baseline": baseline_m.to_dict(),
        **dict(verdict),
    }


def run_adaptive_tuning_cycle(*, repo: PostgresRepo, rules_path: Path) -> dict[str, Any]:
    now = _utcnow()
    rules_raw = _load_rules_yaml(rules_path)
    tuning_cfg = rules_raw.get("adaptive_tuning") if isinstance(rules_raw.get("adaptive_tuning"), Mapping) else {}

    if not bool(tuning_cfg.get("enabled", False)):
        payload = {"status": "skipped", "reason": "adaptive_tuning_disabled"}
        _emit_event(repo, event_type="DYNAMIC_RULE_PATCH_SKIPPED", payload=payload)
        return payload

    rolled_back, rollback_info = _maybe_auto_rollback(
        repo=repo,
        rules_path=rules_path,
        rules_raw=rules_raw,
        tuning_cfg=tuning_cfg,
    )
    if rolled_back:
        return {"status": "reverted", **dict(rollback_info)}

    metrics = _collect_multi_window_metrics(repo, now=now, cfg=tuning_cfg)
    regime = _determine_regime(metrics["short"])
    weights = _resolve_regime_weights(tuning_cfg, regime)
    composite = _weighted_composite_score(metrics=metrics, weights=weights)
    patch, diagnostics = _build_tuning_patch(
        rules_raw=rules_raw,
        tuning_cfg=tuning_cfg,
        metrics=metrics,
        regime=regime,
        composite_score=composite,
    )
    whitelist = [str(x) for x in list(tuning_cfg.get("whitelist_paths") or []) if str(x).strip()]
    if not whitelist:
        whitelist = list(patch.keys())
    if not patch:
        payload = {
            "status": "skipped",
            "reason": "no_effective_patch",
            "regime": str(regime),
            "composite_score": float(round(composite, 6)),
            "diagnostics": diagnostics,
            "metrics": {k: v.to_dict() for k, v in metrics.items()},
        }
        _emit_event(repo, event_type="DYNAMIC_RULE_PATCH_SKIPPED", payload=payload)
        return payload

    patched_rules = _apply_whitelisted_patch(rules_raw=rules_raw, patch=patch, whitelist_paths=whitelist)
    if dict(patched_rules) == dict(rules_raw):
        payload = {
            "status": "skipped",
            "reason": "patch_not_changed",
            "patch": dict(patch),
            "regime": str(regime),
            "diagnostics": diagnostics,
        }
        _emit_event(repo, event_type="DYNAMIC_RULE_PATCH_SKIPPED", payload=payload)
        return payload

    _write_rules_yaml_atomic(rules_path, patched_rules)

    latest_parent = _fetch_latest_rule_version_id(repo)
    previous_rule_version_id = _insert_rule_version_snapshot(
        repo,
        created_by="adaptive_tuning_loop",
        parent_version=latest_parent,
        status="SNAPSHOT",
        summary="adaptive tuning pre-patch snapshot",
        rules_dsl=rules_raw,
        diff={},
        backtest_report={
            "regime": str(regime),
            "composite_score": float(round(composite, 6)),
            "metrics": {k: v.to_dict() for k, v in metrics.items()},
        },
    )
    applied_rule_version_id = _insert_rule_version_snapshot(
        repo,
        created_by="adaptive_tuning_loop",
        parent_version=previous_rule_version_id,
        status="ACTIVE",
        summary=f"adaptive tuning patch ({str(regime)})",
        rules_dsl=patched_rules,
        diff={"paths": dict(patch)},
        backtest_report={
            "regime": str(regime),
            "composite_score": float(round(composite, 6)),
            "weights": dict(weights),
            "diagnostics": dict(diagnostics),
            "metrics": {k: v.to_dict() for k, v in metrics.items()},
        },
    )

    _emit_event(
        repo,
        event_type="DYNAMIC_RULE_PATCH_APPLIED",
        rule_version_id=applied_rule_version_id,
        payload={
            "ts_utc": now.isoformat(),
            "regime": str(regime),
            "weights": dict(weights),
            "composite_score": float(round(composite, 6)),
            "diagnostics": dict(diagnostics),
            "patch": dict(patch),
            "whitelist_paths": list(whitelist),
            "previous_rule_version_id": str(previous_rule_version_id),
            "applied_rule_version_id": str(applied_rule_version_id),
            "metrics": {k: v.to_dict() for k, v in metrics.items()},
        },
    )
    return {
        "status": "applied",
        "regime": str(regime),
        "composite_score": float(round(composite, 6)),
        "patch": dict(patch),
        "previous_rule_version_id": str(previous_rule_version_id),
        "applied_rule_version_id": str(applied_rule_version_id),
    }


def _next_aligned(ts: float, interval_sec: float) -> float:
    return (math.floor(ts / interval_sec) + 1.0) * interval_sec


def main() -> int:
    p = argparse.ArgumentParser(description="Run dynamic adaptive tuning loop.")
    p.add_argument("--rules-path", type=str, default=str(ROOT / "rules.yaml"))
    p.add_argument("--sleep-sec", type=float, default=-1.0, help="loop interval (default: adaptive_tuning.interval_sec)")
    p.add_argument("--once", action="store_true", help="run once and exit")
    p.add_argument(
        "--align-to-clock",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="align each run to interval boundary",
    )
    p.add_argument(
        "--run-immediate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="run once immediately when loop starts",
    )
    args = p.parse_args()

    rules_path = Path(str(args.rules_path))
    rules_raw = _load_rules_yaml(rules_path)
    tuning_cfg = rules_raw.get("adaptive_tuning") if isinstance(rules_raw.get("adaptive_tuning"), Mapping) else {}
    interval_default = max(300.0, float(_as_float(tuning_cfg.get("interval_sec"), default=3600.0)))
    interval_sec = float(args.sleep_sec if float(args.sleep_sec) > 0 else interval_default)

    repo = PostgresRepo()

    if args.once:
        out = run_adaptive_tuning_cycle(repo=repo, rules_path=rules_path)
        print(f"[완료] adaptive_tuning once: {out}")
        return 0

    print(
        f"[시작] adaptive tuning loop: interval={interval_sec:.1f}s align={bool(args.align_to_clock)} path={rules_path}",
        flush=True,
    )
    now_ts = time.time()
    if bool(args.run_immediate):
        next_run_ts = now_ts
    else:
        next_run_ts = _next_aligned(now_ts, interval_sec) if bool(args.align_to_clock) else (now_ts + interval_sec)

    while True:
        wait_sec = float(next_run_ts - time.time())
        if wait_sec > 0:
            time.sleep(wait_sec)
        try:
            out = run_adaptive_tuning_cycle(repo=repo, rules_path=rules_path)
            print(f"[adaptive_tuning] {out}", flush=True)
        except Exception as exc:
            _emit_event(
                repo,
                event_type="DYNAMIC_RULE_PATCH_SKIPPED",
                payload={"status": "error", "reason": "exception", "error": str(exc)[:400]},
            )
            print(f"[경고] adaptive tuning error: {exc}", flush=True)
        now_ts = time.time()
        next_run_ts = _next_aligned(now_ts, interval_sec) if bool(args.align_to_clock) else (now_ts + interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
