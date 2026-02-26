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


def _as_krw(value: Any) -> str:
    try:
        if value is None:
            return "-"
        return f"{float(value):,.0f} KRW"
    except Exception:
        return "-"


def _as_pct(value: Any, *, digits: int = 2) -> str:
    s = _as_float(value, digits=digits)
    return "-" if s == "-" else f"{s}%"


def _as_bps(value: Any, *, digits: int = 2) -> str:
    s = _as_float(value, digits=digits)
    return "-" if s == "-" else f"{s} bps"


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


def _join_nonempty(parts: list[str], *, sep: str = ", ", fallback: str = "-") -> str:
    vals = [str(x).strip() for x in parts if str(x).strip()]
    return sep.join(vals) if vals else fallback


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return bool(value)
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _human_trade_action_hint(*, action: str, buy: Any, sell: Any) -> str:
    a = str(action or "").upper()
    b = _to_bool(buy)
    s = _to_bool(sell)
    if a == "PAUSE":
        return "시스템 보호 모드입니다. 신규/청산 모두 대기하고 운영 상태를 먼저 확인합니다."
    if a == "HOLD":
        return "신규 진입은 보류합니다. 차단 사유가 풀리면 다음 사이클에서 자동 재평가합니다."
    if a == "BUY":
        return "진입 신호입니다. 체결가/수수료/슬리피지를 확인하세요."
    if a == "SELL":
        return "감축/청산 신호입니다. 보유 수량과 체결 내역을 확인하세요."
    if b is False and s is True:
        return "지금은 추가 매수 없이 기존 포지션 정리만 허용된 상태입니다."
    if b is False and s is False:
        return "지금은 매수/매도 모두 막힌 상태입니다."
    return "-"


def _reason_text(codes: Any) -> str:
    if not isinstance(codes, list):
        return "사유 정보가 없습니다."
    return format_reason_codes_ko([str(x) for x in list(codes or [])]) or "사유 정보가 없습니다."


def _market_and_gate_summary(ctx: Mapping[str, Any]) -> tuple[str, str]:
    spread = _as_bps(ctx.get("spread_bps"), digits=2)
    rsi = _as_float(ctx.get("rsi_14"), digits=1)
    atr = _as_pct(ctx.get("atr_pct"), digits=2)
    volz = _as_float(ctx.get("vol_zscore"), digits=2)
    market_line = f"시장 상태: 스프레드 {spread}, RSI {rsi}, ATR {atr}, 거래량 z-score {volz}"

    flags: list[str] = []
    if _to_bool(ctx.get("pause_state")) is True:
        flags.append("시스템 일시중지")
    if str(ctx.get("reconciliation_status") or "").upper() == "FAIL":
        flags.append("정합성 점검 실패")
    if _to_bool(ctx.get("ops_veto")) is True:
        flags.append("운영 게이트 차단")
    if _to_bool(ctx.get("risk_veto")) is True:
        flags.append("리스크 게이트 차단")
    if _to_bool(ctx.get("regime_trade_allowed")) is False:
        flags.append("시장 레짐 비허용")
    try:
        spread_now = float(ctx.get("spread_bps"))
        spread_lim = float(ctx.get("max_spread_bps_entry"))
        if spread_now > spread_lim:
            flags.append("스프레드 과다")
    except Exception:
        pass
    gate_line = "게이트 상태: " + (", ".join(flags) if flags else "차단 조건 없음")
    return market_line, gate_line


def _activation_mode_line(activation_status: str, activation_gate: Mapping[str, Any]) -> str:
    status = str(activation_status or "").strip().upper()
    decision = str(activation_gate.get("decision") or "").strip().upper()
    effective = str(activation_gate.get("decision_effective") or "").strip().upper()
    if status:
        if status == "ACTIVE":
            return "실행 모드: 현재 플랜이 활성 상태입니다."
        if status == "PAPER_ONLY":
            return "실행 모드: 페이퍼 실행 전용입니다. 실거래는 하지 않습니다."
        if status == "HOLD":
            return "실행 모드: 관망 상태입니다. 조건이 맞을 때만 제한적으로 진입합니다."
    if effective == "LIVE":
        return "실행 모드: LIVE 조건이 충족된 상태입니다."
    if effective == "PAPER":
        return "실행 모드: PAPER 조건으로 운영 중입니다."
    if effective == "HOLD" or decision == "HOLD":
        return "실행 모드: HOLD(관망) 상태입니다."
    return "실행 모드: 정책 게이트 기준으로 실시간 평가 중입니다."


