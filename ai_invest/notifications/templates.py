from __future__ import annotations

from typing import Any, Mapping

from ai_invest.domain.reason_codes_ko import format_reason_codes_ko


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_float(value: Any, *, digits: int = 2) -> str:
    try:
        if value is None:
            return "-"
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _as_int(value: Any) -> str:
    try:
        if value is None:
            return "-"
        return str(int(value))
    except Exception:
        return "-"


def _as_bool_ko(value: Any) -> str:
    if isinstance(value, bool):
        return "예" if value else "아니오"
    return "-"


def _clip(text: Any, max_len: int) -> str:
    s = str(text or "").strip()
    if len(s) <= max_len:
        return s
    return f"{s[:max_len].rstrip()} ..."


def _format_risks(risks: Any, *, limit: int = 8) -> str:
    if not isinstance(risks, list):
        return "- (없음)"
    lines: list[str] = []
    for r in risks[:limit]:
        s = str(r or "").strip()
        if s:
            lines.append(f"- {s}")
    return "\n".join(lines) if lines else "- (없음)"


def _operator_hint_for_action(action: str) -> str:
    a = str(action or "").upper()
    if a == "PAUSE":
        return "정합성/운영 상태를 확인하고 수동 재개 여부를 점검하세요."
    if a == "HOLD":
        return "신규 진입 없이 관찰 유지. 차단 사유 해소 시 다음 사이클에서 재평가됩니다."
    if a in {"BUY", "SELL"}:
        return "체결/슬리피지/수수료를 확인하세요."
    return "-"


def tpl_pause_critical(data: Mapping[str, Any]) -> str:
    return (
        "[운영][치명] 거래 중단 (PAUSE)\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 사유: {data.get('reason_type')}\n"
        f"- 심볼: {data.get('symbol')}\n"
        f"- 실행ID: {data.get('run_id')}\n"
        "- 운영자 확인: 정합성/지연/레이트리밋 상태를 확인하세요.\n"
    )


def tpl_recon_fail(data: Mapping[str, Any]) -> str:
    return (
        "[운영][치명] 정합성 실패 (RECON_FAIL)\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 심볼: {data.get('symbol')}\n"
        f"- 요약: {data.get('diff_summary')}\n"
        "- 운영자 확인: 원장/포지션/체결 정합성을 우선 점검하세요.\n"
    )


