from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from ai_invest.config.dotenv import load_dotenv
from ai_invest.config.rules_loader import load_rules
from ai_invest.storage.postgres import PostgresRepo


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "meta": {"ts_utc": utc_now_iso(), "request_id": str(uuid.uuid4())}}


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    # Contract-first: rules must load at boot or the app should fail fast.
    load_dotenv()
    load_rules("rules.yaml")
    yield


app = FastAPI(title="ai-invest", version="0.1.0", lifespan=lifespan)


@app.get("/healthz", tags=["시스템"], summary="헬스 체크")
def healthz() -> dict[str, Any]:
    return ok({"status": "ok"})


@app.get("/api/v1/ui/latest-decisions", tags=["의사결정"], summary="최근 의사결정 목록")
def latest_decisions(limit: int = 20) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_latest_decisions(limit=limit)})


@app.get("/api/v1/ui/today-overview", tags=["대시보드"], summary="오늘 대시보드 요약")
def today_overview() -> dict[str, Any]:
    repo = PostgresRepo()
    latest_safe = repo.fetch_latest_decision(judge_type="SAFE")
    latest_ai = repo.fetch_latest_decision(judge_type="AI")
    pause = repo.fetch_pause_state()
    latest_recon = repo.fetch_latest_reconciliation()
    return ok(
        {
            "latest_safe_decision": latest_safe,
            "latest_ai_decision": latest_ai,
            "pause": pause,
            "latest_reconciliation": latest_recon,
        }
    )


@app.get("/api/v1/ui/timeline", tags=["이벤트"], summary="이벤트 타임라인")
def timeline(limit: int = 200) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_recent_events(limit=limit)})


@app.get("/api/v1/ui/execution-quality", tags=["실행/비용"], summary="실행 품질(TCA-lite) 조회")
def execution_quality(limit: int = 200) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_execution_metrics(limit=limit)})


@app.get("/api/v1/ui/reconciliation-status", tags=["운영"], summary="정합성 체크 목록")
def reconciliation_status(limit: int = 200) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_reconciliation_checks(limit=limit)})


@app.get("/api/v1/ui/pause-log", tags=["운영"], summary="PAUSE 로그")
def pause_log(limit: int = 200) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_pause_logs(limit=limit)})


@app.get("/api/v1/ui/notifications-delivery", tags=["알림"], summary="알림 전송 이력")
def notifications_delivery(limit: int = 200) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_notification_deliveries(limit=limit)})


@app.get("/api/v1/ui/ledger", tags=["정산"], summary="원장(ledger) 조회")
def ledger(limit: int = 200) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_ledger_entries(limit=limit)})


@app.get("/api/v1/ui/outcomes", tags=["학습/복기"], summary="Outcome(복기) 목록")
def outcomes(limit: int = 200) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_decision_outcomes(limit=limit)})


@app.get("/api/v1/ui/tax-exports", tags=["정산"], summary="Tax Export 실행 목록")
def tax_exports(limit: int = 50) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_tax_export_runs(limit=limit)})


@app.get("/api/v1/ui/conference/{decision_id}", tags=["의사결정"], summary="회의 뷰(에이전트 입력 포함)")
def conference(decision_id: str) -> dict[str, Any]:
    repo = PostgresRepo()
    safe = repo.fetch_decision_by_id(decision_id=decision_id)
    event = repo.fetch_event_by_entity(event_type="SAFE_DECISION", entity_type="decisions", entity_id=decision_id)
    agent_inputs = (event or {}).get("payload", {}).get("agent_inputs") if event else None
    return ok({"decision": safe, "agent_inputs": agent_inputs, "event": event})


@app.get("/api/v1/ui/judge/{decision_id}", tags=["의사결정"], summary="Safe vs AI 판정 비교")
def judge(decision_id: str) -> dict[str, Any]:
    repo = PostgresRepo()
    safe = repo.fetch_decision_by_id(decision_id=decision_id)
    ai = repo.fetch_ai_shadow_decision_for(safe_decision_id=decision_id)
    return ok({"safe": safe, "ai_shadow": ai})


@app.get("/api/v1/ui/collaboration/rooms", tags=["협업"], summary="협업 채널(방) 목록")
def collaboration_rooms(limit: int = 200) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_communication_rooms(limit=limit)})


@app.get("/api/v1/ui/research/daily", tags=["리서치"], summary="일일 리서치/에이전트 보고 목록")
def research_daily(limit: int = 50) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_agent_daily_reports(limit=limit)})


@app.get("/api/v1/ui/agent-opinions", tags=["에이전트"], summary="에이전트 의견 목록")
def agent_opinions(limit: int = 200, symbol: str | None = None, agent_name: str | None = None) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_agent_opinions(limit=limit, symbol=symbol, agent_name=agent_name)})


@app.get("/api/v1/ui/meetings", tags=["회의"], summary="회의 세션 목록")
def meetings(limit: int = 50) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_meeting_sessions(limit=limit)})


@app.get("/api/v1/ui/meetings/{meeting_id}", tags=["회의"], summary="회의 상세(메시지 포함)")
def meeting_detail(meeting_id: str, limit: int = 500) -> dict[str, Any]:
    repo = PostgresRepo()
    session = repo.fetch_meeting_session(meeting_id=meeting_id)
    messages = repo.fetch_meeting_messages(meeting_id=meeting_id, limit=limit)
    return ok({"session": session, "messages": messages})


@app.get("/api/v1/ui/strategy-reviews", tags=["거버넌스"], summary="전략 리뷰(주간 우선순위) 목록")
def strategy_reviews(limit: int = 20) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_strategy_reviews(limit=limit)})


@app.get("/api/v1/ui/review/weekly", tags=["리뷰"], summary="주간 리뷰 데이터(PnL/실현거래)")
def weekly_review() -> dict[str, Any]:
    repo = PostgresRepo()
    pnl = repo.fetch_pnl_daily(limit=14)
    trades = repo.fetch_realized_trades(limit=200)
    return ok({"pnl_daily": pnl, "realized_trades": trades})