def _plain_exec_state(activation_gate: Mapping[str, Any]) -> tuple[str, str]:
    reason_code = str(activation_gate.get("reason_code") or "").strip().upper()
    hard_block = bool(activation_gate.get("hard_plan_block"))
    soft_block = bool(activation_gate.get("soft_plan_block"))
    hard_reasons = [str(x).strip().upper() for x in list(activation_gate.get("hard_plan_block_reasons") or []) if str(x).strip()]
    soft_reasons = [str(x).strip().upper() for x in list(activation_gate.get("soft_plan_block_reasons") or []) if str(x).strip()]

    if hard_block:
        why = _reason_text(hard_reasons[:3])
        return "실행 차단", why if why != "사유 정보가 없습니다." else "핵심 하드 게이트를 통과하지 못했습니다."
    if soft_block:
        why = _reason_text(soft_reasons[:3])
        if why == "사유 정보가 없습니다.":
            why = "보수 정책으로 제한 중입니다."
        return "조건부 제한", f"{why} (다음 판단 주기에서 재평가)"
    if reason_code:
        return "실행 가능", _reason_text([reason_code])
    return "실행 가능", "실시간 Safe Judge 게이트 적용"


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
        "[운영 경보] 자동매매 일시중지\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 중지 사유: {data.get('reason_type')}\n"
        f"- 관련 종목: {data.get('symbol')}\n"
        f"- 실행 ID: {data.get('run_id')}\n"
        "- 조치 안내: 정합성/네트워크/레이트리밋 상태를 먼저 확인해 주세요.\n"
    )


def tpl_recon_fail(data: Mapping[str, Any]) -> str:
    return (
        "[운영 경보] 정합성 점검 실패\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 종목: {data.get('symbol')}\n"
        f"- 상세 요약: {data.get('diff_summary')}\n"
        "- 조치 안내: 원장/보유수량/체결내역 일치 여부를 우선 확인해 주세요.\n"
    )


def tpl_safe_decision(data: Mapping[str, Any]) -> str:
    reasons = data.get("reasons") if isinstance(data.get("reasons"), list) else []
    ctx = _as_mapping(data.get("context"))
    action = str(data.get("action") or "-").upper()
    simple_hint = _human_trade_action_hint(
        action=action,
        buy=ctx.get("trade_plan_buy_allowed"),
        sell=ctx.get("trade_plan_sell_allowed"),
    )
    market_line, gate_line = _market_and_gate_summary(ctx)
    reason_line = _reason_text(reasons)
    signal = str(ctx.get("market_signal") or "-").strip().upper()
    signal_conf = _as_float(ctx.get("market_confidence"), digits=2)
    target = _as_pct(ctx.get("trade_plan_target_pct"), digits=2)
    tier = str(ctx.get("capital_tier") or "-")
    cap = _as_pct(ctx.get("capital_target_cap_pct"), digits=2)
    return (
        "[거래 판단 보고]\n"
        f"- 결론: {data.get('symbol')} → {action}\n"
        f"- 이유: {reason_line}\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- {market_line}\n"
        f"- {gate_line}\n"
        f"- 현재 시장 신호: {signal} (신뢰도 {signal_conf})\n"
        f"- 이번 슬롯 목표 비중: {target} (자본 티어 {tier}, 상한 {cap})\n"
        f"- 실행 의미: {simple_hint}\n"
        f"- 운영 안내: {_operator_hint_for_action(action)}\n"
    )


def tpl_fill_notice(data: Mapping[str, Any]) -> str:
    side = str(data.get("side") or "").upper()
    side_ko = "매수" if side in {"BUY", "BID"} else ("매도" if side in {"SELL", "ASK"} else side)
    return (
        "[체결 보고]\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 종목: {data.get('symbol')}\n"
        f"- 거래 유형: {side_ko}\n"
        f"- 체결 수량: {_as_float(data.get('qty'), digits=8)}\n"
        f"- 체결 가격: {_as_float(data.get('price'), digits=0)}\n"
        f"- 수수료: {_as_float(data.get('fee'), digits=4)} {data.get('fee_currency')}\n"
    )