def tpl_safe_decision(data: Mapping[str, Any]) -> str:
    reasons = data.get("reasons") if isinstance(data.get("reasons"), list) else []
    ctx = _as_mapping(data.get("context"))
    action = str(data.get("action") or "-").upper()
    return (
        "[거래] Safe 결정\n"
        f"- 한 줄 요약: {data.get('symbol')} {action} ({format_reason_codes_ko(reasons)})\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 핵심지표: spread={_as_float(ctx.get('spread_bps'))}bps, rsi={_as_float(ctx.get('rsi_14'))}, atr={_as_float(ctx.get('atr_pct'))}%, vol_z={_as_float(ctx.get('vol_zscore'))}\n"
        f"- 게이트상태: regime_trade_allowed={_as_bool_ko(ctx.get('regime_trade_allowed'))}, risk_veto={_as_bool_ko(ctx.get('risk_veto'))}, ops_veto={_as_bool_ko(ctx.get('ops_veto'))}, recon={ctx.get('reconciliation_status')}\n"
        f"- 시장신호: market={ctx.get('market_signal')} ({_as_float(ctx.get('market_confidence'))})\n"
        f"- 적용플랜: slot={ctx.get('trade_plan_slot_key')}, target={_as_float(ctx.get('trade_plan_target_pct'))}%\n"
        f"- 자본정책: tier={ctx.get('capital_tier')}, target_cap={_as_float(ctx.get('capital_target_cap_pct'))}%\n"
        f"- 운영자 확인: {_operator_hint_for_action(action)}\n"
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


def tpl_finance_monthly_review(data: Mapping[str, Any]) -> str:
    alerts = data.get("discrepancy_alerts") if isinstance(data.get("discrepancy_alerts"), list) else []
    lines: list[str] = []
    for a in alerts[:5]:
        s = str(a or "").strip()
        if s:
            lines.append(f"- {s}")
    alerts_txt = "\n".join(lines) if lines else "- (없음)"
    return (
        "[정산][월말] Finance/Tax 리뷰\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 기간: {data.get('period_label')}\n"
        f"- tax_export_status: {data.get('tax_export_status')}\n"
        f"- manifest_ref: {data.get('manifest_ref')}\n"
        f"- LLM: {'사용' if data.get('llm_used') else '미사용'} ({data.get('llm_model')})\n"
        f"- 요약: {_clip(data.get('summary'), 220)}\n"
        f"- 불일치 경보:\n{alerts_txt}\n"
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
    headlines = data.get("headlines") if isinstance(data.get("headlines"), list) else []
    links: list[str] = []
    for h in headlines[:3]:
        if not isinstance(h, Mapping):
            continue
        title = str(h.get("title") or "").strip()
        url = str(h.get("url") or "").strip()
        if title or url:
            links.append(f"- {title} ({url})" if url else f"- {title}")
    links_txt = "\n".join(links) if links else "- (없음)"

    return (
        f"[리서치][일간] {data.get('brief_date')}\n"
        f"- 한 줄 요약: {_clip(data.get('summary'), 240)}\n"
        f"- 리스크:\n{_format_risks(data.get('risk_watchlist'))}\n"
        f"- 주요 링크:\n{links_txt}\n"
    )


def tpl_agent_daily_report(data: Mapping[str, Any]) -> str:
    return (
        f"[리서치][일간] 에이전트 보고 ({data.get('agent_name')})\n"
        f"- 보고일: {data.get('report_date')}\n"
        f"- 요약: {data.get('summary')}\n"
    )


def tpl_meeting_summary(data: Mapping[str, Any]) -> str:
    assistant_minutes = data.get("assistant_minutes")
    assistant_minutes = assistant_minutes if isinstance(assistant_minutes, str) and assistant_minutes.strip() else None
    assistant_meta = _as_mapping(data.get("assistant_meta"))
    used_llm = bool(assistant_meta.get("used_llm") or False)
    model = assistant_meta.get("model")
    trade_plan = _as_mapping(data.get("trade_plan"))

    source_line = "- 생성: deterministic"
    if used_llm:
        source_line = f"- 생성: LLM({model})" if model else "- 생성: LLM"

    plan_line = ""
    if trade_plan:
        plan_line = (
            f"- 최종 플랜: {trade_plan.get('symbol')} / target={_as_float(trade_plan.get('target_position_pct'))}%"
            f" / valid={trade_plan.get('valid_from_kst')}~{trade_plan.get('valid_to_kst')}\n"
        )

    body = assistant_minutes or str(data.get("summary") or "").strip() or "(요약 없음)"
    return (
        "[회의] 회의록\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- meeting_id: {data.get('meeting_id')}\n"
        f"{source_line}\n"
        f"- 한 줄 결론: {_clip(data.get('summary'), 180)}\n"
        + plan_line
        + "\n"
        + f"{_clip(body, 3200)}\n"
    )


def tpl_meeting_action_items(data: Mapping[str, Any]) -> str:
    items = data.get("items")
    lines: list[str] = []
    if isinstance(items, list):
        for it in items[:10]:
            if not isinstance(it, Mapping):
                continue
            owner = str(it.get("owner") or "").strip()
            action = str(it.get("action") or "").strip()
            due = str(it.get("due_date") or "").strip()
            if owner or action:
                lines.append(f"- {owner}: {action} (기한 {due or '-'})")
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


def tpl_trade_plan_set(data: Mapping[str, Any]) -> str:
    allowed = _as_mapping(data.get("allowed_actions"))
    constraints = _as_mapping(data.get("constraints"))
    activation_gate = _as_mapping(data.get("activation_gate"))
    activation_status = str(data.get("activation_status") or "-")
    decision = str(activation_gate.get("decision") or "-")
    decision_effective = str(activation_gate.get("decision_effective") or "-")
    hard_block = bool(activation_gate.get("hard_plan_block"))
    soft_block = bool(activation_gate.get("soft_plan_block"))
    hard_reasons = [str(x).strip() for x in list(activation_gate.get("hard_plan_block_reasons") or []) if str(x).strip()]
    soft_reasons = [str(x).strip() for x in list(activation_gate.get("soft_plan_block_reasons") or []) if str(x).strip()]
    if hard_block:
        exec_state = f"차단(HARD): {', '.join(hard_reasons[:3]) or '-'}"
    elif soft_block:
        exec_state = f"제한(SOFT): {', '.join(soft_reasons[:3]) or '-'} (Safe Judge 실시간 재평가)"
    else:
        exec_state = "가능(단, Safe Judge 실시간 게이트 적용)"
    return (
        "[거버넌스] 트레이드 플랜 확정\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 회의/슬롯: {data.get('meeting_id')} / {data.get('slot_key')}\n"
        f"- 심볼/목표비중: {data.get('symbol')} / {_as_float(data.get('target_position_pct'))}%\n"
        f"- 유효시간(KST): {data.get('valid_from_kst')} ~ {data.get('valid_to_kst')}\n"
        f"- 허용 액션: buy={_as_bool_ko(allowed.get('buy'))}, sell={_as_bool_ko(allowed.get('sell'))}\n"
        f"- 활성화 상태: {activation_status} (decision={decision}, effective={decision_effective})\n"
        f"- 런타임 실행 상태: {exec_state}\n"
        f"- 과매매 방지: cooldown={_as_int(data.get('cooldown_minutes'))}분, rebalance_band={_as_float(data.get('rebalance_band_pct'))}%\n"
        f"- 실행 제약: max_spread={_as_float(constraints.get('max_spread_bps'))}bps, max_slippage={_as_float(constraints.get('max_slippage_bps'))}bps, max_position={_as_float(constraints.get('max_position_pct'))}%\n"
        f"- 근거 요약: {_clip(data.get('rationale_summary'), 240)}\n"
        "- 운영자 확인: TTL 만료 전 회의 갱신 여부를 확인하세요.\n"
    )


def tpl_engineering_change_announced(data: Mapping[str, Any]) -> str:
    lines = data.get("summary_lines")
    summary: list[str] = []
    if isinstance(lines, list):
        for x in lines[:3]:
            s = str(x or "").strip()
            if s:
                summary.append(f"- {s}")
    summary_txt = "\n".join(summary) if summary else "- (요약 없음)"
    return (
        "[엔지니어링] 개선 반영 공지\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- change_id: {data.get('change_id')}\n"
        f"- 활성 모드: {data.get('activation_mode')}\n"
        f"- 변경 요약:\n{summary_txt}\n"
        f"- 롤백 안내: {data.get('rollback_hint')}\n"
    )


TEMPLATES = {
    "tpl_pause_critical": tpl_pause_critical,
    "tpl_recon_fail": tpl_recon_fail,
    "tpl_safe_decision": tpl_safe_decision,
    "tpl_fill_notice": tpl_fill_notice,
    "tpl_tax_export_done": tpl_tax_export_done,
    "tpl_tax_export_fail": tpl_tax_export_fail,
    "tpl_finance_monthly_review": tpl_finance_monthly_review,
    "tpl_order_rejected": tpl_order_rejected,
    "tpl_daily_review": tpl_daily_review,
    "tpl_weekly_review": tpl_weekly_review,
    "tpl_research_daily_brief": tpl_research_daily_brief,
    "tpl_agent_daily_report": tpl_agent_daily_report,
    "tpl_meeting_summary": tpl_meeting_summary,
    "tpl_meeting_action_items": tpl_meeting_action_items,
    "tpl_weekly_priority": tpl_weekly_priority,
    "tpl_trade_plan_set": tpl_trade_plan_set,
    "tpl_engineering_change_announced": tpl_engineering_change_announced,
}


def render(template_id: str, data: Mapping[str, Any]) -> str:
    fn = TEMPLATES.get(template_id)
    if not fn:
        return f"[알림] unknown template_id={template_id}\n{data}"
    return fn(data)
