from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from zoneinfo import ZoneInfo

from ai_invest.agents.research_agent import research_agent_daily_brief
from ai_invest.config.capital_policy import resolve_capital_policy
from ai_invest.config.llm_router import llm_route_for_agent
from ai_invest.config.rules_loader import RulesConfig, load_rules
from ai_invest.market_data.features import build_alpha_features_from_1m_candles
from ai_invest.market_data.macro import fetch_macro_context
from ai_invest.market_data.universe_selector import resolve_dynamic_universe
from ai_invest.market_data.upbit_public import fetch_candles_minutes, fetch_market_snapshot
from ai_invest.research.news_signal import build_news_signal
from ai_invest.research.rss import fetch_crypto_headlines
from ai_invest.strategy.alpha_score import compute_alpha_score, load_alpha_score_config
from ai_invest.storage.postgres import DbAgentDailyReport, DbEvent, PostgresRepo

KST = ZoneInfo("Asia/Seoul")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_kst() -> datetime:
    return _utcnow().astimezone(KST)


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


def _timeframe_to_minutes(tf: str) -> int:
    tf = str(tf or "").strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    return 15


def _quote_currency(symbol: str) -> str:
    if "-" not in str(symbol):
        return "KRW"
    return str(symbol).split("-", 1)[0].strip().upper() or "KRW"


def _candidate_score(*, rsi: float, vol_z: float, spread_bps: float, rsi_min: float, vol_min: float, max_spread: float) -> float:
    score = 0.0
    score += (rsi - rsi_min) / 100.0
    score += (vol_z - vol_min) / 10.0
    if spread_bps > max_spread:
        score -= (spread_bps - max_spread) / 100.0
    return float(score)


def _report_age_minutes(*, now_utc: datetime, created_at: Any) -> float | None:
    if not isinstance(created_at, datetime):
        return None
    ts = created_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now_utc - ts.astimezone(timezone.utc)).total_seconds() / 60.0)


def collect_latest_work_reports(
    *,
    repo: PostgresRepo,
    agent_names: Sequence[str],
    max_age_minutes: int = 360,
    now_utc: datetime | None = None,
    include_details: bool = True,
) -> dict[str, Any]:
    now = now_utc or _utcnow()
    reports: dict[str, Any] = {}
    missing: list[str] = []
    stale: list[str] = []

    for agent_name in list(agent_names):
        name = str(agent_name).strip()
        if not name:
            continue
        row = repo.fetch_latest_agent_daily_report(agent_name=name)
        if not row:
            missing.append(name)
            continue

        age_min = _report_age_minutes(now_utc=now, created_at=row.get("created_at"))
        if age_min is None or age_min > float(max_age_minutes):
            stale.append(name)

        row_out = {
            "report_id": row.get("report_id"),
            "created_at": row.get("created_at").isoformat() if isinstance(row.get("created_at"), datetime) else row.get("created_at"),
            "title": row.get("title"),
            "summary": row.get("summary"),
            "age_minutes": age_min,
        }
        if include_details:
            row_out["team_scope"] = row.get("team_scope")
            row_out["findings"] = row.get("findings")
            row_out["risks"] = row.get("risks")
            row_out["action_items"] = row.get("action_items")
        reports[name] = row_out

    return {
        "reports": reports,
        "missing": sorted(set(missing)),
        "stale": sorted(set(stale)),
        "max_age_minutes": int(max_age_minutes),
        "checked_at_utc": now.isoformat(),
    }


def _store_report(
    *,
    repo: PostgresRepo,
    report_date_kst: datetime,
    cycle_key: str,
    agent_name: str,
    team_scope: str,
    title: str,
    summary: str,
    findings: Mapping[str, Any],
    risks: Mapping[str, Any],
    action_items: Mapping[str, Any],
    meeting_context: str | None = None,
) -> uuid.UUID:
    report_id = uuid.uuid4()
    repo.insert_agent_daily_report(
        DbAgentDailyReport(
            report_id=report_id,
            report_date=report_date_kst.date(),
            agent_name=str(agent_name),
            team_scope=str(team_scope),
            title=str(title),
            summary=str(summary),
            findings=dict(findings or {}),
            risks=dict(risks or {}),
            action_items=dict(action_items or {}),
            run_id=None,
            rule_version_id=None,
        )
    )

    repo.insert_event(
        DbEvent(
            event_id=uuid.uuid4(),
            ts=_utcnow(),
            event_type="AGENT_WORK_REPORT",
            entity_type="agent_daily_reports",
            entity_id=str(report_id),
            run_id=None,
            rule_version_id=None,
            payload={
                "cycle_key": cycle_key,
                "meeting_context": meeting_context,
                "report_id": str(report_id),
                "agent_name": str(agent_name),
                "team_scope": str(team_scope),
                "title": str(title),
            },
        )
    )
    return report_id


def _build_features(*, symbol: str, lookback_minutes: int, alpha_cfg) -> tuple[dict[str, float], dict[str, float]]:
    snap = fetch_market_snapshot(symbol)
    candles = fetch_candles_minutes(symbol, unit=1, count=max(120, int(lookback_minutes)))
    opens = [float(c.get("opening_price") or c["trade_price"]) for c in candles]
    highs = [float(c["high_price"]) for c in candles]
    lows = [float(c["low_price"]) for c in candles]
    closes = [float(c["trade_price"]) for c in candles]
    volumes = [float(c["candle_acc_trade_volume"]) for c in candles]
    turnovers = [float(c.get("candle_acc_trade_price") or 0.0) for c in candles]
    alpha_features = build_alpha_features_from_1m_candles(
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        turnover_values=turnovers,
        ema_fast=int(alpha_cfg.ema_fast),
        ema_slow=int(alpha_cfg.ema_slow),
        ret_short_bars=int(alpha_cfg.ret_short_mins),
        ret_long_bars=int(alpha_cfg.ret_long_mins),
    )
    alpha_res = compute_alpha_score(features=alpha_features, cfg=alpha_cfg)
    snapshot = {
        "last_price": float(snap.last_price),
        "best_bid": float(snap.best_bid),
        "best_ask": float(snap.best_ask),
        "mid_price": float(snap.mid_price),
        "spread_bps": float(snap.spread_bps),
    }
    features = dict(alpha_features)
    features.update(
        {
            "mom_s": float(alpha_res.mom_s),
            "rev_s": float(alpha_res.rev_s),
            "alpha_raw": float(alpha_res.alpha_raw),
            "alpha": float(alpha_res.alpha),
            "signal_target_pct": float(alpha_res.signal_target_pct),
            "strength": float(alpha_res.strength),
            "vol_scale": float(alpha_res.vol_scale),
            "strategy_tag_candidate": str(alpha_res.strategy_tag_candidate),
            "regime": str(alpha_res.regime),
            "trend_strength": float(alpha_res.trend_strength),
            "shock_strength": float(alpha_res.shock_strength),
        }
    )
    return snapshot, features