def tpl_tax_export_done(data: Mapping[str, Any]) -> str:
    return (
        "[정산 보고] 월말 세금 산출 완료\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 기간: {data.get('period_label')}\n"
        f"- 산출 ID: {data.get('export_id')}\n"
    )


def tpl_tax_export_fail(data: Mapping[str, Any]) -> str:
    return (
        "[정산 경보] 월말 세금 산출 실패\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 기간: {data.get('period_label')}\n"
        f"- 산출 ID: {data.get('export_id')}\n"
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
        "[정산 리뷰] 월말 검증 결과\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 기간: {data.get('period_label')}\n"
        f"- 산출 상태: {data.get('tax_export_status')}\n"
        f"- 리포트 참조: {data.get('manifest_ref')}\n"
        f"- LLM: {'사용' if data.get('llm_used') else '미사용'} ({data.get('llm_model')})\n"
        f"- 핵심 요약: {_clip(data.get('summary'), 220)}\n"
        f"- 불일치/경보:\n{alerts_txt}\n"
    )


def tpl_order_rejected(data: Mapping[str, Any]) -> str:
    return (
        "[주문 경보] 주문이 거부되었습니다\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 종목: {data.get('symbol')}\n"
        f"- 주문 ID: {data.get('order_id')}\n"
        f"- 거부 사유: {data.get('reject_reason')}\n"
    )


def tpl_daily_review(data: Mapping[str, Any]) -> str:
    realized = _as_krw(data.get("realized_pnl"))
    fees = _as_krw(data.get("fees_paid"))
    mdd = _as_krw(data.get("max_drawdown"))
    improvement_title = str(data.get("improvement_title") or "").strip()
    improvement_reason = str(data.get("improvement_reason") or "").strip()
    suggested_changes = data.get("suggested_changes") if isinstance(data.get("suggested_changes"), list) else []
    change_lines: list[str] = []
    for row in suggested_changes[:5]:
        s = str(row or "").strip()
        if s:
            change_lines.append(f"- {s}")
    changes_text = "\n".join(change_lines) if change_lines else "- (없음)"
    recommendation_block = ""
    if improvement_title or improvement_reason or change_lines:
        recommendation_block = (
            f"- 오늘 수정 우선순위: {improvement_title or '-'}\n"
            f"- 판단 근거: {improvement_reason or '-'}\n"
            f"- 권장 수정 항목:\n{changes_text}\n"
        )
    return (
        f"[일간 리뷰] {data.get('day')}\n"
        f"- 오늘 실현손익: {realized}\n"
        f"- 오늘 수수료: {fees}\n"
        f"- 오늘 거래 횟수: {_as_int(data.get('trades_count'))}회\n"
        f"- 최대 낙폭: {mdd}\n"
        f"{recommendation_block}"
    )


def tpl_weekly_review(data: Mapping[str, Any]) -> str:
    return (
        f"[주간 리뷰] {data.get('week_label')}\n"
        f"- 주간 손익: {_as_krw(data.get('weekly_pnl'))}\n"
        f"- 승률: {_as_pct(data.get('win_rate'), digits=2)}\n"
        f"- 반복 손실 원인: {data.get('loss_tags_top3')}\n"
        f"- 자동 튜닝 상태: {data.get('rule_patch_status')}\n"
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
        f"[리서치 브리핑] {data.get('brief_date')}\n"
        f"- 오늘 요약: {_clip(data.get('summary'), 240)}\n"
        f"- 주의할 리스크:\n{_format_risks(data.get('risk_watchlist'))}\n"
        f"- 참고 기사:\n{links_txt}\n"
    )


