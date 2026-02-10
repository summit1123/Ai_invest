#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import psycopg
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit(
        "[실패] Python 의존성이 설치되어 있지 않습니다(psycopg 누락).\n"
        "아래 중 하나로 실행하세요:\n"
        "  1) uv sync && .venv/bin/python scripts/e2e_insert_sample_decision.py\n"
        "  2) uv run python scripts/e2e_insert_sample_decision.py\n"
    ) from exc

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_invest.config.rules_loader import load_rules
from ai_invest.judge.safe_judge import safe_judge_decide


ENV_PATH = ROOT / ".env"
RULES_PATH = ROOT / "rules.yaml"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def to_psycopg_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+psycopg://"):
        return "postgresql://" + dsn[len("postgresql+psycopg://") :]
    return dsn


def jdump(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def ensure_table(cur: psycopg.Cursor, table: str) -> None:
    cur.execute("select to_regclass(%s)", (table,))
    if cur.fetchone()[0] is None:
        raise SystemExit(
            f"[실패] 필요한 테이블이 없습니다: {table}\n"
            " - 먼저 `uv run python scripts/init_schema_v1_1.py`를 실행하세요."
        )


def main() -> int:
    if not ENV_PATH.exists():
        print("[실패] .env 파일이 없습니다.")
        return 1

    env = load_env(ENV_PATH)
    dsn = env.get("POSTGRES_DSN", "")
    if not dsn:
        print("[실패] POSTGRES_DSN 값이 비어 있습니다.")
        return 1

    rules = load_rules(RULES_PATH)
    rules_dsl = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc)
    run_id = uuid.uuid4()
    rule_version_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    opinion_id = uuid.uuid4()

    payload = {
        "run_id": str(run_id),
        "rule_version_id": str(rule_version_id),
        "decision_id": str(decision_id),
        "timestamp_utc": now.isoformat(),
        "symbol": "KRW-BTC",
        "snapshot": {
            "last_price": 43_120_500,
            "best_bid": 43_120_000,
            "best_ask": 43_121_000,
            "mid_price": 43_120_500,
            "spread_bps": 2.32,
        },
        "features": {
            "atr_pct": 1.12,
            "rsi_14": 58.4,
            "vol_zscore": 1.7,
            "missing_rate_1m": 0.0,
        },
        "ops": {
            "rate_limit_alert": False,
            "reconciliation_status": "OK",
            "pause_state": False,
        },
        "context": {
            "account": {"daily_loss_pct": 0.0},
            "risk_limits": {
                "max_daily_loss_pct": rules.risk.max_daily_loss_pct,
                "max_slippage_bps": rules.cost_guard.max_predicted_slippage_bps,
            },
        },
    }

    market_opinion = {
        "signal": "LONG",
        "confidence": 0.70,
        "target_position_pct": 10.0,
        "reason_codes": ["RG_PASS"],
    }
    regime_opinion = {"regime": "TREND", "trade_allowed": True, "reason_codes": ["RG_PASS"]}
    risk_opinion = {"veto": False, "reason_codes": ["RG_PASS"]}
    ops_opinion = {"system_state": "OK", "veto": False, "reconciliation_status": "OK", "reason_codes": ["RG_PASS"]}

    decision = safe_judge_decide(
        payload,
        rules=rules,
        market=market_opinion,
        regime=regime_opinion,
        risk=risk_opinion,
        ops=ops_opinion,
    )

    # DB writes
    dsn = to_psycopg_dsn(dsn)
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                for required in (
                    "public.events",
                    "public.decisions",
                    "public.agent_opinions",
                    "public.runs",
                    "public.rule_versions",
                ):
                    ensure_table(cur, required)

                # runs / rule_versions are not FK-bound, but inserting them makes tracing easier.
                cur.execute(
                    """
                    insert into runs (run_id, run_type, started_at, description, config, git_commit)
                    values (%s, %s, %s, %s, %s::jsonb, %s)
                    on conflict (run_id) do nothing
                    """,
                    (
                        run_id,
                        "PAPER",
                        now,
                        "bootstrap sample run (e2e check)",
                        jdump({"rules_version": rules.version}),
                        None,
                    ),
                )

                cur.execute(
                    """
                    insert into rule_versions (
                      rule_version_id, created_by, parent_version, status, summary, rules_dsl, diff, backtest_report
                    )
                    values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                    on conflict (rule_version_id) do nothing
                    """,
                    (
                        rule_version_id,
                        "system",
                        None,
                        "ACTIVE",
                        "bootstrap from rules.yaml",
                        jdump(rules_dsl),
                        jdump({}),
                        jdump({}),
                    ),
                )

                # agent_opinions (+ event mirror)
                cur.execute(
                    """
                    insert into agent_opinions (
                      opinion_id, ts, symbol, agent_name, signal, confidence, horizon, features, reason, raw_payload,
                      run_id, rule_version_id
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)
                    """,
                    (
                        opinion_id,
                        now,
                        payload["symbol"],
                        "market_agent",
                        "LONG",
                        float(market_opinion["confidence"]),
                        "15m",
                        jdump(payload["features"]),
                        jdump({"reason_codes": market_opinion.get("reason_codes", [])}),
                        jdump(market_opinion),
                        run_id,
                        rule_version_id,
                    ),
                )

                agent_event_id = uuid.uuid4()
                cur.execute(
                    """
                    insert into events (
                      event_id, ts, event_type, entity_type, entity_id, run_id, rule_version_id, payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        agent_event_id,
                        now,
                        "AGENT_OPINION",
                        "agent_opinions",
                        str(opinion_id),
                        run_id,
                        rule_version_id,
                        jdump(
                            {
                                "symbol": payload["symbol"],
                                "agent_name": "market_agent",
                                "opinion_id": str(opinion_id),
                                "opinion": market_opinion,
                            }
                        ),
                    ),
                )

                # decisions (+ event mirror)
                cur.execute(
                    """
                    insert into decisions (
                      decision_id, ts, symbol, judge_type, action, score, confidence, gates,
                      selected_reasons, rejected_reasons, expected_cost_bps, expected_rr, run_id, rule_version_id
                    )
                    values (
                      %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                      %s::jsonb, %s::jsonb, %s, %s, %s, %s
                    )
                    """,
                    (
                        decision_id,
                        now,
                        payload["symbol"],
                        "SAFE",
                        decision.action,
                        decision.score,
                        decision.confidence,
                        jdump(decision.gates),
                        jdump(decision.selected_reasons),
                        jdump(decision.rejected_reasons),
                        decision.expected_cost_bps,
                        decision.expected_rr,
                        run_id,
                        rule_version_id,
                    ),
                )

                safe_event_id = uuid.uuid4()
                cur.execute(
                    """
                    insert into events (
                      event_id, ts, event_type, entity_type, entity_id, run_id, rule_version_id, payload
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        safe_event_id,
                        now,
                        "SAFE_DECISION",
                        "decisions",
                        str(decision_id),
                        run_id,
                        rule_version_id,
                        jdump(
                            {
                                "symbol": payload["symbol"],
                                "decision_id": str(decision_id),
                                "input": payload,
                                "agent_inputs": {
                                    "market": market_opinion,
                                    "regime": regime_opinion,
                                    "risk": risk_opinion,
                                    "ops": ops_opinion,
                                },
                                "decision": asdict(decision),
                            }
                        ),
                    ),
                )

                # Quick verification: counts by run_id
                cur.execute("select count(*) from events where run_id=%s", (run_id,))
                events_count = cur.fetchone()[0]
                cur.execute("select count(*) from decisions where run_id=%s", (run_id,))
                decisions_count = cur.fetchone()[0]
                cur.execute("select count(*) from agent_opinions where run_id=%s", (run_id,))
                opinions_count = cur.fetchone()[0]

            conn.commit()

        print("[성공] 샘플 decision chain 저장 완료")
        print(f"- run_id={run_id}")
        print(f"- rule_version_id={rule_version_id}")
        print(f"- decision_id={decision_id}")
        print(f"- opinion_id={opinion_id}")
        print(f"- events_count={events_count}, decisions_count={decisions_count}, opinions_count={opinions_count}")
        return 0
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover
        print(f"[실패] DB 적재 실패: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
