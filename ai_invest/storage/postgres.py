from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

import psycopg


class PostgresConfigError(RuntimeError):
    pass


def to_psycopg_dsn(dsn: str) -> str:
    # Allow SQLAlchemy-style DSN in .env while using psycopg directly.
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn[len("postgresql+psycopg://") :]
    return dsn


def get_postgres_dsn() -> str:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not dsn:
        raise PostgresConfigError("POSTGRES_DSN is missing")
    return to_psycopg_dsn(dsn)


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _parse_iso_dt(value: Any) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _is_trade_plan_payload_active(payload: Mapping[str, Any], *, now_utc: datetime) -> bool:
    vf = _parse_iso_dt(payload.get("valid_from_kst") or payload.get("valid_from"))
    vt = _parse_iso_dt(payload.get("valid_to_kst") or payload.get("valid_to"))
    if vf is not None and now_utc < vf.astimezone(timezone.utc):
        return False
    if vt is not None and now_utc >= vt.astimezone(timezone.utc):
        return False
    return True


@dataclass(frozen=True)
class DbEvent:
    event_id: uuid.UUID
    ts: datetime
    event_type: str
    entity_type: str
    entity_id: str
    run_id: uuid.UUID | None
    rule_version_id: uuid.UUID | None
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class DbRun:
    run_id: uuid.UUID
    run_type: str
    started_at: datetime
    ended_at: datetime | None
    description: str | None
    config: Mapping[str, Any]
    git_commit: str | None


@dataclass(frozen=True)
class DbRuleVersion:
    rule_version_id: uuid.UUID
    created_by: str
    parent_version: uuid.UUID | None
    status: str
    summary: str
    rules_dsl: Mapping[str, Any]
    diff: Mapping[str, Any] | None
    backtest_report: Mapping[str, Any] | None


@dataclass(frozen=True)
class DbAgentOpinion:
    opinion_id: uuid.UUID
    ts: datetime
    symbol: str
    agent_name: str
    signal: str
    confidence: float
    horizon: str | None
    features: Mapping[str, Any] | None
    reason: Mapping[str, Any] | None
    raw_payload: Mapping[str, Any]
    run_id: uuid.UUID | None
    rule_version_id: uuid.UUID | None


@dataclass(frozen=True)
class DbDecision:
    decision_id: uuid.UUID
    ts: datetime
    symbol: str
    judge_type: str
    action: str
    score: float | None
    confidence: float | None
    gates: Mapping[str, Any]
    selected_reasons: Sequence[str]
    rejected_reasons: Sequence[str]
    expected_cost_bps: float | None
    expected_rr: float | None
    run_id: uuid.UUID | None
    rule_version_id: uuid.UUID | None


@dataclass(frozen=True)
class DbOrder:
    order_id: str
    ts_created: datetime
    symbol: str
    side: str
    order_type: str
    price: float | None
    quantity: float
    time_in_force: str | None
    status: str
    client_order_id: str | None
    meta: Mapping[str, Any] | None
    run_id: uuid.UUID | None
    rule_version_id: uuid.UUID | None


@dataclass(frozen=True)
class DbFill:
    fill_id: uuid.UUID
    order_id: str
    ts_filled: datetime
    price: float
    quantity: float
    fee: float | None
    fee_currency: str | None
    liquidity: str | None
    meta: Mapping[str, Any] | None


@dataclass(frozen=True)
class DbLedgerEntry:
    entry_id: uuid.UUID
    ts: datetime
    entry_type: str  # TRADE_FILL / FEE / DEPOSIT / WITHDRAW / ADJUSTMENT
    symbol: str | None
    currency: str
    amount: float
    price: float | None
    fee_amount: float | None
    fee_currency: str | None
    order_id: str | None
    fill_id: uuid.UUID | None
    meta: Mapping[str, Any] | None


@dataclass(frozen=True)
class DbPosition:
    symbol: str
    ts_updated: datetime
    qty: float
    avg_entry_price: float | None
    unrealized_pnl: float | None
    stop_price: float | None
    take_profit: float | None
    meta: Mapping[str, Any] | None


@dataclass(frozen=True)
class DbExecutionMetric:
    metric_id: uuid.UUID
    order_id: str | None
    symbol: str
    ts_decision: datetime | None
    ts_submit: datetime | None
    ts_first_fill: datetime | None
    ts_last_fill: datetime | None
    decision_mid: float | None
    submit_mid: float | None
    fill_vwap: float | None
    slippage_bps_vs_decision: float | None
    slippage_bps_vs_submit: float | None
    spread_bps_at_submit: float | None
    filled_ratio: float | None
    latency_ms_decision_to_submit: int | None
    latency_ms_submit_to_fill: int | None
    meta: Mapping[str, Any] | None


@dataclass(frozen=True)
class DbReconciliationCheck:
    check_id: uuid.UUID
    ts: datetime
    scope: str
    symbol: str | None
    status: str  # OK / WARN / FAIL
    diff_summary: str | None
    diff_payload: Mapping[str, Any] | None
    action_taken: str | None
    run_id: uuid.UUID | None


@dataclass(frozen=True)
class DbPauseLog:
    pause_id: uuid.UUID
    ts_pause: datetime
    ts_resume: datetime | None
    reason_type: str
    severity: str
    auto_resumable: bool
    resume_policy: Mapping[str, Any] | None
    notes: str | None
    run_id: uuid.UUID | None


@dataclass(frozen=True)
class DbDecisionOutcome:
    outcome_id: uuid.UUID
    decision_id: uuid.UUID
    trade_id: uuid.UUID | None
    symbol: str
    ts_open: datetime | None
    ts_close: datetime | None
    outcome_label: str  # WIN / LOSS / FLAT / MISS
    error_type: str | None  # OC_* only
    root_cause: str | None
    evidence_refs: Mapping[str, Any] | None
    fix_hypothesis: str | None
    reviewed_by: str
    reviewed_at: datetime
    run_id: uuid.UUID | None
    rule_version_id: uuid.UUID | None
    meta: Mapping[str, Any] | None


@dataclass(frozen=True)
class DbAgentDailyReport:
    report_id: uuid.UUID
    report_date: date
    agent_name: str
    team_scope: str
    title: str
    summary: str
    findings: Any | None
    risks: Any | None
    action_items: Any | None
    run_id: uuid.UUID | None
    rule_version_id: uuid.UUID | None


@dataclass(frozen=True)
class DbStrategyReview:
    review_id: uuid.UUID
    week_start: date
    week_end: date
    priority_title: str
    hypothesis: str
    owner: str
    success_criteria: Any
    status: str  # OPEN / IN_PROGRESS / DONE / CANCELED
    evidence: Any | None
    run_id: uuid.UUID | None


