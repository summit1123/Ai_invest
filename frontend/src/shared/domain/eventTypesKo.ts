export const EVENT_TYPE_KO: Record<string, string> = {
  MARKET_SNAPSHOT: '시장 스냅샷',
  FEATURE_SNAPSHOT: '특징 스냅샷',
  AGENT_OPINION: '에이전트 의견',
  SAFE_DECISION: 'Safe 결정',
  AI_DECISION: 'AI Shadow 결정',
  MEETING_STARTED: '회의 시작',
  MEETING_MESSAGE: '회의 메시지',
  MEETING_SUMMARY: '회의 요약',
  MEETING_ACTION_ASSIGNED: '회의 액션 할당',
  ORDER_SUBMITTED: '주문 제출',
  ORDER_ACK: '주문 ACK',
  ORDER_REJECTED: '주문 거부',
  FILL: '체결',
  RECONCILIATION_FAIL: '정합성 실패',
  PAUSE: '거래 중단(PAUSE)',
  RESUME: '거래 재개(RESUME)',
  DECISION_OUTCOME_RECORDED: '결과 기록',
  TAX_EXPORT_COMPLETED: '정산 산출 완료',
  TAX_EXPORT_FAILED: '정산 산출 실패',
  RESEARCH_DAILY_BRIEF: '리서치 일일 브리프',
}

export function eventTypeKo(t: string): string {
  return EVENT_TYPE_KO[t] ?? t
}
