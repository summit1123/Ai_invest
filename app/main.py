from __future__ import annotations

import json
import os
import queue
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from fastapi import FastAPI, Query
from starlette.responses import StreamingResponse

from ai_invest.config.dotenv import load_dotenv
from ai_invest.config.rules_loader import load_rules
from ai_invest.meetings.governance_meeting import run_governance_meeting_now
from ai_invest.notifications.service import NotificationService
from ai_invest.storage.postgres import PostgresRepo
from ai_invest.work.agent_work_loop import collect_latest_work_reports


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
def latest_decisions(
    limit: int = Query(20, ge=1, le=500, description="조회할 최대 개수"),
) -> dict[str, Any]:
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
def timeline(
    limit: int = Query(200, ge=1, le=2000, description="조회할 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_recent_events(limit=limit)})


@app.get("/api/v1/ui/execution-quality", tags=["실행/비용"], summary="실행 품질(TCA-lite) 조회")
def execution_quality(
    limit: int = Query(200, ge=1, le=2000, description="조회할 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_execution_metrics(limit=limit)})


@app.get("/api/v1/ui/reconciliation-status", tags=["운영"], summary="정합성 체크 목록")
def reconciliation_status(
    limit: int = Query(200, ge=1, le=2000, description="조회할 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_reconciliation_checks(limit=limit)})


@app.get("/api/v1/ui/pause-log", tags=["운영"], summary="PAUSE 로그")
def pause_log(
    limit: int = Query(200, ge=1, le=2000, description="조회할 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_pause_logs(limit=limit)})


@app.get("/api/v1/ui/notifications-delivery", tags=["알림"], summary="알림 전송 이력")
def notifications_delivery(
    limit: int = Query(200, ge=1, le=2000, description="조회할 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_notification_deliveries(limit=limit)})


@app.get("/api/v1/ui/ledger", tags=["정산"], summary="원장(ledger) 조회")
def ledger(
    limit: int = Query(200, ge=1, le=2000, description="조회할 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_ledger_entries(limit=limit)})


@app.get("/api/v1/ui/outcomes", tags=["학습/복기"], summary="Outcome(복기) 목록")
def outcomes(
    limit: int = Query(200, ge=1, le=2000, description="조회할 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_decision_outcomes(limit=limit)})


@app.get("/api/v1/ui/tax-exports", tags=["정산"], summary="Tax Export 실행 목록")
def tax_exports(
    limit: int = Query(50, ge=1, le=500, description="조회할 최대 개수"),
) -> dict[str, Any]:
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
def collaboration_rooms(
    limit: int = Query(200, ge=1, le=1000, description="조회할 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_communication_rooms(limit=limit)})


@app.get("/api/v1/ui/research/daily", tags=["리서치"], summary="일일 리서치/에이전트 보고 목록")
def research_daily(
    limit: int = Query(50, ge=1, le=500, description="조회할 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_agent_daily_reports(limit=limit)})


@app.get("/api/v1/ui/agent-opinions", tags=["에이전트"], summary="에이전트 의견 목록")
def agent_opinions(
    limit: int = Query(200, ge=1, le=2000, description="조회할 최대 개수"),
    symbol: str | None = Query(None, description="필터: 심볼 (예: KRW-BTC)"),
    agent_name: str | None = Query(None, description="필터: agent_name (예: market_agent)"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_agent_opinions(limit=limit, symbol=symbol, agent_name=agent_name)})


@app.get("/api/v1/ui/work-reports/latest", tags=["에이전트"], summary="사전업무 리포트 최신 상태")
def work_reports_latest(
    max_age_minutes: int | None = Query(None, ge=1, le=24 * 60, description="리포트 최대 허용 나이(분), 비우면 rules 사용"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    rules_raw = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))
    default_age = int(((rules_raw.get("governance") or {}).get("prework_max_age_min") or 360) if isinstance(rules_raw, Mapping) else 360)
    age = int(max_age_minutes if max_age_minutes is not None else default_age)
    data = collect_latest_work_reports(
        repo=repo,
        agent_names=["research_agent", "quant_strategist", "risk_manager", "ops_manager"],
        max_age_minutes=age,
    )
    return ok(data)


@app.get("/api/v1/ui/meetings", tags=["회의"], summary="회의 세션 목록")
def meetings(
    limit: int = Query(50, ge=1, le=500, description="조회할 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_meeting_sessions(limit=limit)})


@app.get("/api/v1/ui/meetings/{meeting_id}", tags=["회의"], summary="회의 상세(메시지 포함)")
def meeting_detail(
    meeting_id: str,
    limit: int = Query(500, ge=1, le=5000, description="회의 메시지 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    session = repo.fetch_meeting_session(meeting_id=meeting_id)
    messages = repo.fetch_meeting_messages(meeting_id=meeting_id, limit=limit)
    return ok({"session": session, "messages": messages})


@app.get("/api/v1/ui/strategy-reviews", tags=["거버넌스"], summary="전략 리뷰(주간 우선순위) 목록")
def strategy_reviews(
    limit: int = Query(20, ge=1, le=200, description="조회할 최대 개수"),
) -> dict[str, Any]:
    repo = PostgresRepo()
    return ok({"items": repo.fetch_strategy_reviews(limit=limit)})


@app.get("/api/v1/ui/trade-plan/latest", tags=["거버넌스"], summary="최신 Trade Plan(종목/비중)")
def latest_trade_plan() -> dict[str, Any]:
    repo = PostgresRepo()
    ev = repo.fetch_latest_event(event_type="TRADE_PLAN_SET")
    return ok({"event": ev})


@app.get("/api/v1/ui/meetings/governance/live", tags=["회의"], summary="거버넌스 회의 라이브 실행(SSE, 멀티 LLM)")
def meetings_governance_live(
    slot_key: str | None = Query(None, description="(선택) 강제 slot_key. 비우면 LIVE 슬롯으로 생성"),
) -> StreamingResponse:
    """Start a governance meeting and stream messages as Server-Sent Events (SSE)."""

    repo = PostgresRepo()
    notifier = NotificationService(repo)
    rules_raw = yaml.safe_load(Path("rules.yaml").read_text(encoding="utf-8"))

    q: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()

    def emit(event: str, data: Mapping[str, Any]) -> None:
        q.put((str(event), dict(data)))

    def worker() -> None:
        try:
            run_governance_meeting_now(
                repo=repo,
                notifier=notifier,
                rules_raw=rules_raw,
                force_slot_key=slot_key,
                emit=emit,
            )
        except Exception as exc:
            emit("run_error", {"error": str(exc)[:300]})
            emit("done", {"ok": False})

    threading.Thread(target=worker, daemon=True).start()

    def gen() -> Iterable[bytes]:
        while True:
            event, data = q.get()
            payload = json.dumps(data, ensure_ascii=False, default=str)
            msg = f"event: {event}\ndata: {payload}\n\n"
            yield msg.encode("utf-8")
            if event == "done":
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/v1/ui/review/weekly", tags=["리뷰"], summary="주간 리뷰 데이터(PnL/실현거래)")
def weekly_review() -> dict[str, Any]:
    repo = PostgresRepo()
    pnl = repo.fetch_pnl_daily(limit=14)
    trades = repo.fetch_realized_trades(limit=200)
    return ok({"pnl_daily": pnl, "realized_trades": trades})


@app.get("/api/v1/ui/orchestrator/status", tags=["운영"], summary="멀티 오케스트레이터 상태")
def orchestrator_status() -> dict[str, Any]:
    status_path = Path(os.environ.get("ORCHESTRATOR_STATUS_PATH", "runtime/orchestrator_status.json"))
    if not status_path.exists():
        return ok({"running": False, "status_file": str(status_path), "status": None})
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return ok({"running": False, "status_file": str(status_path), "status": None, "error": str(exc)})
    workers = status.get("workers") if isinstance(status, Mapping) else {}
    running = any(bool((v or {}).get("alive")) for v in (workers or {}).values()) if isinstance(workers, Mapping) else False
    return ok({"running": running, "status_file": str(status_path), "status": status})