@dataclass(frozen=True)
class DbMeetingSession:
    meeting_id: uuid.UUID
    meeting_type: str  # DAILY_RESEARCH / WEEKLY_STRATEGY / INCIDENT_REVIEW
    status: str  # OPEN / CLOSED
    started_at: datetime
    ended_at: datetime | None
    facilitator: str
    participants: Any
    agenda: Any | None
    summary: str | None
    decisions: Any | None
    action_items: Any | None
    run_id: uuid.UUID | None


@dataclass(frozen=True)
class DbMeetingMessage:
    message_id: uuid.UUID
    meeting_id: uuid.UUID
    ts: datetime
    sender_agent: str
    message_type: str  # CLAIM / EVIDENCE / PROPOSAL / QUESTION / ACTION_ITEM
    content: str
    payload: Any | None
    confidence: float | None


class PostgresRepo:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or get_postgres_dsn()

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn)

    def insert_event(self, event: DbEvent) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into events (
                  event_id, ts, event_type, entity_type, entity_id, run_id, rule_version_id, payload
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                """,
                (
                    event.event_id,
                    event.ts,
                    event.event_type,
                    event.entity_type,
                    event.entity_id,
                    event.run_id,
                    event.rule_version_id,
                    json_dumps(event.payload),
                ),
            )
            conn.commit()

    def insert_run(self, run: DbRun) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into runs (run_id, run_type, started_at, ended_at, description, config, git_commit)
                values (%s,%s,%s,%s,%s,%s::jsonb,%s)
                on conflict (run_id) do nothing
                """,
                (
                    run.run_id,
                    run.run_type,
                    run.started_at,
                    run.ended_at,
                    run.description,
                    json_dumps(run.config),
                    run.git_commit,
                ),
            )
            conn.commit()

    def insert_rule_version(self, rv: DbRuleVersion) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into rule_versions (
                  rule_version_id, created_by, parent_version, status, summary, rules_dsl, diff, backtest_report
                )
                values (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)
                on conflict (rule_version_id) do nothing
                """,
                (
                    rv.rule_version_id,
                    rv.created_by,
                    rv.parent_version,
                    rv.status,
                    rv.summary,
                    json_dumps(rv.rules_dsl),
                    json_dumps(rv.diff or {}),
                    json_dumps(rv.backtest_report or {}),
                ),
            )
            conn.commit()

    def insert_agent_opinion(self, opinion: DbAgentOpinion) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into agent_opinions (
                  opinion_id, ts, symbol, agent_name, signal, confidence, horizon, features, reason, raw_payload,
                  run_id, rule_version_id
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
                """,
                (
                    opinion.opinion_id,
                    opinion.ts,
                    opinion.symbol,
                    opinion.agent_name,
                    opinion.signal,
                    opinion.confidence,
                    opinion.horizon,
                    json_dumps(opinion.features or {}),
                    json_dumps(opinion.reason or {}),
                    json_dumps(opinion.raw_payload),
                    opinion.run_id,
                    opinion.rule_version_id,
                ),
            )
            conn.commit()

    def insert_decision(self, decision: DbDecision) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into decisions (
                  decision_id, ts, symbol, judge_type, action, score, confidence, gates,
                  selected_reasons, rejected_reasons, expected_cost_bps, expected_rr, run_id, rule_version_id
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s)
                """,
                (
                    decision.decision_id,
                    decision.ts,
                    decision.symbol,
                    decision.judge_type,
                    decision.action,
                    decision.score,
                    decision.confidence,
                    json_dumps(decision.gates),
                    json_dumps(list(decision.selected_reasons)),
                    json_dumps(list(decision.rejected_reasons)),
                    decision.expected_cost_bps,
                    decision.expected_rr,
                    decision.run_id,
                    decision.rule_version_id,
                ),
            )
            conn.commit()

    def insert_order(self, order: DbOrder) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into orders (
                  order_id, ts_created, symbol, side, order_type, price, quantity, time_in_force,
                  status, client_order_id, meta, run_id, rule_version_id
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                """,
                (
                    order.order_id,
                    order.ts_created,
                    order.symbol,
                    order.side,
                    order.order_type,
                    order.price,
                    order.quantity,
                    order.time_in_force,
                    order.status,
                    order.client_order_id,
                    json_dumps(order.meta or {}),
                    order.run_id,
                    order.rule_version_id,
                ),
            )
            conn.commit()

    def insert_fill(self, fill: DbFill) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into fills (
                  fill_id, order_id, ts_filled, price, quantity, fee, fee_currency, liquidity, meta
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                """,
                (
                    fill.fill_id,
                    fill.order_id,
                    fill.ts_filled,
                    fill.price,
                    fill.quantity,
                    fill.fee,
                    fill.fee_currency,
                    fill.liquidity,
                    json_dumps(fill.meta or {}),
                ),
            )
            conn.commit()

    def insert_ledger_entry(self, entry: DbLedgerEntry) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into ledger_entries (
                  entry_id, ts, entry_type, symbol, currency, amount,
                  price, fee_amount, fee_currency, order_id, fill_id, meta
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                """,
                (
                    entry.entry_id,
                    entry.ts,
                    entry.entry_type,
                    entry.symbol,
                    entry.currency,
                    entry.amount,
                    entry.price,
                    entry.fee_amount,
                    entry.fee_currency,
                    entry.order_id,
                    entry.fill_id,
                    json_dumps(entry.meta or {}),
                ),
            )
            conn.commit()

    def insert_decision_outcome(self, outcome: DbDecisionOutcome) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into decision_outcomes (
                  outcome_id, decision_id, trade_id, symbol, ts_open, ts_close,
                  outcome_label, error_type, root_cause, evidence_refs, fix_hypothesis,
                  reviewed_by, reviewed_at, run_id, rule_version_id, meta
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb)
                """,
                (
                    outcome.outcome_id,
                    outcome.decision_id,
                    outcome.trade_id,
                    outcome.symbol,
                    outcome.ts_open,
                    outcome.ts_close,
                    outcome.outcome_label,
                    outcome.error_type,
                    outcome.root_cause,
                    json_dumps(outcome.evidence_refs or {}),
                    outcome.fix_hypothesis,
                    outcome.reviewed_by,
                    outcome.reviewed_at,
                    outcome.run_id,
                    outcome.rule_version_id,
                    json_dumps(outcome.meta or {}),
                ),
            )
            conn.commit()

    def upsert_tax_export_run(
        self,
        *,
        export_id: uuid.UUID,
        period_start: datetime,
        period_end: datetime,
        status: str,
        checksum_sha256: str | None,
        generated_by: str,
        manifest: Mapping[str, Any],
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into tax_export_runs (
                  export_id, period_start, period_end, status, checksum_sha256, generated_by, manifest, meta
                )
                values (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                on conflict (export_id) do update set
                  period_start=excluded.period_start,
                  period_end=excluded.period_end,
                  status=excluded.status,
                  checksum_sha256=excluded.checksum_sha256,
                  generated_by=excluded.generated_by,
                  manifest=excluded.manifest,
                  meta=excluded.meta
                """,
                (
                    export_id,
                    period_start,
                    period_end,
                    status,
                    checksum_sha256,
                    generated_by,
                    json_dumps(manifest),
                    json_dumps(meta or {}),
                ),
            )
            conn.commit()

    def update_order_status(self, order_id: str, *, status: str, meta_patch: Mapping[str, Any] | None = None) -> None:
        patch = meta_patch or {}
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                update orders
                set status=%s,
                    meta = coalesce(meta, '{}'::jsonb) || %s::jsonb
                where order_id=%s
                """,
                (status, json_dumps(patch), order_id),
            )
            conn.commit()

    def fetch_position(self, symbol: str) -> DbPosition | None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select symbol, ts_updated, qty, avg_entry_price, unrealized_pnl, stop_price, take_profit, meta
                from positions
                where symbol=%s
                """,
                (symbol,),
            )
            row = cur.fetchone()
        if not row:
            return None
        sym, ts_updated, qty, avg_entry_price, unrealized_pnl, stop_price, take_profit, meta = row
        return DbPosition(
            symbol=sym,
            ts_updated=ts_updated,
            qty=float(qty),
            avg_entry_price=avg_entry_price,
            unrealized_pnl=unrealized_pnl,
            stop_price=stop_price,
            take_profit=take_profit,
            meta=meta,
        )

    def fetch_cash_balance(self, *, currency: str) -> float:
        """ledger_entries 기반 단순 현금 잔고(통화별).

        - amount는 현금 유입/유출(+/-)
        - fee_amount가 존재하면 amount에서 차감하여 net cashflow로 계산한다.
        """

        ccy = str(currency or "").strip().upper()
        if not ccy:
            return 0.0
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select coalesce(sum(amount - coalesce(fee_amount, 0.0)), 0.0)
                from ledger_entries
                where currency=%s
                """,
                (ccy,),
            )
            row = cur.fetchone()
        try:
            return float(row[0]) if row else 0.0
        except Exception:
            return 0.0

    def fetch_portfolio_overview(self, *, quote_currency: str = "KRW") -> dict[str, Any]:
        """현재 포트폴리오 요약(현금/포지션 평가/총자산)을 반환한다."""

        ccy = str(quote_currency or "").strip().upper() or "KRW"
        cash = float(self.fetch_cash_balance(currency=ccy))
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select
                  p.symbol,
                  p.qty,
                  p.avg_entry_price,
                  p.ts_updated,
                  q.mid_price,
                  q.ts as quote_ts
                from positions p
                left join lateral (
                  select mq.mid_price, mq.ts
                  from market_quotes mq
                  where mq.symbol=p.symbol
                    and mq.mid_price is not null
                  order by mq.ts desc
                  limit 1
                ) q on true
                where coalesce(p.qty, 0.0) <> 0.0
                order by p.symbol asc
                """
            )
            rows = cur.fetchall()

        positions: list[dict[str, Any]] = []
        position_value = 0.0
        total_entry_value = 0.0
        total_unrealized_pnl = 0.0
        for symbol, qty, avg_entry_price, ts_updated, mid_price, quote_ts in rows:
            qty_f = float(qty or 0.0)
            avg_f = float(avg_entry_price) if avg_entry_price is not None else None
            mid_f = float(mid_price) if mid_price is not None else None
            mark = mid_f if mid_f is not None else (avg_f if avg_f is not None else 0.0)
            value = float(qty_f) * float(mark)
            position_value += value
            unrealized = ((float(mark) - float(avg_f)) * float(qty_f)) if avg_f is not None else None
            entry_value = (float(qty_f) * float(avg_f)) if avg_f is not None else None
            if entry_value is not None:
                total_entry_value += float(entry_value)
            if unrealized is not None:
                total_unrealized_pnl += float(unrealized)
            pnl_pct = None
            if avg_f is not None and float(avg_f) > 0:
                pnl_pct = (float(mark) / float(avg_f) - 1.0) * 100.0
            if unrealized is None:
                pnl_direction = "FLAT"
            elif unrealized > 0:
                pnl_direction = "PLUS"
            elif unrealized < 0:
                pnl_direction = "MINUS"
            else:
                pnl_direction = "FLAT"
            positions.append(
                {
                    "symbol": str(symbol),
                    "qty": qty_f,
                    "avg_entry_price": avg_f,
                    "entry_value_krw": float(entry_value) if entry_value is not None else None,
                    "mark_price": float(mark),
                    "mid_price": mid_f,
                    "value_krw": float(value),
                    "unrealized_pnl_krw": unrealized,
                    "unrealized_pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
                    "pnl_direction": pnl_direction,
                    "ts_updated": ts_updated,
                    "quote_ts": quote_ts,
                }
            )

        equity = float(cash) + float(position_value)
        exposure_pct = (float(position_value) / float(equity) * 100.0) if float(equity) > 0 else 0.0
        total_unrealized_pct_on_entry = (
            (float(total_unrealized_pnl) / float(total_entry_value) * 100.0) if float(total_entry_value) > 0 else 0.0
        )
        total_unrealized_pct_on_equity = (
            (float(total_unrealized_pnl) / float(equity) * 100.0) if float(equity) > 0 else 0.0
        )
        return {
            "quote_currency": ccy,
            "cash_krw": float(cash),
            "position_value_krw": float(position_value),
            "equity_krw": float(equity),
            "exposure_pct": float(exposure_pct),
            "total_entry_value_krw": float(total_entry_value),
            "total_unrealized_pnl_krw": float(total_unrealized_pnl),
            "total_unrealized_pnl_pct_on_entry": float(total_unrealized_pct_on_entry),
            "total_unrealized_pnl_pct_on_equity": float(total_unrealized_pct_on_equity),
            "positions_count": len(positions),
            "positions": positions,
        }

    def paper_seed_exists(self, *, currency: str) -> bool:
        ccy = str(currency or "").strip().upper()
        if not ccy:
            return False
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select 1
                from ledger_entries
                where currency=%s
                  and entry_type='DEPOSIT'
                  and (meta->>'paper_seed')='true'
                limit 1
                """,
                (ccy,),
            )
            return cur.fetchone() is not None

    def ensure_paper_seed_cash(self, *, currency: str, amount: float) -> None:
        """Paper 계정 seed 현금(DEPOSIT)을 1회만 주입한다.

        - live 모드에서는 사용하지 않는 것을 전제로 한다.
        - meta.paper_seed=true 를 키로 중복 주입을 방지한다.
        """

        ccy = str(currency or "").strip().upper()
        amt = float(amount or 0.0)
        if not ccy or amt <= 0:
            return
        if self.paper_seed_exists(currency=ccy):
            return
        self.insert_ledger_entry(
            DbLedgerEntry(
                entry_id=uuid.uuid4(),
                ts=datetime.now(timezone.utc),
                entry_type="DEPOSIT",
                symbol=None,
                currency=ccy,
                amount=amt,
                price=None,
                fee_amount=None,
                fee_currency=None,
                order_id=None,
                fill_id=None,
                meta={"paper": True, "paper_seed": True, "note": "initial paper cash"},
            )
        )

    def upsert_position(self, pos: DbPosition) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into positions (
                  symbol, ts_updated, qty, avg_entry_price, unrealized_pnl, stop_price, take_profit, meta
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                on conflict (symbol) do update set
                  ts_updated=excluded.ts_updated,
                  qty=excluded.qty,
                  avg_entry_price=excluded.avg_entry_price,
                  unrealized_pnl=excluded.unrealized_pnl,
                  stop_price=excluded.stop_price,
                  take_profit=excluded.take_profit,
                  meta=excluded.meta
                """,
                (
                    pos.symbol,
                    pos.ts_updated,
                    pos.qty,
                    pos.avg_entry_price,
                    pos.unrealized_pnl,
                    pos.stop_price,
                    pos.take_profit,
                    json_dumps(pos.meta or {}),
                ),
            )
            conn.commit()

    def insert_execution_metric(self, metric: DbExecutionMetric) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into execution_metrics (
                  metric_id, order_id, symbol, ts_decision, ts_submit, ts_first_fill, ts_last_fill,
                  decision_mid, submit_mid, fill_vwap,
                  slippage_bps_vs_decision, slippage_bps_vs_submit, spread_bps_at_submit,
                  filled_ratio, latency_ms_decision_to_submit, latency_ms_submit_to_fill,
                  meta
                )
                values (
                  %s,%s,%s,%s,%s,%s,%s,
                  %s,%s,%s,
                  %s,%s,%s,
                  %s,%s,%s,
                  %s::jsonb
                )
                """,
                (
                    metric.metric_id,
                    metric.order_id,
                    metric.symbol,
                    metric.ts_decision,
                    metric.ts_submit,
                    metric.ts_first_fill,
                    metric.ts_last_fill,
                    metric.decision_mid,
                    metric.submit_mid,
                    metric.fill_vwap,
                    metric.slippage_bps_vs_decision,
                    metric.slippage_bps_vs_submit,
                    metric.spread_bps_at_submit,
                    metric.filled_ratio,
                    metric.latency_ms_decision_to_submit,
                    metric.latency_ms_submit_to_fill,
                    json_dumps(metric.meta or {}),
                ),
            )
            conn.commit()

    def insert_reconciliation_check(self, check: DbReconciliationCheck) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into reconciliation_checks (
                  check_id, ts, scope, symbol, status, diff_summary, diff_payload, action_taken, run_id
                )
                values (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                """,
                (
                    check.check_id,
                    check.ts,
                    check.scope,
                    check.symbol,
                    check.status,
                    check.diff_summary,
                    json_dumps(check.diff_payload or {}),
                    check.action_taken,
                    check.run_id,
                ),
            )
            conn.commit()

    def insert_pause_log(self, pause: DbPauseLog) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into pause_log (
                  pause_id, ts_pause, ts_resume, reason_type, severity, auto_resumable, resume_policy, notes, run_id
                )
                values (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                """,
                (
                    pause.pause_id,
                    pause.ts_pause,
                    pause.ts_resume,
                    pause.reason_type,
                    pause.severity,
                    pause.auto_resumable,
                    json_dumps(pause.resume_policy or {}),
                    pause.notes,
                    pause.run_id,
                ),
            )
            conn.commit()

    def insert_market_quote(
        self,
        *,
        ts: datetime,
        symbol: str,
        best_bid: float,
        best_ask: float,
        mid_price: float,
        spread_abs: float,
        spread_bps: float,
        source: str = "upbit_public",
    ) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into market_quotes (
                  ts, symbol, best_bid, best_ask, mid_price, spread_abs, spread_bps, source
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (symbol, ts) do nothing
                """,
                (ts, symbol, best_bid, best_ask, mid_price, spread_abs, spread_bps, source),
            )
            conn.commit()

    def insert_notification_delivery(
        self,
        *,
        delivery_id: uuid.UUID,
        event_id: uuid.UUID,
        channel: str,
        template_id: str,
        severity: str,
        status: str,
        attempt_count: int,
        last_error: str | None,
        dedupe_key: str | None,
        payload: Mapping[str, Any],
        sent_at: datetime | None,
    ) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into notification_deliveries (
                  delivery_id, event_id, channel, template_id, severity, status,
                  attempt_count, last_error, dedupe_key, payload, sent_at
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                """,
                (
                    delivery_id,
                    event_id,
                    channel,
                    template_id,
                    severity,
                    status,
                    attempt_count,
                    last_error,
                    dedupe_key,
                    json_dumps(payload),
                    sent_at,
                ),
            )
            conn.commit()

    def was_notification_sent_recently(self, *, dedupe_key: str, within_sec: int = 60) -> bool:
        if not dedupe_key:
            return False
        within_sec = int(within_sec)
        if within_sec <= 0:
            return False
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select 1
                from notification_deliveries
                where dedupe_key=%s
                  and status='SENT'
                  and created_at >= now() - make_interval(secs => %s)
                limit 1
                """,
                (dedupe_key, within_sec),
            )
            return cur.fetchone() is not None

    def upsert_communication_room(
        self,
        *,
        room_id: uuid.UUID,
        channel_type: str,
        room_key: str,
        room_name: str,
        team_scope: str,
        is_active: bool = True,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into communication_rooms (
                  room_id, channel_type, room_key, room_name, team_scope, is_active, meta
                )
                values (%s,%s,%s,%s,%s,%s,%s::jsonb)
                on conflict (channel_type, room_key) do update set
                  room_name=excluded.room_name,
                  team_scope=excluded.team_scope,
                  is_active=excluded.is_active,
                  meta=excluded.meta
                """,
                (room_id, channel_type, room_key, room_name, team_scope, bool(is_active), json_dumps(meta or {})),
            )
            conn.commit()

    def fetch_communication_rooms(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select room_id, channel_type, room_key, room_name, team_scope, is_active
                from communication_rooms
                order by team_scope asc, room_name asc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for room_id, channel_type, room_key, room_name, team_scope, is_active in rows:
            out.append(
                {
                    "room_id": str(room_id),
                    "channel_type": channel_type,
                    "room_key": room_key,
                    "room_name": room_name,
                    "team_scope": team_scope,
                    "is_active": bool(is_active),
                }
            )
        return out

    def insert_agent_daily_report(self, report: DbAgentDailyReport) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into agent_daily_reports (
                  report_id, report_date, agent_name, team_scope, title, summary,
                  findings, risks, action_items,
                  run_id, rule_version_id
                )
                values (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
                """,
                (
                    report.report_id,
                    report.report_date,
                    report.agent_name,
                    report.team_scope,
                    report.title,
                    report.summary,
                    json_dumps(report.findings or {}),
                    json_dumps(report.risks or {}),
                    json_dumps(report.action_items or {}),
                    report.run_id,
                    report.rule_version_id,
                ),
            )
            conn.commit()

    def insert_meeting_session(self, session: DbMeetingSession) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into meeting_sessions (
                  meeting_id, meeting_type, status, started_at, ended_at,
                  facilitator, participants, agenda, summary, decisions, action_items,
                  run_id
                )
                values (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s)
                """,
                (
                    session.meeting_id,
                    session.meeting_type,
                    session.status,
                    session.started_at,
                    session.ended_at,
                    session.facilitator,
                    json_dumps(session.participants or []),
                    json_dumps(session.agenda or {}),
                    session.summary,
                    json_dumps(session.decisions or {}),
                    json_dumps(session.action_items or {}),
                    session.run_id,
                ),
            )
            conn.commit()

    def update_meeting_session(
        self,
        *,
        meeting_id: uuid.UUID,
        status: str | None = None,
        ended_at: datetime | None = None,
        summary: str | None = None,
        decisions: Any | None = None,
        action_items: Any | None = None,
    ) -> None:
        """Update mutable fields for a meeting session.

        Used for live meetings: create session as OPEN, then close it with summary/decisions.
        """

        sets: list[str] = []
        params: list[Any] = []

        if status is not None:
            sets.append("status=%s")
            params.append(str(status))
        if ended_at is not None:
            sets.append("ended_at=%s")
            params.append(ended_at)
        if summary is not None:
            sets.append("summary=%s")
            params.append(str(summary))
        if decisions is not None:
            sets.append("decisions=%s::jsonb")
            params.append(json_dumps(decisions))
        if action_items is not None:
            sets.append("action_items=%s::jsonb")
            params.append(json_dumps(action_items))

        if not sets:
            return

        params.append(meeting_id)

        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                update meeting_sessions
                set {", ".join(sets)}
                where meeting_id=%s
                """,
                tuple(params),
            )
            conn.commit()

    def insert_meeting_message(self, msg: DbMeetingMessage) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into meeting_messages (
                  message_id, meeting_id, ts, sender_agent, message_type, content, payload, confidence
                )
                values (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                """,
                (
                    msg.message_id,
                    msg.meeting_id,
                    msg.ts,
                    msg.sender_agent,
                    msg.message_type,
                    msg.content,
                    json_dumps(msg.payload or {}),
                    msg.confidence,
                ),
            )
            conn.commit()

    def fetch_agent_daily_reports(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select report_id, report_date, agent_name, team_scope, title, summary, findings, risks, action_items, created_at
                from agent_daily_reports
                order by report_date desc, created_at desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for report_id, report_date, agent_name, team_scope, title, summary, findings, risks, action_items, created_at in rows:
            out.append(
                {
                    "report_id": str(report_id),
                    "report_date": str(report_date),
                    "agent_name": agent_name,
                    "team_scope": team_scope,
                    "title": title,
                    "summary": summary,
                    "findings": findings,
                    "risks": risks,
                    "action_items": action_items,
                    "created_at": created_at,
                }
            )
        return out

    def fetch_latest_agent_daily_report(self, *, agent_name: str) -> dict[str, Any] | None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select report_id, report_date, agent_name, team_scope, title, summary, findings, risks, action_items, created_at
                from agent_daily_reports
                where agent_name=%s
                order by created_at desc
                limit 1
                """,
                (str(agent_name),),
            )
            row = cur.fetchone()
        if not row:
            return None
        report_id, report_date, agent_name2, team_scope, title, summary, findings, risks, action_items, created_at = row
        return {
            "report_id": str(report_id),
            "report_date": str(report_date),
            "agent_name": agent_name2,
            "team_scope": team_scope,
            "title": title,
            "summary": summary,
            "findings": findings,
            "risks": risks,
            "action_items": action_items,
            "created_at": created_at,
        }

    def insert_strategy_review(self, review: DbStrategyReview) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into strategy_reviews (
                  review_id, week_start, week_end, priority_title, hypothesis, owner,
                  success_criteria, status, evidence, run_id
                )
                values (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s)
                """,
                (
                    review.review_id,
                    review.week_start,
                    review.week_end,
                    review.priority_title,
                    review.hypothesis,
                    review.owner,
                    json_dumps(review.success_criteria or {}),
                    review.status,
                    json_dumps(review.evidence or {}),
                    review.run_id,
                ),
            )
            conn.commit()

    def fetch_strategy_reviews(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select review_id, week_start, week_end, priority_title, hypothesis, owner, success_criteria, status, evidence, created_at
                from strategy_reviews
                order by week_start desc, created_at desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for review_id, week_start, week_end, priority_title, hypothesis, owner, success_criteria, status, evidence, created_at in rows:
            out.append(
                {
                    "review_id": str(review_id),
                    "week_start": str(week_start),
                    "week_end": str(week_end),
                    "priority_title": priority_title,
                    "hypothesis": hypothesis,
                    "owner": owner,
                    "success_criteria": success_criteria,
                    "status": status,
                    "evidence": evidence,
                    "created_at": created_at,
                }
            )
        return out

    def fetch_agent_opinions(
        self,
        *,
        limit: int = 200,
        symbol: str | None = None,
        agent_name: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []

        if symbol:
            where.append("o.symbol=%s")
            params.append(symbol)
        if agent_name:
            where.append("o.agent_name=%s")
            params.append(agent_name)

        where_sql = ("where " + " and ".join(where)) if where else ""

        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                select
                  o.opinion_id, o.ts, o.symbol, o.agent_name, o.signal, o.confidence, o.horizon,
                  o.features, o.reason, o.raw_payload, o.run_id, o.rule_version_id,
                  (e.payload->>'decision_id') as decision_id
                from agent_opinions o
                left join events e
                  on e.event_type='AGENT_OPINION'
                 and e.entity_type='agent_opinions'
                 and e.entity_id = o.opinion_id::text
                {where_sql}
                order by o.ts desc
                limit %s
                """,
                (*params, limit),
            )
            rows = cur.fetchall()

        out: list[dict[str, Any]] = []
        for (
            opinion_id,
            ts,
            sym,
            agent,
            signal,
            confidence,
            horizon,
            features,
            reason,
            raw_payload,
            run_id,
            rule_version_id,
            decision_id,
        ) in rows:
            out.append(
                {
                    "opinion_id": str(opinion_id),
                    "ts": ts,
                    "symbol": sym,
                    "agent_name": agent,
                    "signal": signal,
                    "confidence": float(confidence),
                    "horizon": horizon,
                    "features": features,
                    "reason": reason,
                    "raw_payload": raw_payload,
                    "run_id": str(run_id) if run_id else None,
                    "rule_version_id": str(rule_version_id) if rule_version_id else None,
                    "decision_id": str(decision_id) if decision_id else None,
                }
            )
        return out

    def fetch_latest_decisions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select decision_id, ts, symbol, judge_type, action, confidence, selected_reasons
                from decisions
                order by ts desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for decision_id, ts, symbol, judge_type, action, confidence, selected_reasons in rows:
            out.append(
                {
                    "decision_id": str(decision_id),
                    "ts": ts,
                    "symbol": symbol,
                    "judge_type": judge_type,
                    "action": action,
                    "confidence": confidence,
                    "selected_reasons": selected_reasons,
                }
            )
        return out

    def fetch_latest_decision(self, *, judge_type: str = "SAFE") -> dict[str, Any] | None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select decision_id, ts, symbol, judge_type, action, score, confidence, gates,
                       selected_reasons, rejected_reasons, expected_cost_bps, expected_rr
                from decisions
                where judge_type=%s
                order by ts desc
                limit 1
                """,
                (judge_type,),
            )
            row = cur.fetchone()
        if not row:
            return None
        (
            decision_id,
            ts,
            symbol,
            judge_type,
            action,
            score,
            confidence,
            gates,
            selected_reasons,
            rejected_reasons,
            expected_cost_bps,
            expected_rr,
        ) = row
        return {
            "decision_id": str(decision_id),
            "ts": ts,
            "symbol": symbol,
            "judge_type": judge_type,
            "action": action,
            "score": score,
            "confidence": confidence,
            "gates": gates,
            "selected_reasons": selected_reasons,
            "rejected_reasons": rejected_reasons,
            "expected_cost_bps": expected_cost_bps,
            "expected_rr": expected_rr,
        }

    def fetch_decision_by_id(self, *, decision_id: str) -> dict[str, Any] | None:
        try:
            did = uuid.UUID(str(decision_id))
        except Exception:
            return None
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select decision_id, ts, symbol, judge_type, action, score, confidence, gates,
                       selected_reasons, rejected_reasons, expected_cost_bps, expected_rr
                from decisions
                where decision_id=%s
                """,
                (did,),
            )
            row = cur.fetchone()
        if not row:
            return None
        (
            decision_id2,
            ts,
            symbol,
            judge_type,
            action,
            score,
            confidence,
            gates,
            selected_reasons,
            rejected_reasons,
            expected_cost_bps,
            expected_rr,
        ) = row
        return {
            "decision_id": str(decision_id2),
            "ts": ts,
            "symbol": symbol,
            "judge_type": judge_type,
            "action": action,
            "score": score,
            "confidence": confidence,
            "gates": gates,
            "selected_reasons": selected_reasons,
            "rejected_reasons": rejected_reasons,
            "expected_cost_bps": expected_cost_bps,
            "expected_rr": expected_rr,
        }

    def fetch_event_by_entity(
        self, *, event_type: str, entity_type: str, entity_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select event_id, ts, payload
                from events
                where event_type=%s and entity_type=%s and entity_id=%s
                order by ts desc
                limit 1
                """,
                (event_type, entity_type, entity_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        event_id, ts, payload = row
        return {"event_id": str(event_id), "ts": ts, "payload": payload}

    def fetch_latest_event(self, *, event_type: str) -> dict[str, Any] | None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select event_id, ts, event_type, entity_type, entity_id, run_id, rule_version_id, payload
                from events
                where event_type=%s
                order by ts desc
                limit 1
                """,
                (event_type,),
            )
            row = cur.fetchone()
        if not row:
            return None
        event_id, ts, event_type2, entity_type, entity_id, run_id, rule_version_id, payload = row
        return {
            "event_id": str(event_id),
            "ts": ts,
            "event_type": event_type2,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "run_id": str(run_id) if run_id else None,
            "rule_version_id": str(rule_version_id) if rule_version_id else None,
            "payload": payload,
        }

    def fetch_latest_trade_plan_event(self, *, prefer_active: bool = True, lookback_limit: int = 300) -> dict[str, Any] | None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select event_id, ts, event_type, entity_type, entity_id, run_id, rule_version_id, payload
                from events
                where event_type='TRADE_PLAN_SET'
                order by ts desc
                limit %s
                """,
                (int(max(1, lookback_limit)),),
            )
            rows = cur.fetchall()
        if not rows:
            return None

        latest_any: dict[str, Any] | None = None
        now_utc = datetime.now(timezone.utc)
        for event_id, ts, event_type, entity_type, entity_id, run_id, rule_version_id, payload in rows:
            if not isinstance(payload, Mapping):
                continue
            ev = {
                "event_id": str(event_id),
                "ts": ts,
                "event_type": event_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "run_id": str(run_id) if run_id else None,
                "rule_version_id": str(rule_version_id) if rule_version_id else None,
                "payload": payload,
            }
            if latest_any is None:
                latest_any = ev
            if bool(prefer_active) and _is_trade_plan_payload_active(payload, now_utc=now_utc):
                return ev
        return latest_any

    def fetch_latest_trade_plan(self, *, prefer_active: bool = True, lookback_limit: int = 300) -> dict[str, Any] | None:
        ev = self.fetch_latest_trade_plan_event(prefer_active=prefer_active, lookback_limit=lookback_limit)
        if not ev:
            return None
        payload = ev.get("payload")
        if not isinstance(payload, Mapping):
            return None
        return {"event_id": ev.get("event_id"), "ts": ev.get("ts"), **dict(payload)}

    def fetch_latest_governance_policy(self) -> dict[str, Any] | None:
        ev = self.fetch_latest_event(event_type="GOVERNANCE_POLICY_SET")
        if not ev:
            return None
        payload = ev.get("payload")
        if not isinstance(payload, Mapping):
            return None
        return {"event_id": ev.get("event_id"), "ts": ev.get("ts"), **dict(payload)}

    def fetch_ready_agent_tasks(self, *, agent_name: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select e.event_id, e.ts, e.entity_id, e.payload
                from events e
                where e.event_type='AGENT_TASK_ASSIGNED'
                  and coalesce(e.payload->>'target_agent','')=%s
                  and coalesce(e.payload->>'status','READY')='READY'
                  and not exists (
                    select 1
                    from events c
                    where c.event_type='AGENT_TASK_COMPLETED'
                      and coalesce(c.payload->>'task_id','')=e.entity_id
                  )
                order by e.ts asc
                limit %s
                """,
                (str(agent_name), int(limit)),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for event_id, ts, entity_id, payload in rows:
            out.append(
                {
                    "event_id": str(event_id),
                    "ts": ts,
                    "task_id": str(entity_id),
                    "payload": payload if isinstance(payload, Mapping) else {},
                }
            )
        return out

    def mark_agent_task_completed(
        self,
        *,
        task_id: str,
        agent_name: str,
        result: Mapping[str, Any] | None = None,
        run_id: uuid.UUID | None = None,
        rule_version_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        ev_id = uuid.uuid4()
        self.insert_event(
            DbEvent(
                event_id=ev_id,
                ts=datetime.now(timezone.utc),
                event_type="AGENT_TASK_COMPLETED",
                entity_type="agent_tasks",
                entity_id=str(task_id),
                run_id=run_id,
                rule_version_id=rule_version_id,
                payload={
                    "task_id": str(task_id),
                    "target_agent": str(agent_name),
                    "status": "DONE",
                    "result": dict(result or {}),
                },
            )
        )
        return ev_id

    def fetch_ai_shadow_decision_for(self, *, safe_decision_id: str) -> dict[str, Any] | None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select entity_id
                from events
                where event_type='AI_DECISION'
                  and payload->>'shadow_of'=%s
                order by ts desc
                limit 1
                """,
                (safe_decision_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        ai_decision_id = str(row[0])
        return self.fetch_decision_by_id(decision_id=ai_decision_id)

    def fetch_latest_reconciliation(self, *, symbol: str | None = None) -> dict[str, Any] | None:
        with self.connect() as conn, conn.cursor() as cur:
            if symbol:
                cur.execute(
                    """
                    select check_id, ts, scope, symbol, status, diff_summary, diff_payload, action_taken, run_id
                    from reconciliation_checks
                    where symbol=%s
                    order by ts desc
                    limit 1
                    """,
                    (symbol,),
                )
            else:
                cur.execute(
                    """
                    select check_id, ts, scope, symbol, status, diff_summary, diff_payload, action_taken, run_id
                    from reconciliation_checks
                    order by ts desc
                    limit 1
                    """
                )
            row = cur.fetchone()
        if not row:
            return None
        check_id, ts, scope, sym, status, diff_summary, diff_payload, action_taken, run_id = row
        return {
            "check_id": str(check_id),
            "ts": ts,
            "scope": scope,
            "symbol": sym,
            "status": status,
            "diff_summary": diff_summary,
            "diff_payload": diff_payload,
            "action_taken": action_taken,
            "run_id": str(run_id) if run_id else None,
        }

    def fetch_pause_state(self) -> dict[str, Any]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select pause_id, ts_pause, ts_resume, reason_type, severity, auto_resumable, notes, run_id
                from pause_log
                order by ts_pause desc
                limit 1
                """
            )
            row = cur.fetchone()
        if not row:
            return {"paused": False, "latest": None}
        pause_id, ts_pause, ts_resume, reason_type, severity, auto_resumable, notes, run_id = row
        latest = {
            "pause_id": str(pause_id),
            "ts_pause": ts_pause,
            "ts_resume": ts_resume,
            "reason_type": reason_type,
            "severity": severity,
            "auto_resumable": auto_resumable,
            "notes": notes,
            "run_id": str(run_id) if run_id else None,
        }
        paused = ts_resume is None
        return {"paused": paused, "latest": latest}

    def meeting_slot_exists(self, *, slot_key: str) -> bool:
        slot_key = str(slot_key).strip()
        if not slot_key:
            return False
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select 1
                from meeting_sessions
                where agenda->>'slot_key'=%s
                limit 1
                """,
                (slot_key,),
            )
            return cur.fetchone() is not None

    def fetch_meeting_sessions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select meeting_id, meeting_type, status, started_at, ended_at, facilitator, participants, summary, decisions, action_items, run_id
                from meeting_sessions
                order by started_at desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for meeting_id, meeting_type, status, started_at, ended_at, facilitator, participants, summary, decisions, action_items, run_id in rows:
            out.append(
                {
                    "meeting_id": str(meeting_id),
                    "meeting_type": meeting_type,
                    "status": status,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "facilitator": facilitator,
                    "participants": participants,
                    "summary": summary,
                    "decisions": decisions,
                    "action_items": action_items,
                    "run_id": str(run_id) if run_id else None,
                }
            )
        return out

    def fetch_meeting_session(self, *, meeting_id: str) -> dict[str, Any] | None:
        try:
            mid = uuid.UUID(str(meeting_id))
        except Exception:
            return None
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select meeting_id, meeting_type, status, started_at, ended_at, facilitator, participants, agenda, summary, decisions, action_items, run_id
                from meeting_sessions
                where meeting_id=%s
                """,
                (mid,),
            )
            row = cur.fetchone()
        if not row:
            return None
        (
            meeting_id2,
            meeting_type,
            status,
            started_at,
            ended_at,
            facilitator,
            participants,
            agenda,
            summary,
            decisions,
            action_items,
            run_id,
        ) = row
        return {
            "meeting_id": str(meeting_id2),
            "meeting_type": meeting_type,
            "status": status,
            "started_at": started_at,
            "ended_at": ended_at,
            "facilitator": facilitator,
            "participants": participants,
            "agenda": agenda,
            "summary": summary,
            "decisions": decisions,
            "action_items": action_items,
            "run_id": str(run_id) if run_id else None,
        }

    def fetch_meeting_messages(self, *, meeting_id: str, limit: int = 500) -> list[dict[str, Any]]:
        try:
            mid = uuid.UUID(str(meeting_id))
        except Exception:
            return []
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select message_id, ts, sender_agent, message_type, content, payload, confidence
                from meeting_messages
                where meeting_id=%s
                order by ts asc
                limit %s
                """,
                (mid, limit),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for message_id, ts, sender_agent, message_type, content, payload, confidence in rows:
            out.append(
                {
                    "message_id": str(message_id),
                    "ts": ts,
                    "sender_agent": sender_agent,
                    "message_type": message_type,
                    "content": content,
                    "payload": payload,
                    "confidence": confidence,
                }
            )
        return out

    def fetch_recent_events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select event_id, ts, event_type, entity_type, entity_id, run_id, rule_version_id, payload
                from events
                order by ts desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for event_id, ts, event_type, entity_type, entity_id, run_id, rule_version_id, payload in rows:
            out.append(
                {
                    "event_id": str(event_id),
                    "ts": ts,
                    "event_type": event_type,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "run_id": str(run_id) if run_id else None,
                    "rule_version_id": str(rule_version_id) if rule_version_id else None,
                    "payload": payload,
                }
            )
        return out

    def fetch_execution_metrics(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select metric_id, order_id, symbol, ts_submit, fill_vwap, slippage_bps_vs_submit, spread_bps_at_submit, filled_ratio
                from execution_metrics
                order by ts_submit desc nulls last
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for metric_id, order_id, symbol, ts_submit, fill_vwap, slip_submit, spread_submit, filled_ratio in rows:
            out.append(
                {
                    "metric_id": str(metric_id),
                    "order_id": order_id,
                    "symbol": symbol,
                    "ts_submit": ts_submit,
                    "fill_vwap": fill_vwap,
                    "slippage_bps_vs_submit": slip_submit,
                    "spread_bps_at_submit": spread_submit,
                    "filled_ratio": filled_ratio,
                }
            )
        return out

    def fetch_reconciliation_checks(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select check_id, ts, scope, symbol, status, diff_summary, action_taken
                from reconciliation_checks
                order by ts desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for check_id, ts, scope, symbol, status, diff_summary, action_taken in rows:
            out.append(
                {
                    "check_id": str(check_id),
                    "ts": ts,
                    "scope": scope,
                    "symbol": symbol,
                    "status": status,
                    "diff_summary": diff_summary,
                    "action_taken": action_taken,
                }
            )
        return out

    def fetch_pause_logs(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select pause_id, ts_pause, ts_resume, reason_type, severity, auto_resumable, notes
                from pause_log
                order by ts_pause desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for pause_id, ts_pause, ts_resume, reason_type, severity, auto_resumable, notes in rows:
            out.append(
                {
                    "pause_id": str(pause_id),
                    "ts_pause": ts_pause,
                    "ts_resume": ts_resume,
                    "reason_type": reason_type,
                    "severity": severity,
                    "auto_resumable": auto_resumable,
                    "notes": notes,
                }
            )
        return out

    def fetch_notification_deliveries(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select delivery_id, created_at, channel, template_id, severity, status, attempt_count, last_error
                from notification_deliveries
                order by created_at desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for delivery_id, created_at, channel, template_id, severity, status, attempt_count, last_error in rows:
            out.append(
                {
                    "delivery_id": str(delivery_id),
                    "created_at": created_at,
                    "channel": channel,
                    "template_id": template_id,
                    "severity": severity,
                    "status": status,
                    "attempt_count": attempt_count,
                    "last_error": last_error,
                }
            )
        return out

    def fetch_tax_export_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select export_id, period_start, period_end, generated_at, status, checksum_sha256, generated_by
                from tax_export_runs
                order by generated_at desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for export_id, period_start, period_end, generated_at, status, checksum_sha256, generated_by in rows:
            out.append(
                {
                    "export_id": str(export_id),
                    "period_start": period_start,
                    "period_end": period_end,
                    "generated_at": generated_at,
                    "status": status,
                    "checksum_sha256": checksum_sha256,
                    "generated_by": generated_by,
                }
            )
        return out

    def fetch_pnl_daily(self, *, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select day, realized_pnl, fees_paid, trades_count, max_drawdown
                from pnl_daily
                order by day desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for day, realized_pnl, fees_paid, trades_count, max_drawdown in rows:
            out.append(
                {
                    "day": str(day),
                    "realized_pnl": realized_pnl,
                    "fees_paid": fees_paid,
                    "trades_count": trades_count,
                    "max_drawdown": max_drawdown,
                }
            )
        return out

    def fetch_realized_trades(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select trade_id, symbol, ts_open, ts_close, side, qty, avg_entry_price, avg_exit_price, realized_pnl, fees_total, pnl_bps
                from realized_trades
                order by ts_close desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for (
            trade_id,
            symbol,
            ts_open,
            ts_close,
            side,
            qty,
            avg_entry_price,
            avg_exit_price,
            realized_pnl,
            fees_total,
            pnl_bps,
        ) in rows:
            out.append(
                {
                    "trade_id": str(trade_id),
                    "symbol": symbol,
                    "ts_open": ts_open,
                    "ts_close": ts_close,
                    "side": side,
                    "qty": qty,
                    "avg_entry_price": avg_entry_price,
                    "avg_exit_price": avg_exit_price,
                    "realized_pnl": realized_pnl,
                    "fees_total": fees_total,
                    "pnl_bps": pnl_bps,
                }
            )
        return out

    def fetch_ledger_entries(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select entry_id, ts, entry_type, symbol, currency, amount, price, fee_amount, fee_currency, order_id, fill_id
                from ledger_entries
                order by ts desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for entry_id, ts, entry_type, symbol, currency, amount, price, fee_amount, fee_currency, order_id, fill_id in rows:
            out.append(
                {
                    "entry_id": str(entry_id),
                    "ts": ts,
                    "entry_type": entry_type,
                    "symbol": symbol,
                    "currency": currency,
                    "amount": amount,
                    "price": price,
                    "fee_amount": fee_amount,
                    "fee_currency": fee_currency,
                    "order_id": order_id,
                    "fill_id": str(fill_id) if fill_id else None,
                }
            )
        return out

    def fetch_decision_outcomes(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select outcome_id, reviewed_at, decision_id, trade_id, symbol, ts_open, ts_close, outcome_label, error_type, root_cause
                from decision_outcomes
                order by reviewed_at desc
                limit %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for outcome_id, reviewed_at, decision_id, trade_id, symbol, ts_open, ts_close, outcome_label, error_type, root_cause in rows:
            out.append(
                {
                    "outcome_id": str(outcome_id),
                    "reviewed_at": reviewed_at,
                    "decision_id": str(decision_id),
                    "trade_id": str(trade_id) if trade_id else None,
                    "symbol": symbol,
                    "ts_open": ts_open,
                    "ts_close": ts_close,
                    "outcome_label": outcome_label,
                    "error_type": error_type,
                    "root_cause": root_cause,
                }
            )
        return out

    def insert_realized_trade(
        self,
        *,
        trade_id: uuid.UUID,
        symbol: str,
        ts_open: datetime,
        ts_close: datetime,
        side: str,
        qty: float,
        avg_entry_price: float,
        avg_exit_price: float,
        realized_pnl: float,
        fees_total: float,
        pnl_bps: float | None,
        tags: Mapping[str, Any] | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into realized_trades (
                  trade_id, symbol, ts_open, ts_close, side, qty,
                  avg_entry_price, avg_exit_price,
                  realized_pnl, fees_total, pnl_bps, tags, meta
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                """,
                (
                    trade_id,
                    symbol,
                    ts_open,
                    ts_close,
                    side,
                    qty,
                    avg_entry_price,
                    avg_exit_price,
                    realized_pnl,
                    fees_total,
                    pnl_bps,
                    json_dumps(tags or {}),
                    json_dumps(meta or {}),
                ),
            )
            conn.commit()

    def upsert_pnl_daily_delta(
        self,
        *,
        day: str,
        realized_pnl_delta: float,
        fees_paid_delta: float,
        trades_count_delta: int,
    ) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into pnl_daily (day, realized_pnl, fees_paid, trades_count)
                values (%s,%s,%s,%s)
                on conflict (day) do update set
                  realized_pnl = pnl_daily.realized_pnl + excluded.realized_pnl,
                  fees_paid = pnl_daily.fees_paid + excluded.fees_paid,
                  trades_count = pnl_daily.trades_count + excluded.trades_count
                """,
                (day, realized_pnl_delta, fees_paid_delta, trades_count_delta),
            )
            conn.commit()
