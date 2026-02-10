from __future__ import annotations

import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import yaml

from ai_invest.agents.market_agent import market_agent_opine
from ai_invest.agents.ops_agent import ops_agent_opine
from ai_invest.agents.regime_agent import regime_agent_opine
from ai_invest.agents.risk_agent import risk_agent_opine
from ai_invest.config.rules_loader import RulesConfig, load_rules
from ai_invest.execution.paper_execution import PaperExecutor
from ai_invest.judge.ai_judge import ai_judge_shadow_decide
from ai_invest.judge.safe_judge import safe_judge_decide
from ai_invest.learning.outcome_evaluator import evaluate_closed_trade
from ai_invest.market_data.features import build_feature_snapshot_from_candles
from ai_invest.market_data.upbit_public import MarketSnapshot, fetch_candles_minutes, fetch_market_snapshot
from ai_invest.notifications.service import NotificationService
from ai_invest.ops.reconciliation import record_reconciliation_check
from ai_invest.storage.postgres import (
    DbAgentOpinion,
    DbDecision,
    DbDecisionOutcome,
    DbEvent,
    DbPauseLog,
    DbRuleVersion,
    DbRun,
    PostgresRepo,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timeframe_to_minutes(tf: str) -> int:
    tf = tf.strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    raise ValueError(f"Unsupported timeframe: {tf}")


def build_common_payload(
    *,
    run_id: uuid.UUID,
    rule_version_id: uuid.UUID,
    decision_id: uuid.UUID,
    snapshot: MarketSnapshot,
    features: dict[str, Any],
    ops: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": str(run_id),
        "rule_version_id": str(rule_version_id),
        "decision_id": str(decision_id),
        "timestamp_utc": _utcnow().isoformat(),
        "symbol": snapshot.symbol,
        "snapshot": {
            "last_price": snapshot.last_price,
            "best_bid": snapshot.best_bid,
            "best_ask": snapshot.best_ask,
            "mid_price": snapshot.mid_price,
            "spread_bps": snapshot.spread_bps,
        },
        "features": features,
        "ops": ops,
        "context": context,
    }


def default_context(*, daily_loss_pct: float = 0.0) -> dict[str, Any]:
    return {
        "account": {"daily_loss_pct": float(daily_loss_pct)},
        "risk_limits": {},
    }


def run_paper_loop(*, cycles: int = 1, sleep_sec: float | None = None) -> None:
    rules = load_rules("rules.yaml")
    raw_rules = yaml.safe_load(open("rules.yaml", "r", encoding="utf-8"))

    repo = PostgresRepo()
    executor = PaperExecutor(repo)
    notifier = NotificationService(repo)

    run_id = uuid.uuid4()
    rule_version_id = uuid.uuid4()
    now0 = _utcnow()
    repo.insert_run(
        DbRun(
            run_id=run_id,
            run_type="PAPER",
            started_at=now0,
            ended_at=None,
            description="paper loop (dev)",
            config={"rules_version": rules.version},
            git_commit=None,
        )
    )
    repo.insert_rule_version(
        DbRuleVersion(
            rule_version_id=rule_version_id,
            created_by="system",
            parent_version=None,
            status="ACTIVE",
            summary="bootstrap from rules.yaml (paper loop)",
            rules_dsl=raw_rules,
            diff={},
            backtest_report={},
        )
    )

    symbol = rules.universe.symbols[0]
    timeframe_entry = str(raw_rules.get("signal", {}).get("timeframe_entry", "15m"))
    tf_min = _timeframe_to_minutes(timeframe_entry)

    for _i in range(cycles):
        decision_id = uuid.uuid4()
        snapshot = fetch_market_snapshot(symbol)
        quote_ts = _utcnow()
        repo.insert_market_quote(
            ts=quote_ts,
            symbol=symbol,
            best_bid=snapshot.best_bid,
            best_ask=snapshot.best_ask,
            mid_price=snapshot.mid_price,
            spread_abs=snapshot.best_ask - snapshot.best_bid,
            spread_bps=snapshot.spread_bps,
            source="upbit_public",
        )
        repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=quote_ts,
                event_type="MARKET_SNAPSHOT",
                entity_type="market_quotes",
                entity_id=f"{symbol}:{int(snapshot.ts_ms)}",
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={
                    "symbol": symbol,
                    "last_price": snapshot.last_price,
                    "best_bid": snapshot.best_bid,
                    "best_ask": snapshot.best_ask,
                    "mid_price": snapshot.mid_price,
                    "spread_bps": snapshot.spread_bps,
                },
            )
        )

        candles = fetch_candles_minutes(symbol, unit=tf_min, count=200)
        highs = [float(c["high_price"]) for c in candles]
        lows = [float(c["low_price"]) for c in candles]
        closes = [float(c["trade_price"]) for c in candles]
        volumes = [float(c["candle_acc_trade_volume"]) for c in candles]
        feat = build_feature_snapshot_from_candles(highs=highs, lows=lows, closes=closes, volumes=volumes)
        repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=_utcnow(),
                event_type="FEATURE_SNAPSHOT",
                entity_type="features",
                entity_id=f"{symbol}:{decision_id}",
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={"symbol": symbol, "decision_id": str(decision_id), "features": asdict(feat)},
            )
        )

        pause_state = bool(repo.fetch_pause_state().get("paused") or False)
        latest_recon = repo.fetch_latest_reconciliation(symbol=symbol)
        recon_status = str((latest_recon or {}).get("status") or "OK").upper()
        ops = {"rate_limit_alert": False, "reconciliation_status": recon_status, "pause_state": pause_state}
        payload = build_common_payload(
            run_id=run_id,
            rule_version_id=rule_version_id,
            decision_id=decision_id,
            snapshot=snapshot,
            features=asdict(feat),
            ops=ops,
            context=default_context(daily_loss_pct=0.0),
        )

        # Agents (opinion-only)
        market = market_agent_opine(payload, rules=rules)
        regime = regime_agent_opine(payload, rules=rules)
        risk = risk_agent_opine(payload, rules=rules)
        ops_op = ops_agent_opine(payload)

        now = _utcnow()

        def store_agent_opinion(agent_name: str, signal: str, confidence: float, raw: dict[str, Any], reason_codes: list[str]) -> None:
            opinion_id = uuid.uuid4()
            repo.insert_agent_opinion(
                DbAgentOpinion(
                    opinion_id=opinion_id,
                    ts=now,
                    symbol=symbol,
                    agent_name=agent_name,
                    signal=signal,
                    confidence=confidence,
                    horizon=timeframe_entry,
                    features=payload.get("features") or {},
                    reason={"reason_codes": reason_codes},
                    raw_payload=raw,
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                )
            )
            repo.insert_event(
                DbEvent(
                    event_id=uuid.uuid4(),
                    ts=now,
                    event_type="AGENT_OPINION",
                    entity_type="agent_opinions",
                    entity_id=str(opinion_id),
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                    payload={
                        "symbol": symbol,
                        "decision_id": str(decision_id),
                        "agent_name": agent_name,
                        "opinion_id": str(opinion_id),
                        "opinion": raw,
                    },
                )
            )

        store_agent_opinion(
            "market_agent",
            market.signal,
            market.confidence,
            asdict(market),
            list(market.reason_codes),
        )
        store_agent_opinion(
            "regime_agent",
            "NONE",
            1.0,
            asdict(regime),
            list(regime.reason_codes),
        )
        store_agent_opinion(
            "risk_agent",
            "NONE",
            1.0,
            asdict(risk),
            list(risk.reason_codes),
        )
        store_agent_opinion(
            "ops_agent",
            "NONE",
            1.0,
            asdict(ops_op),
            list(ops_op.reason_codes),
        )

        # Safe Judge decision
        safe = safe_judge_decide(
            payload,
            rules=rules,
            market={"signal": market.signal, "confidence": market.confidence},
            regime={"trade_allowed": regime.trade_allowed},
            risk={"veto": risk.veto},
            ops={"veto": ops_op.veto},
        )
        repo.insert_decision(
            DbDecision(
                decision_id=decision_id,
                ts=now,
                symbol=symbol,
                judge_type="SAFE",
                action=safe.action,
                score=safe.score,
                confidence=safe.confidence,
                gates=safe.gates,
                selected_reasons=safe.selected_reasons,
                rejected_reasons=safe.rejected_reasons,
                expected_cost_bps=safe.expected_cost_bps,
                expected_rr=safe.expected_rr,
                run_id=run_id,
                rule_version_id=rule_version_id,
            )
        )
        repo.insert_event(
            DbEvent(
                event_id=(safe_event_id := uuid.uuid4()),
                ts=now,
                event_type="SAFE_DECISION",
                entity_type="decisions",
                entity_id=str(decision_id),
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={
                    "symbol": symbol,
                    "decision_id": str(decision_id),
                    "decision": asdict(safe),
                    "agent_inputs": {
                        "market": asdict(market),
                        "regime": asdict(regime),
                        "risk": asdict(risk),
                        "ops": asdict(ops_op),
                    },
                },
            )
        )
        notifier.notify_safe_decision(
            event_id=safe_event_id,
            symbol=symbol,
            action=safe.action,
            reasons=list(safe.selected_reasons),
            run_id=run_id,
            context={
                "last_price": snapshot.last_price,
                "spread_bps": snapshot.spread_bps,
                "rsi_14": feat.rsi_14,
                "atr_pct": feat.atr_pct,
                "vol_zscore": feat.vol_zscore,
                "market_signal": market.signal,
                "market_confidence": market.confidence,
                "regime": regime.regime,
                "regime_trade_allowed": regime.trade_allowed,
                "risk_veto": risk.veto,
                "ops_state": ops_op.system_state,
                "ops_veto": ops_op.veto,
                "reconciliation_status": ops_op.reconciliation_status,
                "pause_state": ops.get("pause_state"),
            },
        )

        # Paper execution
        exec_res = executor.execute(
            run_id=run_id,
            rule_version_id=rule_version_id,
            decision_id=decision_id,
            action=safe.action,
            snapshot=snapshot,
            rules=rules,
        )
        if exec_res is not None:
            notifier.notify_fill(
                event_id=exec_res.fill_event_id,
                symbol=symbol,
                side=exec_res.side,
                qty=exec_res.fill_qty,
                price=exec_res.fill_price,
                fee=exec_res.fee,
                fee_currency=(symbol.split("-", 1)[0] if "-" in symbol else "KRW"),
            )
            if exec_res.closed_trade is not None:
                ev = evaluate_closed_trade(
                    qty=exec_res.closed_trade.qty,
                    avg_entry_price=exec_res.closed_trade.avg_entry_price,
                    avg_exit_price=exec_res.closed_trade.avg_exit_price,
                    realized_pnl_krw=exec_res.closed_trade.realized_pnl,
                    fees_total_krw=exec_res.closed_trade.fees_total,
                )
                outcome_id = uuid.uuid4()
                outcome_decision_id = exec_res.closed_trade.entry_decision_id or exec_res.closed_trade.exit_decision_id
                repo.insert_decision_outcome(
                    DbDecisionOutcome(
                        outcome_id=outcome_id,
                        decision_id=outcome_decision_id,
                        trade_id=exec_res.closed_trade.trade_id,
                        symbol=symbol,
                        ts_open=exec_res.closed_trade.ts_open,
                        ts_close=exec_res.closed_trade.ts_close,
                        outcome_label=ev.outcome_label,
                        error_type=ev.error_type,
                        root_cause=ev.root_cause,
                        evidence_refs={"order_id": exec_res.order_id, "fill_id": str(exec_res.fill_id)},
                        fix_hypothesis=ev.fix_hypothesis,
                        reviewed_by="system",
                        reviewed_at=_utcnow(),
                        run_id=run_id,
                        rule_version_id=rule_version_id,
                        meta={"paper": True, "eval": dict(ev.meta)},
                    )
                )
                repo.insert_event(
                    DbEvent(
                        event_id=uuid.uuid4(),
                        ts=_utcnow(),
                        event_type="DECISION_OUTCOME_RECORDED",
                        entity_type="decision_outcomes",
                        entity_id=str(outcome_id),
                        run_id=run_id,
                        rule_version_id=rule_version_id,
                        payload={
                            "symbol": symbol,
                            "decision_id": str(outcome_decision_id),
                            "trade_id": str(exec_res.closed_trade.trade_id),
                            "outcome_label": ev.outcome_label,
                            "error_type": ev.error_type,
                        },
                    )
                )

        # AI shadow decision (stored, no execution)
        ai = ai_judge_shadow_decide(
            payload,
            rules=rules,
            market={"signal": market.signal, "confidence": market.confidence},
            regime={"trade_allowed": regime.trade_allowed},
            risk={"veto": risk.veto},
            ops={"veto": ops_op.veto},
        )
        ai_decision_id = uuid.uuid4()
        repo.insert_decision(
            DbDecision(
                decision_id=ai_decision_id,
                ts=_utcnow(),
                symbol=symbol,
                judge_type="AI",
                action=ai.action,
                score=ai.score,
                confidence=ai.confidence,
                gates={"shadow": True, "baseline": ai.meta},
                selected_reasons=ai.selected_reasons,
                rejected_reasons=ai.rejected_reasons,
                expected_cost_bps=None,
                expected_rr=None,
                run_id=run_id,
                rule_version_id=rule_version_id,
            )
        )
        repo.insert_event(
            DbEvent(
                event_id=uuid.uuid4(),
                ts=_utcnow(),
                event_type="AI_DECISION",
                entity_type="decisions",
                entity_id=str(ai_decision_id),
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={"symbol": symbol, "shadow_of": str(decision_id), "decision": asdict(ai)},
            )
        )

        # Reconciliation
        check = record_reconciliation_check(repo, run_id=run_id, symbol=symbol)
        if check.status == "FAIL":
            recon_event_id = uuid.uuid4()
            repo.insert_event(
                DbEvent(
                    event_id=recon_event_id,
                    ts=_utcnow(),
                    event_type="RECONCILIATION_FAIL",
                    entity_type="reconciliation_checks",
                    entity_id=str(check.check_id),
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                    payload={"symbol": symbol, "check_id": str(check.check_id), "diff": check.diff_payload},
                )
            )
            notifier.notify_recon_fail(
                event_id=recon_event_id,
                symbol=symbol,
                diff_summary=check.diff_summary,
                run_id=run_id,
            )
            pause_event_id = uuid.uuid4()
            repo.insert_event(
                DbEvent(
                    event_id=pause_event_id,
                    ts=_utcnow(),
                    event_type="PAUSE",
                    entity_type="pause_log",
                    entity_id="AUTO",
                    run_id=run_id,
                    rule_version_id=rule_version_id,
                    payload={"symbol": symbol, "reason_type": "RECON_FAIL"},
                )
            )
            notifier.notify_pause(event_id=pause_event_id, symbol=symbol, reason_type="RECON_FAIL", run_id=run_id)
            repo.insert_pause_log(
                DbPauseLog(
                    pause_id=uuid.uuid4(),
                    ts_pause=_utcnow(),
                    ts_resume=None,
                    reason_type="RECON_FAIL",
                    severity="HIGH",
                    auto_resumable=False,
                    resume_policy={},
                    notes="auto pause due to recon fail",
                    run_id=run_id,
                )
            )
            break

        if sleep_sec is not None:
            time.sleep(float(sleep_sec))
