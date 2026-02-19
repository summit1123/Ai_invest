from __future__ import annotations

from typing import Iterable


REASON_CODE_KO: dict[str, str] = {
    # RG_* (gate / safe judge)
    "RG_RECON_FAIL": "정합성 실패",
    "RG_DAILY_LOSS_LIMIT_HIT": "일 손실 제한 도달",
    "RG_DATA_BAD": "데이터 품질 문제",
    "RG_RATE_LIMIT_STORM": "레이트리밋(429) 급증",
    "RG_WS_UNSTABLE": "웹소켓 불안정",
    "RG_RISK_VETO": "리스크 차단",
    "RG_REGIME_BLOCKED": "레짐 차단",
    "RG_SPREAD_TOO_WIDE": "스프레드 과다",
    "RG_SLIPPAGE_EST_TOO_HIGH": "슬리피지 예상 초과",
    "RG_EDGE_TOO_LOW": "엣지 부족",
    "RG_EXPOSURE_LIMIT": "익스포저 상한",
    "RG_TRADE_PLAN_FLAT": "트레이드 플랜: 노출 0% 유지",
    "RG_TRADE_PLAN_TARGET_REACHED": "트레이드 플랜: 목표 비중 도달",
    "RG_MIN_ORDER_NOT_MET": "최소 주문금액 미충족",
    "RG_COOLDOWN_ACTIVE": "쿨다운 활성",
    "RG_SIGNAL_CONFLICT": "신호 충돌",
    "RG_CAP_PENDING": "조건부 활성화 평가 중",
    "RG_CAP_PROMOTED": "조건부 활성화로 PAPER 승격",
    "RG_CAP_BLOCKED": "조건부 활성화 하드게이트 차단",
    "RG_PASS": "게이트 통과",
    # EX_* (execution)
    "EX_ORDER_SUBMIT_FAIL": "주문 제출 실패",
    "EX_ORDER_REJECTED": "주문 거부",
    "EX_ACK_TIMEOUT": "주문 ACK 타임아웃",
    "EX_PARTIAL_FILL_TIMEOUT": "부분 체결 타임아웃",
    "EX_CANCEL_FAILED": "취소 실패/상태 불일치",
    "EX_REPRICE_LIMIT_REACHED": "재호가 한도 도달",
    "EX_TICK_SIZE_INVALID": "호가 단위 위반",
    "EX_INSUFFICIENT_BALANCE": "잔고 부족",
    "EX_INVALID_STATE_TRANSITION": "상태머신 불법 전이",
    # OP_* (ops)
    "OP_PAUSE_TRIGGERED": "PAUSE 상태",
    "OP_RESUME_COMPLETED": "RESUME 완료",
    "OP_RESTART_RECOVERY": "재시작 복구",
    "OP_MANUAL_REVIEW_REQUIRED": "수동 검토 필요",
    # OC_* (outcome)
    "OC_COST_UNDERESTIMATED": "비용 과소추정(수수료/스프레드/슬리피지)",
    "OC_FALSE_BREAKOUT": "가짜 돌파",
    "OC_REGIME_MISCLASSIFIED": "레짐 오분류",
    "OC_STOP_TOO_TIGHT": "손절폭 과도하게 타이트",
    "OC_STOP_TOO_LOOSE": "손절폭 과도하게 느슨",
    "OC_LATE_ENTRY": "늦은 진입",
    "OC_EARLY_EXIT": "너무 이른 청산",
    "OC_LIQUIDITY_DROPOUT": "유동성 급감",
    "OC_NEWS_SHOCK": "뉴스 급변",
    "OC_SIGNAL_OVERFIT": "신호 과최적화",
    "OC_EXECUTION_LATENCY": "실행 지연",
    "OC_RULE_DRIFT": "룰 드리프트",
}


def reason_title_ko(code: str) -> str:
    return REASON_CODE_KO.get(str(code), str(code))


def format_reason_codes_ko(codes: Iterable[str]) -> str:
    items = [str(c) for c in codes if c]
    if not items:
        return "없음"
    # Show Korean title first, keep code in parentheses for traceability.
    return ", ".join(f"{reason_title_ko(c)}({c})" for c in items)