def tpl_agent_daily_report(data: Mapping[str, Any]) -> str:
    return (
        f"[에이전트 보고] {data.get('agent_name')}\n"
        f"- 보고일: {data.get('report_date')}\n"
        f"- 내용 요약: {_clip(data.get('summary'), 260)}\n"
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
            f"- 최종 플랜: {trade_plan.get('symbol')} / 목표 비중 {_as_pct(trade_plan.get('target_position_pct'), digits=2)}"
            f" / valid={trade_plan.get('valid_from_kst')}~{trade_plan.get('valid_to_kst')}\n"
        )

    body = assistant_minutes or str(data.get("summary") or "").strip() or "(요약 없음)"
    action_hint = _human_trade_action_hint(
        action=str((_as_mapping(trade_plan.get("final_trade_plan")).get("action") or "")),
        buy=_as_mapping(trade_plan.get("allowed_actions")).get("buy"),
        sell=_as_mapping(trade_plan.get("allowed_actions")).get("sell"),
    )
    buy_flag = _as_mapping(trade_plan.get("allowed_actions")).get("buy")
    sell_flag = _as_mapping(trade_plan.get("allowed_actions")).get("sell")
    return (
        "[회의 결과 보고]\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 회의 ID: {data.get('meeting_id')}\n"
        f"{source_line}\n"
        f"- 한 줄 결론: {_clip(data.get('summary'), 180)}\n"
        + plan_line
        + f"- 실행 이해: {action_hint}\n"
        + f"- 이번 슬롯 허용: 매수 {_as_bool_ko(buy_flag)}, 매도 {_as_bool_ko(sell_flag)}\n"
        + "\n"
        + f"{_clip(body, 1800)}\n"
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
                lines.append(f"- 담당 {owner}: {action} (기한 {due or '-'})")
    items_txt = "\n".join(lines) if lines else "- (없음)"
    return (
        "[회의 후속 작업]\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 회의 ID: {data.get('meeting_id')}\n"
        f"{items_txt}\n"
    )


def tpl_weekly_priority(data: Mapping[str, Any]) -> str:
    return (
        "[주간 개선 과제]\n"
        f"- 주차: {data.get('week_label')}\n"
        f"- 이번 주 핵심 과제: {data.get('priority_title')}\n"
        f"- 검증 가설: {data.get('hypothesis')}\n"
        f"- 담당: {data.get('owner')}\n"
    )


def tpl_trade_plan_set(data: Mapping[str, Any]) -> str:
    allowed = _as_mapping(data.get("allowed_actions"))
    constraints = _as_mapping(data.get("constraints"))
    activation_gate = _as_mapping(data.get("activation_gate"))
    activation_status = str(data.get("activation_status") or "-")
    exec_state_title, exec_state_detail = _plain_exec_state(activation_gate)
    mode_line = _activation_mode_line(activation_status, activation_gate)
    action_hint = _human_trade_action_hint(
        action=str(data.get("action") or ""),
        buy=allowed.get("buy"),
        sell=allowed.get("sell"),
    )
    return (
        "[거버넌스 플랜 보고]\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 회의/슬롯: {data.get('meeting_id')} / {data.get('slot_key')}\n"
        f"- 이번 슬롯 결론: {data.get('symbol')} 목표 {_as_pct(data.get('target_position_pct'), digits=2)}\n"
        f"- 실행 요약: {action_hint}\n"
        f"- 유효시간(KST): {data.get('valid_from_kst')} ~ {data.get('valid_to_kst')}\n"
        f"- 허용 동작: 매수 {_as_bool_ko(allowed.get('buy'))}, 매도 {_as_bool_ko(allowed.get('sell'))}\n"
        f"- {mode_line}\n"
        f"- 현재 실행 판정: {exec_state_title} / {exec_state_detail}\n"
        f"- 과매매 방지: 재진입 대기 {_as_int(data.get('cooldown_minutes'))}분, 리밸런싱 밴드 {_as_pct(data.get('rebalance_band_pct'), digits=2)}\n"
        f"- 실행 제약: 스프레드 {_as_bps(constraints.get('max_spread_bps'), digits=2)} 이하, 슬리피지 {_as_bps(constraints.get('max_slippage_bps'), digits=2)} 이하, 종목 비중 {_as_pct(constraints.get('max_position_pct'), digits=2)} 이하\n"
        f"- 근거 요약: {_clip(data.get('rationale_summary'), 240)}\n"
        "- 운영 안내: 유효시간이 끝나기 전에 다음 회의 플랜이 나오는지 확인해 주세요.\n"
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
        "[엔지니어링 공지] 개선사항 반영\n"
        f"- 시각(KST): {data.get('ts_kst')}\n"
        f"- 변경 ID: {data.get('change_id')}\n"
        f"- 적용 모드: {data.get('activation_mode')}\n"
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