def _candidate_score_alpha(*, alpha: float, spread_bps: float, max_spread_bps: float) -> float:
    score = float(alpha)
    if spread_bps > max_spread_bps:
        score -= (spread_bps - max_spread_bps) / 100.0
    return float(score)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _mean(values: Sequence[float]) -> float:
    xs = [float(v) for v in list(values or [])]
    return (sum(xs) / float(len(xs))) if xs else 0.0


def _to_symbol(value: Any) -> str:
    s = str(value or "").strip().upper()
    return s


def _safe_repo_rows(*, repo: Any, method_name: str, limit: int) -> list[Mapping[str, Any]]:
    fn = getattr(repo, str(method_name), None)
    if not callable(fn):
        return []
    try:
        rows = fn(limit=int(limit))
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, Mapping)]
        return []
    except Exception:
        return []


def _build_quant_feedback_profiles(
    *,
    repo: PostgresRepo,
    rules_raw: Mapping[str, Any],
    symbols: Sequence[str],
) -> dict[str, Any]:
    cfg = (rules_raw.get("quant_feedback") or {}) if isinstance(rules_raw, Mapping) else {}
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        return {"enabled": False, "profiles": {}, "summary": {"reason": "disabled"}}

    trades_limit = max(100, int(_as_float(cfg.get("realized_trades_limit"), default=1200)))
    exec_limit = max(100, int(_as_float(cfg.get("execution_metrics_limit"), default=1200)))
    outcomes_limit = max(100, int(_as_float(cfg.get("decision_outcomes_limit"), default=1200)))
    min_samples = max(3, int(_as_float(cfg.get("min_samples_per_symbol"), default=8)))
    max_boost = max(0.0, _as_float(cfg.get("max_score_boost"), default=0.18))
    max_penalty = max(0.0, _as_float(cfg.get("max_score_penalty"), default=0.30))

    allowed = {_to_symbol(s) for s in list(symbols or []) if _to_symbol(s)}
    if not allowed:
        return {"enabled": True, "profiles": {}, "summary": {"reason": "no_symbols"}}

    trades = _safe_repo_rows(repo=repo, method_name="fetch_realized_trades", limit=trades_limit)
    execm = _safe_repo_rows(repo=repo, method_name="fetch_execution_metrics", limit=exec_limit)
    outcomes = _safe_repo_rows(repo=repo, method_name="fetch_decision_outcomes", limit=outcomes_limit)

    trade_map: dict[str, list[Mapping[str, Any]]] = {sym: [] for sym in allowed}
    for row in trades:
        sym = _to_symbol(row.get("symbol"))
        if sym in allowed:
            trade_map[sym].append(row)

    exec_map: dict[str, list[Mapping[str, Any]]] = {sym: [] for sym in allowed}
    for row in execm:
        sym = _to_symbol(row.get("symbol"))
        if sym in allowed:
            exec_map[sym].append(row)

    outcome_map: dict[str, list[Mapping[str, Any]]] = {sym: [] for sym in allowed}
    for row in outcomes:
        sym = _to_symbol(row.get("symbol"))
        if sym in allowed:
            outcome_map[sym].append(row)

    profiles: dict[str, dict[str, Any]] = {}
    adjustments: list[float] = []
    symbols_with_data = 0
    for sym in sorted(allowed):
        tr_rows = list(trade_map.get(sym) or [])
        ex_rows = list(exec_map.get(sym) or [])
        oc_rows = list(outcome_map.get(sym) or [])

        n_tr = len(tr_rows)
        n_ex = len(ex_rows)
        n_oc = len(oc_rows)
        sample_total = n_tr + n_ex + n_oc
        if sample_total > 0:
            symbols_with_data += 1

        avg_pnl_bps = _mean([_as_float(r.get("pnl_bps"), default=0.0) for r in tr_rows])
        win_rate_trades = (
            float(sum(1 for r in tr_rows if _as_float(r.get("realized_pnl"), default=0.0) > 0.0)) / float(n_tr)
            if n_tr > 0
            else 0.0
        )
        trade_sample = min(1.0, float(n_tr) / float(min_samples))
        trade_edge = _clamp(avg_pnl_bps / 35.0, -0.35, 0.35)
        trade_consistency = _clamp((win_rate_trades - 0.5) * 1.4, -0.30, 0.30)
        trade_component = trade_sample * (0.65 * trade_edge + 0.35 * trade_consistency)

        avg_slippage_bps = _mean([_as_float(r.get("slippage_bps_vs_submit"), default=0.0) for r in ex_rows])
        avg_spread_submit_bps = _mean([_as_float(r.get("spread_bps_at_submit"), default=0.0) for r in ex_rows])
        avg_fill_ratio = _mean([_as_float(r.get("filled_ratio"), default=1.0) for r in ex_rows]) if n_ex > 0 else 1.0
        exec_sample = min(1.0, float(n_ex) / float(min_samples))
        slip_pen = _clamp(max(0.0, avg_slippage_bps - 2.5) / 20.0, 0.0, 0.20)
        spread_pen = _clamp(max(0.0, avg_spread_submit_bps - 4.0) / 28.0, 0.0, 0.12)
        fill_pen = _clamp(max(0.0, 0.92 - avg_fill_ratio) * 0.8, 0.0, 0.10)
        slip_bonus = _clamp(max(0.0, -avg_slippage_bps) / 30.0, 0.0, 0.05)
        execution_component = exec_sample * (slip_bonus - (slip_pen + spread_pen + fill_pen))

        wins_outcome = sum(1 for r in oc_rows if str(r.get("outcome_label") or "").strip().upper() == "WIN")
        losses_outcome = sum(1 for r in oc_rows if str(r.get("outcome_label") or "").strip().upper() == "LOSS")
        err_counter: dict[str, int] = {}
        for r in oc_rows:
            code = str(r.get("error_type") or "").strip().upper()
            if not code:
                continue
            err_counter[code] = err_counter.get(code, 0) + 1
        oc_sample = min(1.0, float(n_oc) / float(min_samples))
        outcome_edge = (
            _clamp((float(wins_outcome - losses_outcome) / float(max(1, n_oc))) * 0.25, -0.15, 0.15) if n_oc > 0 else 0.0
        )
        cost_ratio = (float(err_counter.get("OC_COST_UNDERESTIMATED", 0)) / float(n_oc)) if n_oc > 0 else 0.0
        liq_ratio = (float(err_counter.get("OC_LIQUIDITY_DROPOUT", 0)) / float(n_oc)) if n_oc > 0 else 0.0
        latency_ratio = (float(err_counter.get("OC_EXECUTION_LATENCY", 0)) / float(n_oc)) if n_oc > 0 else 0.0
        false_break_ratio = (float(err_counter.get("OC_FALSE_BREAKOUT", 0)) / float(n_oc)) if n_oc > 0 else 0.0
        outcome_penalty = oc_sample * (
            0.22 * cost_ratio + 0.12 * latency_ratio + 0.10 * liq_ratio + 0.08 * false_break_ratio
        )
        outcome_component = oc_sample * outcome_edge - outcome_penalty

        raw_adjust = trade_component + execution_component + outcome_component
        score_adjustment = _clamp(raw_adjust, -float(max_penalty), float(max_boost))
        adjustments.append(float(score_adjustment))
        profiles[sym] = {
            "sample_total": int(sample_total),
            "samples": {"realized_trades": int(n_tr), "execution_metrics": int(n_ex), "decision_outcomes": int(n_oc)},
            "trade_stats": {"avg_pnl_bps": float(avg_pnl_bps), "win_rate_trades": float(win_rate_trades)},
            "execution_stats": {
                "avg_slippage_bps": float(avg_slippage_bps),
                "avg_spread_submit_bps": float(avg_spread_submit_bps),
                "avg_fill_ratio": float(avg_fill_ratio),
            },
            "outcome_stats": {
                "wins": int(wins_outcome),
                "losses": int(losses_outcome),
                "oc_cost_underestimated_ratio": float(cost_ratio),
                "oc_execution_latency_ratio": float(latency_ratio),
                "oc_liquidity_dropout_ratio": float(liq_ratio),
            },
            "components": {
                "trade_component": float(trade_component),
                "execution_component": float(execution_component),
                "outcome_component": float(outcome_component),
            },
            "score_adjustment": float(score_adjustment),
        }

    return {
        "enabled": True,
        "profiles": profiles,
        "summary": {
            "symbols_with_feedback": int(symbols_with_data),
            "avg_adjustment": float(_mean(adjustments)),
            "max_adjustment": float(max(adjustments)) if adjustments else 0.0,
            "min_adjustment": float(min(adjustments)) if adjustments else 0.0,
            "limits": {
                "realized_trades_limit": int(trades_limit),
                "execution_metrics_limit": int(exec_limit),
                "decision_outcomes_limit": int(outcomes_limit),
                "min_samples_per_symbol": int(min_samples),
                "max_score_boost": float(max_boost),
                "max_score_penalty": float(max_penalty),
            },
        },
    }


