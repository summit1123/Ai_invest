from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from ai_invest.domain.reason_codes_ko import format_reason_codes_ko


def tpl_pause_critical(data: Mapping[str, Any]) -> str:
    return (
        "[운영][치명] 거래 중단 (PAUSE)\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 사유: {data.get('reason_type')}\n"
        f"- 심볼: {data.get('symbol')}\n"
        f"- 실행ID: {data.get('run_id')}\n"
    )


def tpl_recon_fail(data: Mapping[str, Any]) -> str:
    return (
        "[운영][치명] 정합성 실패 (RECON_FAIL)\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 심볼: {data.get('symbol')}\n"
        f"- 요약: {data.get('diff_summary')}\n"
    )


def tpl_safe_decision(data: Mapping[str, Any]) -> str:
    reasons = data.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    ctx = data.get("context") if isinstance(data.get("context"), Mapping) else {}
    return (
        "[거래] Safe 결정\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 심볼: {data.get('symbol')}\n"
        f"- 액션: {data.get('action')}\n"
        f"- 이유: {format_reason_codes_ko(reasons)}\n"
        f"- 핵심지표: spread_bps={ctx.get('spread_bps')}, rsi_14={ctx.get('rsi_14')}, atr_pct={ctx.get('atr_pct')}, vol_z={ctx.get('vol_zscore')}\n"
        f"- 에이전트: market={ctx.get('market_signal')}/{ctx.get('market_confidence')}, regime={ctx.get('regime')}/{ctx.get('regime_trade_allowed')}, risk_veto={ctx.get('risk_veto')}, ops={ctx.get('ops_state')}/{ctx.get('ops_veto')}\n"
    )


def tpl_fill_notice(data: Mapping[str, Any]) -> str:
    return (
        "[거래] 체결 알림\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 심볼: {data.get('symbol')}\n"
        f"- 매수/매도·수량: {data.get('side')}/{data.get('qty')}\n"
        f"- 체결가: {data.get('price')}\n"
        f"- 수수료: {data.get('fee')} {data.get('fee_currency')}\n"
    )


def tpl_tax_export_done(data: Mapping[str, Any]) -> str:
    return (
        "[정산] Tax Export 완료\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 기간: {data.get('period_label')}\n"
        f"- export_id: {data.get('export_id')}\n"
    )


def tpl_tax_export_fail(data: Mapping[str, Any]) -> str:
    return (
        "[정산][실패] Tax Export 실패\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 기간: {data.get('period_label')}\n"
        f"- export_id: {data.get('export_id')}\n"
        f"- 오류: {data.get('errors')}\n"
    )


def tpl_order_rejected(data: Mapping[str, Any]) -> str:
    return (
        "[거래][높음] 주문 거부\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 심볼: {data.get('symbol')}\n"
        f"- order_id: {data.get('order_id')}\n"
        f"- 사유: {data.get('reject_reason')}\n"
    )


def tpl_daily_review(data: Mapping[str, Any]) -> str:
    return (
        f"[리뷰][일간] {data.get('day')}\n"
        f"- 실현손익: {data.get('realized_pnl')}\n"
        f"- 수수료: {data.get('fees_paid')}\n"
        f"- 거래 수: {data.get('trades_count')}\n"
        f"- 최대 낙폭: {data.get('max_drawdown')}\n"
    )


def tpl_weekly_review(data: Mapping[str, Any]) -> str:
    return (
        f"[리뷰][주간] {data.get('week_label')}\n"
        f"- 주간 손익: {data.get('weekly_pnl')}\n"
        f"- 승률: {data.get('win_rate')}\n"
        f"- 손실 원인 Top3: {data.get('loss_tags_top3')}\n"
        f"- 룰 패치 상태: {data.get('rule_patch_status')}\n"
    )


def tpl_research_daily_brief(data: Mapping[str, Any]) -> str:
    risks = data.get("risk_watchlist")
    risk_lines: list[str] = []
    if isinstance(risks, list):
        for r in risks[:8]:
            s = str(r or "").strip()
            if s:
                risk_lines.append(f"- {s}")
    risks_txt = "\n".join(risk_lines) if risk_lines else "- (없음)"
    return (
        f"[리서치][일간] {data.get('brief_date')}\n"
        f"- 요약: {data.get('summary')}\n"
        f"- 리스크:\n{risks_txt}\n"
    )


def tpl_agent_daily_report(data: Mapping[str, Any]) -> str:
    return (
        f"[리서치][일간] 에이전트 보고 ({data.get('agent_name')})\n"
        f"- 보고일: {data.get('report_date')}\n"
        f"- 요약: {data.get('summary')}\n"
    )


def tpl_meeting_summary(data: Mapping[str, Any]) -> str:
    assistant_minutes = data.get("assistant_minutes")
    if not isinstance(assistant_minutes, str) or not assistant_minutes.strip():
        assistant_minutes = None
    assistant_meta = data.get("assistant_meta") if isinstance(data.get("assistant_meta"), Mapping) else {}
    used_llm = bool(assistant_meta.get("used_llm") or False)
    model = assistant_meta.get("model")

    body = assistant_minutes or str(data.get("summary") or "").strip()
    if not body:
        body = "(요약 없음)"
    return (
        "[회의] 회의록\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- meeting_id: {data.get('meeting_id')}\n"
        + (f"- 생성: LLM({model})\n" if used_llm and model else ("- 생성: deterministic\n" if not used_llm else ""))
        + "\n"
        + f"{body}\n"
    )


def tpl_meeting_action_items(data: Mapping[str, Any]) -> str:
    items = data.get("items")
    lines: list[str] = []
    if isinstance(items, list):
        for it in items[:10]:
            if not isinstance(it, Mapping):
                continue
            owner = str(it.get("owner") or "")
            action = str(it.get("action") or "")
            due = str(it.get("due_date") or "")
            if owner or action:
                lines.append(f"- {owner}: {action} (기한 {due})")
    items_txt = "\n".join(lines) if lines else "- (없음)"
    return (
        "[회의][액션아이템]\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- meeting_id: {data.get('meeting_id')}\n"
        f"{items_txt}\n"
    )


def tpl_weekly_priority(data: Mapping[str, Any]) -> str:
    return (
        "[거버넌스][주간] 개선 우선순위\n"
        f"- 주차: {data.get('week_label')}\n"
        f"- 우선순위: {data.get('priority_title')}\n"
        f"- 가설: {data.get('hypothesis')}\n"
        f"- 담당: {data.get('owner')}\n"
    )


TEMPLATES = {
    "tpl_pause_critical": tpl_pause_critical,
    "tpl_recon_fail": tpl_recon_fail,
    "tpl_safe_decision": tpl_safe_decision,
    "tpl_fill_notice": tpl_fill_notice,
    "tpl_tax_export_done": tpl_tax_export_done,
    "tpl_tax_export_fail": tpl_tax_export_fail,
    "tpl_order_rejected": tpl_order_rejected,
    "tpl_daily_review": tpl_daily_review,
    "tpl_weekly_review": tpl_weekly_review,
    "tpl_research_daily_brief": tpl_research_daily_brief,
    "tpl_agent_daily_report": tpl_agent_daily_report,
    "tpl_meeting_summary": tpl_meeting_summary,
    "tpl_meeting_action_items": tpl_meeting_action_items,
    "tpl_weekly_priority": tpl_weekly_priority,
}


def render(template_id: str, data: Mapping[str, Any]) -> str:
    fn = TEMPLATES.get(template_id)
    if not fn:
        return f"[알림] unknown template_id={template_id}\n{data}"
    return fn(data)