def _apply_feedback_to_candidates(
    *,
    candidates: Sequence[Mapping[str, Any]],
    feedback_profiles: Mapping[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in list(candidates or []):
        item = dict(row or {})
        sym = _to_symbol(item.get("symbol"))
        profile = dict(feedback_profiles.get(sym) or {}) if isinstance(feedback_profiles, Mapping) else {}
        base_score = _as_float(item.get("score"), default=0.0)
        score_adjustment = _as_float(profile.get("score_adjustment"), default=0.0) if profile else 0.0
        item["base_score"] = float(base_score)
        item["feedback_score_adjustment"] = float(score_adjustment)
        item["score"] = float(base_score + score_adjustment)
        if profile:
            item["feedback"] = {
                "sample_total": int(profile.get("sample_total") or 0),
                "samples": dict(profile.get("samples") or {}),
                "trade_stats": dict(profile.get("trade_stats") or {}),
                "execution_stats": dict(profile.get("execution_stats") or {}),
                "outcome_stats": dict(profile.get("outcome_stats") or {}),
            }
        out.append(item)
    out.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return out


def _quant_candidate_rows(
    *,
    rules_raw: Mapping[str, Any],
    rules: RulesConfig,
    symbols: Sequence[str],
    lookback_minutes: int,
    alpha_cfg,
) -> list[dict[str, Any]]:
    cost_cfg = (rules_raw.get("cost_guard") or {}) if isinstance(rules_raw, Mapping) else {}
    max_spread = _as_float(cost_cfg.get("max_spread_bps_entry"), default=float(rules.cost_guard.max_spread_bps_entry))

    out: list[dict[str, Any]] = []
    for sym in list(symbols):
        try:
            snapshot, features = _build_features(symbol=sym, lookback_minutes=int(lookback_minutes), alpha_cfg=alpha_cfg)
            alpha = _as_float(features.get("alpha"), default=0.0)
            score = _candidate_score_alpha(
                alpha=float(alpha),
                spread_bps=_as_float(snapshot.get("spread_bps"), default=0.0),
                max_spread_bps=max_spread,
            )
            out.append(
                {
                    "symbol": sym,
                    "score": score,
                    "snapshot": snapshot,
                    "features": features,
                }
            )
        except Exception as exc:
            out.append(
                {
                    "symbol": sym,
                    "score": -9.0,
                    "snapshot": {},
                    "features": {},
                    "error": str(exc)[:180],
                }
            )
    out.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return out


def _quick_backtest_candidate(
    *,
    symbol: str,
    alpha_cfg,
    fee_total_bps: float,
    base_slippage_bps: float = 1.0,
    spread_penalty_mult: float = 0.30,
    low_liquidity_penalty_bps: float = 1.2,
    fill_ratio: float = 1.0,
    lookback_bars: int = 500,
    hold_bars: int = 24,
) -> dict[str, Any]:
    """Lightweight replay aligned with AlphaScore entry/exit/cooldown."""

    try:
        candles = fetch_candles_minutes(symbol, unit=1, count=max(120, int(lookback_bars)))
    except Exception as exc:
        return {
            "symbol": symbol,
            "ok": False,
            "error": f"candles_fetch_failed:{str(exc)[:120]}",
            "trades": 0,
            "win_rate_pct": 0.0,
            "net_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_trade_return_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_after_cost_pct": 0.0,
            "backtest_score": -99.0,
        }

    opens = [float(c.get("opening_price") or c.get("trade_price") or 0.0) for c in candles]
    highs = [float(c.get("high_price") or 0.0) for c in candles]
    lows = [float(c.get("low_price") or 0.0) for c in candles]
    closes = [float(c.get("trade_price") or 0.0) for c in candles]
    volumes = [float(c.get("candle_acc_trade_volume") or 0.0) for c in candles]
    turnovers = [float(c.get("candle_acc_trade_price") or 0.0) for c in candles]
    n = min(len(highs), len(lows), len(closes), len(volumes))
    if n < 120:
        return {
            "symbol": symbol,
            "ok": False,
            "error": "not_enough_candles",
            "trades": 0,
            "win_rate_pct": 0.0,
            "net_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_trade_return_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_after_cost_pct": 0.0,
            "backtest_score": -99.0,
        }

    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    in_pos = False
    entry_px = 0.0
    entry_i = -1
    hwm = 0.0
    strategy_tag = "MOM"
    cooldown_until_i = -1
    trades = 0
    wins = 0
    sum_ret_pct = 0.0
    sum_win = 0.0
    sum_loss_abs = 0.0
    fee_rate = max(0.0, float(fee_total_bps)) / 10000.0
    fill_ratio_n = max(0.2, min(1.0, float(fill_ratio)))
    lookback = max(80, int(alpha_cfg.lookback_minutes))
    warmup = max(80, int(alpha_cfg.ema_slow) + 5, int(alpha_cfg.ret_long_mins) + 1)
    cooldown_bars = max(0, int(alpha_cfg.cooldown_minutes))
    min_hold_bars = max(0, int(hold_bars))

    for i in range(warmup, n):
        px = float(closes[i])
        if px <= 0:
            continue
        start = max(0, i - lookback + 1)
        f = build_alpha_features_from_1m_candles(
            opens=opens[start : i + 1],
            highs=highs[start : i + 1],
            lows=lows[start : i + 1],
            closes=closes[start : i + 1],
            volumes=volumes[start : i + 1],
            turnover_values=turnovers[start : i + 1],
            ema_fast=int(alpha_cfg.ema_fast),
            ema_slow=int(alpha_cfg.ema_slow),
            ret_short_bars=int(alpha_cfg.ret_short_mins),
            ret_long_bars=int(alpha_cfg.ret_long_mins),
        )
        a = compute_alpha_score(features=f, cfg=alpha_cfg)
        atr_pct = _as_float(f.get("atr_pct"), default=0.0)
        rsi = _as_float(f.get("rsi_14"), default=50.0)
        ema20 = _as_float(f.get("ema20"), default=0.0)
        ema60 = _as_float(f.get("ema60"), default=0.0)
        volz = _as_float(f.get("vol_zscore"), default=0.0)

        if not in_pos:
            if i < cooldown_until_i:
                continue
            if float(a.alpha) >= float(alpha_cfg.entry_alpha):
                in_pos = True
                entry_px = px
                entry_i = i
                hwm = px
                strategy_tag = str(a.strategy_tag_candidate)
            continue

        hwm = max(hwm, px)
        stop_pct = float(alpha_cfg.stop_atr_mult) * (atr_pct / 100.0)
        trail_pct = float(alpha_cfg.trail_atr_mult) * (atr_pct / 100.0)
        hold_minutes = max(0, i - entry_i)
        time_limit = int(alpha_cfg.time_stop_rev_minutes) if str(strategy_tag).upper() == "REV" else int(alpha_cfg.time_stop_mom_minutes)
        exit_cond = False
        if stop_pct > 0 and px <= entry_px * (1.0 - stop_pct):
            exit_cond = True
        elif hold_minutes >= min_hold_bars and trail_pct > 0 and px <= hwm * (1.0 - trail_pct):
            exit_cond = True
        elif hold_minutes >= min_hold_bars and (rsi <= float(alpha_cfg.exit_rsi) or (ema20 > 0 and ema60 > 0 and ema20 < ema60)):
            exit_cond = True
        elif hold_minutes >= time_limit:
            exit_cond = True
        if not exit_cond:
            continue

        gross = (px - entry_px) / entry_px if entry_px > 0 else 0.0
        range_pct = ((float(highs[i]) - float(lows[i])) / px * 100.0) if px > 0 else 0.0
        spread_like_bps = max(0.0, float(range_pct) * 100.0)
        liq_pen = max(0.0, -float(volz)) * float(low_liquidity_penalty_bps)
        slippage_total_bps = (float(base_slippage_bps) * 2.0) + (float(spread_penalty_mult) * spread_like_bps) + liq_pen
        total_cost_rate = fee_rate + (slippage_total_bps / 10000.0)
        net = (gross * fill_ratio_n) - total_cost_rate
        eq *= max(0.0, 1.0 + net)
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
        trades += 1
        ret_pct = net * 100.0
        sum_ret_pct += ret_pct
        if net > 0:
            wins += 1
            sum_win += ret_pct
        else:
            sum_loss_abs += abs(ret_pct)
        in_pos = False
        entry_px = 0.0
        entry_i = -1
        hwm = 0.0
        cooldown_until_i = i + cooldown_bars

    if in_pos and entry_px > 0:
        last_i = n - 1
        last_px = float(closes[last_i])
        gross = (last_px - entry_px) / entry_px if entry_px > 0 else 0.0
        range_pct = ((float(highs[last_i]) - float(lows[last_i])) / last_px * 100.0) if last_px > 0 else 0.0
        spread_like_bps = max(0.0, float(range_pct) * 100.0)
        net = (gross * fill_ratio_n) - (fee_rate + ((float(base_slippage_bps) * 2.0 + float(spread_penalty_mult) * spread_like_bps) / 10000.0))
        eq *= max(0.0, 1.0 + net)
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
        trades += 1
        ret_pct = net * 100.0
        sum_ret_pct += ret_pct
        if net > 0:
            wins += 1
            sum_win += ret_pct
        else:
            sum_loss_abs += abs(ret_pct)

    win_rate = (float(wins) / float(trades) * 100.0) if trades > 0 else 0.0
    net_return_pct = (eq - 1.0) * 100.0
    avg_trade = (sum_ret_pct / float(trades)) if trades > 0 else 0.0
    expectancy = float(avg_trade)
    profit_factor = (float(sum_win) / float(sum_loss_abs)) if sum_loss_abs > 0 else (999.0 if sum_win > 0 else 0.0)
    backtest_score = float(net_return_pct) + 0.10 * float(win_rate) - 0.70 * float(max_dd * 100.0)

    return {
        "symbol": symbol,
        "ok": True,
        "trades": int(trades),
        "win_rate_pct": float(win_rate),
        "net_return_pct": float(net_return_pct),
        "max_drawdown_pct": float(max_dd * 100.0),
        "avg_trade_return_pct": float(avg_trade),
        "profit_factor": float(profit_factor),
        "expectancy_after_cost_pct": float(expectancy),
        "backtest_score": float(backtest_score),
        "assumptions": {
            "fee_total_bps": float(fee_total_bps),
            "base_slippage_bps": float(base_slippage_bps),
            "spread_penalty_mult": float(spread_penalty_mult),
            "low_liquidity_penalty_bps": float(low_liquidity_penalty_bps),
            "fill_ratio": float(fill_ratio_n),
            "entry_alpha": float(alpha_cfg.entry_alpha),
            "hold_bars": int(min_hold_bars),
        },
    }


@dataclass(frozen=True)
class WorkCycleResult:
    cycle_key: str
    report_ids: dict[str, str]


WORK_AGENT_NAMES = ("research_agent", "quant_strategist", "risk_manager", "ops_manager")


def run_agent_work_cycle(
    *,
    repo: PostgresRepo,
    rules_raw: Mapping[str, Any],
    meeting_context: str | None = None,
    selected_agents: Sequence[str] | None = None,
) -> WorkCycleResult:
    """Run one pre-meeting agent work cycle and persist reports.

    This does not execute orders. It only creates report assets for meetings.
    """

    now_kst = _now_kst()
    cycle_key = now_kst.strftime("%Y-%m-%d %H:%M:%S")

    selected = {str(a).strip() for a in (selected_agents or WORK_AGENT_NAMES) if str(a).strip()}
    selected &= set(WORK_AGENT_NAMES)
    if not selected:
        raise ValueError("selected_agents is empty")

    rules = load_rules("rules.yaml")
    alpha_cfg = load_alpha_score_config(rules_raw=rules_raw)
    universe = resolve_dynamic_universe(rules_raw=rules_raw, fallback_symbols=list(rules.universe.symbols))
    static_symbols = [str(s).strip().upper() for s in list(rules.universe.symbols) if str(s).strip()]
    dynamic_symbols = [str(s).strip().upper() for s in list(universe.symbols or []) if str(s).strip()]
    dyn_cfg = (
        ((rules_raw.get("universe") or {}).get("dynamic") or {})
        if isinstance(rules_raw, Mapping)
        else {}
    )
    enforce_static_allowlist = bool(dyn_cfg.get("enforce_static_allowlist", False))
    if enforce_static_allowlist:
        excluded_symbols = [s for s in dynamic_symbols if s not in set(static_symbols)]
        symbols = [s for s in dynamic_symbols if s in set(static_symbols)] or list(static_symbols)
    else:
        excluded_symbols = []
        symbols = list(dict.fromkeys(dynamic_symbols or static_symbols))
    default_symbol = symbols[0]
    lookback_minutes = max(120, int(alpha_cfg.lookback_minutes))

    need_market_ctx = bool({"research_agent", "quant_strategist"} & selected)
    macro_cfg = (
        ((rules_raw.get("research") or {}).get("macro_context") or {})
        if isinstance(rules_raw, Mapping)
        else {}
    )
    macro_context_enabled = bool(_as_float(macro_cfg.get("enabled"), default=1.0) > 0.0)
    macro_timeout_sec = max(3, int(_as_float(macro_cfg.get("timeout_sec"), default=6.0)))
    macro_context: dict[str, Any] = {
        "as_of_utc": _utcnow().isoformat(),
        "status": "DISABLED",
        "risk_mode": "UNKNOWN",
        "fear_greed_index": {},
        "crypto_market": {},
        "errors": [],
    }
    if need_market_ctx and bool(macro_context_enabled):
        try:
            macro_context = fetch_macro_context(timeout_sec=int(macro_timeout_sec))
        except Exception as exc:
            macro_context = {
                "as_of_utc": _utcnow().isoformat(),
                "status": "FAIL",
                "risk_mode": "UNKNOWN",
                "fear_greed_index": {},
                "crypto_market": {},
                "errors": [f"macro_context_failed:{str(exc)[:120]}"],
            }
    learning_feedback: dict[str, Any] = {"enabled": False, "profiles": {}, "summary": {}}
    candidates: list[dict[str, Any]] = (
        _quant_candidate_rows(
            rules_raw=rules_raw,
            rules=rules,
            symbols=symbols,
            lookback_minutes=lookback_minutes,
            alpha_cfg=alpha_cfg,
        )
        if need_market_ctx
        else []
    )
    if need_market_ctx:
        learning_feedback = _build_quant_feedback_profiles(repo=repo, rules_raw=rules_raw, symbols=symbols)
        feedback_profiles = learning_feedback.get("profiles") if isinstance(learning_feedback, Mapping) else {}
        if isinstance(feedback_profiles, Mapping) and feedback_profiles:
            candidates = _apply_feedback_to_candidates(candidates=candidates, feedback_profiles=feedback_profiles)

    top = candidates[0] if candidates else {"symbol": default_symbol, "score": 0.0, "snapshot": {}, "features": {}}
    symbol = str(top.get("symbol") or default_symbol)
    snapshot = (top.get("snapshot") or {}) if isinstance(top.get("snapshot"), Mapping) else {}
    features = (top.get("features") or {}) if isinstance(top.get("features"), Mapping) else {}
    if need_market_ctx and (not snapshot or not features):
        snapshot, features = _build_features(symbol=symbol, lookback_minutes=lookback_minutes, alpha_cfg=alpha_cfg)

    # Shared state
    pause = repo.fetch_pause_state()
    recon = repo.fetch_latest_reconciliation()
    report_ids: dict[str, str] = {}

    if "research_agent" in selected:
        research_tasks = repo.fetch_ready_agent_tasks(agent_name="research_agent", limit=10)
        research_cfg = (rules_raw.get("research") or {}) if isinstance(rules_raw, Mapping) else {}
        web_cfg = (research_cfg.get("web_search") or {}) if isinstance(research_cfg, Mapping) else {}
        headline_limit = max(4, min(24, int(_as_float(research_cfg.get("headline_limit"), default=12))))
        web_search_enabled = bool(web_cfg.get("enabled", False))
        web_search_provider = str(web_cfg.get("provider") or "auto").strip() or "auto"
        web_search_limit = max(
            1,
            min(
                headline_limit,
                int(_as_float(web_cfg.get("limit"), default=min(8, headline_limit))),
            ),
        )
        web_search_timeout_sec = max(3, int(_as_float(web_cfg.get("timeout_sec"), default=10)))
        rss_timeout_sec = max(3, int(_as_float(research_cfg.get("rss_timeout_sec"), default=12)))
        try:
            headlines = fetch_crypto_headlines(
                symbol=symbol,
                limit=headline_limit,
                include_web_search=web_search_enabled,
                web_search_provider=web_search_provider,
                web_search_limit=web_search_limit,
                web_search_timeout_sec=web_search_timeout_sec,
                rss_timeout_sec=rss_timeout_sec,
            )
        except Exception:
            headlines = []
        research_route = llm_route_for_agent(rules_raw=rules_raw, agent_name="research_agent")
        brief = research_agent_daily_brief(
            symbol=symbol,
            snapshot=snapshot,
            features=features,
            ops={"pause": pause, "latest_reconciliation": recon},
            headlines=headlines,
            llm_route=research_route,
        )
        compact_headlines: list[dict[str, Any]] = []
        for h in list(headlines or [])[:10]:
            if not isinstance(h, Mapping):
                continue
            compact_headlines.append(
                {
                    "source": h.get("source"),
                    "channel": h.get("channel"),
                    "title": h.get("title"),
                    "url": h.get("url"),
                    "published_at": h.get("published_at"),
                }
            )
        news_signal = build_news_signal(
            headlines=compact_headlines,
            risk_watchlist=list(brief.risk_watchlist),
        )
        research_id = _store_report(
            repo=repo,
            report_date_kst=now_kst,
            cycle_key=cycle_key,
            meeting_context=meeting_context,
            agent_name="research_agent",
            team_scope="RESEARCH",
            title="사전업무 리포트(Research)",
            summary=brief.summary,
            findings={
                "key_findings": list(brief.key_findings),
                "llm_meta": brief.llm_meta,
                "symbol": symbol,
                "headlines": compact_headlines,
                "news_signal": dict(news_signal),
                "research_fetch": {
                    "headline_limit": int(headline_limit),
                    "web_search_enabled": bool(web_search_enabled),
                    "web_search_provider": str(web_search_provider),
                    "web_search_limit": int(web_search_limit),
                    "web_search_timeout_sec": int(web_search_timeout_sec),
                    "rss_timeout_sec": int(rss_timeout_sec),
                },
                "macro_context": dict(macro_context),
                "assigned_tasks": [dict(t.get("payload") or {}) for t in research_tasks[:5]],
            },
            risks={"watchlist": list(brief.risk_watchlist)},
            action_items={
                "next_actions": list(brief.next_actions),
                "task_refs": [str(t.get("task_id")) for t in research_tasks[:10]],
            },
        )
        report_ids["research_agent"] = str(research_id)
        for t in research_tasks:
            try:
                repo.mark_agent_task_completed(
                    task_id=str(t.get("task_id")),
                    agent_name="research_agent",
                    result={"cycle_key": cycle_key, "report_id": str(research_id)},
                )
            except Exception:
                pass

    if "quant_strategist" in selected:
        quant_tasks = repo.fetch_ready_agent_tasks(agent_name="quant_strategist", limit=10)
        top = candidates[0] if candidates else {"symbol": symbol, "score": 0.0, "snapshot": snapshot, "features": features}
        fees_cfg = (rules_raw.get("fees") or {}) if isinstance(rules_raw, Mapping) else {}
        fee_total_bps = _as_float(fees_cfg.get("fallback_bid_fee_bps"), default=5.0) + _as_float(
            fees_cfg.get("fallback_ask_fee_bps"), default=5.0
        )
        bt_cfg = (rules_raw.get("quant_backtest") or {}) if isinstance(rules_raw, Mapping) else {}
        base_slippage_bps = _as_float(bt_cfg.get("base_slippage_bps"), default=1.0)
        spread_penalty_mult = _as_float(bt_cfg.get("spread_penalty_mult"), default=0.30)
        low_liquidity_penalty_bps = _as_float(bt_cfg.get("low_liquidity_penalty_bps"), default=1.2)
        fill_ratio = _as_float(bt_cfg.get("fill_ratio"), default=0.92)
        lookback_bars = int(_as_float(bt_cfg.get("lookback_bars"), default=500))
        hold_bars = int(_as_float(bt_cfg.get("hold_bars"), default=24))
        backtests: list[dict[str, Any]] = []
        for row in candidates[: min(8, len(candidates))]:
            sym = str(row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            bt = _quick_backtest_candidate(
                symbol=sym,
                alpha_cfg=alpha_cfg,
                fee_total_bps=fee_total_bps,
                base_slippage_bps=base_slippage_bps,
                spread_penalty_mult=spread_penalty_mult,
                low_liquidity_penalty_bps=low_liquidity_penalty_bps,
                fill_ratio=fill_ratio,
                lookback_bars=lookback_bars,
                hold_bars=hold_bars,
            )
            backtests.append(bt)
        if backtests:
            bt_rank = sorted(backtests, key=lambda x: float(x.get("backtest_score") or -999.0), reverse=True)
            bt_best = bt_rank[0]
            bt_best_symbol = str(bt_best.get("symbol") or "").strip()
            if bt_best_symbol:
                for row in candidates:
                    if str(row.get("symbol") or "").strip().upper() == bt_best_symbol.upper():
                        top = row
                        break
        default_target = _as_float(((rules_raw.get("governance") or {}).get("default_target_position_pct")), default=10.0)
        max_pos = float(rules.risk.max_position_pct_per_symbol)
        quote_ccy = _quote_currency(str(top.get("symbol") or symbol))
        cash = float(repo.fetch_cash_balance(currency=quote_ccy))
        top_snapshot = (top.get("snapshot") or {}) if isinstance(top.get("snapshot"), Mapping) else {}
        top_mid = _as_float(top_snapshot.get("mid_price"), default=0.0)
        top_pos = repo.fetch_position(str(top.get("symbol") or symbol))
        top_qty = float(top_pos.qty) if top_pos else 0.0
        equity = float(cash) + float(top_qty) * float(top_mid)
        capital_profile = resolve_capital_policy(
            rules_raw=rules_raw,
            equity_krw=equity,
            default_target_position_pct=default_target,
            max_position_pct_per_symbol=max_pos,
            cooldown_minutes_after_trigger=int(rules.risk.cooldown_minutes_after_trigger),
        )
        target = min(
            float(capital_profile.max_target_position_pct),
            float(capital_profile.max_position_pct_per_symbol),
            max(0.0, default_target if float(top.get("score") or 0.0) > 0 else 0.0),
        )
        feedback_profiles = learning_feedback.get("profiles") if isinstance(learning_feedback, Mapping) else {}
        top_sym = _to_symbol(top.get("symbol") or symbol)
        top_feedback_profile = (
            dict(feedback_profiles.get(top_sym) or {}) if isinstance(feedback_profiles, Mapping) and top_sym else {}
        )
        top_feedback_adj = _as_float(
            top.get("feedback_score_adjustment"),
            default=_as_float(top_feedback_profile.get("score_adjustment"), default=0.0),
        )
        quant_summary = (
            f"후보 1순위 {top.get('symbol')} (signal_score={float(top.get('score') or 0.0):.3f}), "
            f"권장 목표비중 {target:.1f}% (tier={capital_profile.tier_name}, equity={equity:.0f} KRW)"
        )
        if abs(float(top_feedback_adj)) > 1e-6:
            quant_summary += f", feedback_adj={float(top_feedback_adj):+.3f}"
        quant_risks: list[str] = []
        if _as_float((top.get("snapshot") or {}).get("spread_bps"), default=0.0) > float(rules.cost_guard.max_spread_bps_entry):
            quant_risks.append("상위 후보의 스프레드가 제한보다 넓음")
        if float(top_feedback_adj) < -0.10:
            quant_risks.append("과거 성과/비용 회고 기준에서 상위 후보 품질이 약함")
        feedback_snapshot: dict[str, Any] = {}
        if isinstance(feedback_profiles, Mapping):
            for row in candidates[:8]:
                sym = _to_symbol(row.get("symbol"))
                if not sym:
                    continue
                prof = feedback_profiles.get(sym)
                if isinstance(prof, Mapping):
                    feedback_snapshot[sym] = dict(prof)
        quant_id = _store_report(
            repo=repo,
            report_date_kst=now_kst,
            cycle_key=cycle_key,
            meeting_context=meeting_context,
            agent_name="quant_strategist",
            team_scope="STRATEGY",
            title="사전업무 리포트(Quant)",
            summary=quant_summary,
            findings={
                "universe_selection": {
                    "source": universe.source,
                    "total_krw_markets": universe.total_krw_markets,
                    "ranked_count": universe.ranked_count,
                    "symbols": symbols[:20],
                    "enforce_static_allowlist": bool(enforce_static_allowlist),
                    "excluded_not_allowed": excluded_symbols[:50],
                    "top24h_turnover": universe.top24h_turnover[:10],
                },
                "candidates": candidates[:8],
                "backtest": {
                    "engine": "quick_alpha_score_replay_v1_0",
                    "params": {
                        "tf_min": 1,
                        "entry_alpha": alpha_cfg.entry_alpha,
                        "cooldown_minutes": alpha_cfg.cooldown_minutes,
                        "fee_total_bps": fee_total_bps,
                        "base_slippage_bps": base_slippage_bps,
                        "spread_penalty_mult": spread_penalty_mult,
                        "low_liquidity_penalty_bps": low_liquidity_penalty_bps,
                        "fill_ratio": fill_ratio,
                        "lookback_bars": lookback_bars,
                        "hold_bars": hold_bars,
                    },
                    "ranked": sorted(backtests, key=lambda x: float(x.get("backtest_score") or -999.0), reverse=True)[:8],
                },
                "macro_context": dict(macro_context),
                "learning_feedback": {
                    "enabled": bool(learning_feedback.get("enabled")) if isinstance(learning_feedback, Mapping) else False,
                    "summary": (
                        dict(learning_feedback.get("summary") or {}) if isinstance(learning_feedback, Mapping) else {}
                    ),
                    "top_symbol": top_sym,
                    "top_symbol_profile": top_feedback_profile,
                    "by_symbol": feedback_snapshot,
                },
                "suggested_plan": {"symbol": top.get("symbol"), "target_position_pct": target},
                "capital_profile": capital_profile.as_dict(),
                "assigned_tasks": [dict(t.get("payload") or {}) for t in quant_tasks[:5]],
            },
            risks={"watchlist": quant_risks},
            action_items={
                "next_actions": [
                    "회의에서 상위 후보의 비용/리스크 충돌 여부 검증",
                    "target_position_pct와 cooldown/rebalance_band 합의",
                    "자본 티어(capital_policy) 상한과 플랜 비중 일치 여부 확인",
                ],
                "task_refs": [str(t.get("task_id")) for t in quant_tasks[:10]],
            },
        )
        report_ids["quant_strategist"] = str(quant_id)
        for t in quant_tasks:
            try:
                repo.mark_agent_task_completed(
                    task_id=str(t.get("task_id")),
                    agent_name="quant_strategist",
                    result={"cycle_key": cycle_key, "report_id": str(quant_id)},
                )
            except Exception:
                pass

    if "risk_manager" in selected:
        risk_tasks = repo.fetch_ready_agent_tasks(agent_name="risk_manager", limit=10)
        latest_pnl = (repo.fetch_pnl_daily(limit=1) or [None])[0]
        risk_watch: list[str] = []
        if pause.get("paused"):
            risk_watch.append("현재 PAUSE 상태")
        if str((recon or {}).get("status") or "OK").upper() == "FAIL":
            risk_watch.append("정합성 FAIL 상태")
        risk_summary = (
            f"리스크 한도: 일손실 {rules.risk.max_daily_loss_pct:.2f}%, "
            f"심볼 최대비중 {rules.risk.max_position_pct_per_symbol:.1f}%"
        )
        if latest_pnl:
            risk_summary += f", 최근 일손익={latest_pnl.get('realized_pnl')}"
        risk_id = _store_report(
            repo=repo,
            report_date_kst=now_kst,
            cycle_key=cycle_key,
            meeting_context=meeting_context,
            agent_name="risk_manager",
            team_scope="RISK",
            title="사전업무 리포트(Risk)",
            summary=risk_summary,
            findings={
                "limits": {
                    "max_daily_loss_pct": rules.risk.max_daily_loss_pct,
                    "max_position_pct_per_symbol": rules.risk.max_position_pct_per_symbol,
                },
                "assigned_tasks": [dict(t.get("payload") or {}) for t in risk_tasks[:5]],
            },
            risks={"watchlist": risk_watch},
            action_items={
                "next_actions": ["회의에서 하드게이트(veto 조건) 재확인"],
                "task_refs": [str(t.get("task_id")) for t in risk_tasks[:10]],
            },
        )
        report_ids["risk_manager"] = str(risk_id)
        for t in risk_tasks:
            try:
                repo.mark_agent_task_completed(
                    task_id=str(t.get("task_id")),
                    agent_name="risk_manager",
                    result={"cycle_key": cycle_key, "report_id": str(risk_id)},
                )
            except Exception:
                pass

    if "ops_manager" in selected:
        ops_tasks = repo.fetch_ready_agent_tasks(agent_name="ops_manager", limit=10)
        deliveries = repo.fetch_notification_deliveries(limit=30)
        failed_n = len([d for d in deliveries if str(d.get("status") or "").upper() == "FAILED"])
        ops_summary = (
            f"운영상태 recon={str((recon or {}).get('status') or 'OK').upper()}, "
            f"paused={bool(pause.get('paused'))}, 최근 알림 실패={failed_n}건"
        )
        ops_risks: list[str] = []
        if failed_n > 0:
            ops_risks.append("최근 알림 전송 실패 존재")
        if bool(pause.get("paused")):
            ops_risks.append("시스템 PAUSE 상태")
        ops_id = _store_report(
            repo=repo,
            report_date_kst=now_kst,
            cycle_key=cycle_key,
            meeting_context=meeting_context,
            agent_name="ops_manager",
            team_scope="OPS",
            title="사전업무 리포트(Ops)",
            summary=ops_summary,
            findings={
                "reconciliation": recon,
                "pause": pause,
                "notification_failures_recent": failed_n,
                "assigned_tasks": [dict(t.get("payload") or {}) for t in ops_tasks[:5]],
            },
            risks={"watchlist": ops_risks},
            action_items={
                "next_actions": ["회의 전 recon/pause 상태 재검증", "알림 실패 원인 점검"],
                "task_refs": [str(t.get("task_id")) for t in ops_tasks[:10]],
            },
        )
        report_ids["ops_manager"] = str(ops_id)
        for t in ops_tasks:
            try:
                repo.mark_agent_task_completed(
                    task_id=str(t.get("task_id")),
                    agent_name="ops_manager",
                    result={"cycle_key": cycle_key, "report_id": str(ops_id)},
                )
            except Exception:
                pass

    return WorkCycleResult(cycle_key=cycle_key, report_ids=report_ids)
