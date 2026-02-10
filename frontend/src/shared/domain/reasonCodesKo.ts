export const REASON_CODE_KO: Record<string, { title: string; hint?: string }> = {
  RG_RECON_FAIL: { title: '정합성 실패', hint: 'reconciliation FAIL 감지' },
  RG_DAILY_LOSS_LIMIT_HIT: { title: '일손실 제한 도달' },
  RG_DATA_BAD: { title: '데이터 품질 불량' },
  RG_RATE_LIMIT_STORM: { title: '429 급증(레이트리밋)' },
  RG_WS_UNSTABLE: { title: 'WS 불안정' },
  RG_RISK_VETO: { title: '리스크 차단', hint: 'Risk Agent veto' },
  RG_REGIME_BLOCKED: { title: '레짐 차단', hint: 'trade_allowed=false' },
  RG_SPREAD_TOO_WIDE: { title: '스프레드 과다' },
  RG_SLIPPAGE_EST_TOO_HIGH: { title: '슬리피지 예상 초과' },
  RG_EDGE_TOO_LOW: { title: '엣지 부족' },
  RG_EXPOSURE_LIMIT: { title: '익스포저 상한' },
  RG_MIN_ORDER_NOT_MET: { title: '최소 주문금액 미충족' },
  RG_COOLDOWN_ACTIVE: { title: '쿨다운 활성' },
  RG_SIGNAL_CONFLICT: { title: '신호 충돌' },
  RG_PASS: { title: '게이트 통과' },

  EX_ORDER_SUBMIT_FAIL: { title: '주문 제출 실패' },
  EX_ORDER_REJECTED: { title: '주문 거부' },
  EX_ACK_TIMEOUT: { title: 'ACK 타임아웃' },
  EX_PARTIAL_FILL_TIMEOUT: { title: '부분체결 타임아웃' },
  EX_CANCEL_FAILED: { title: '취소 실패/상태 불일치' },
  EX_REPRICE_LIMIT_REACHED: { title: '재호가 한도 도달' },
  EX_TICK_SIZE_INVALID: { title: '호가단위 위반' },
  EX_INSUFFICIENT_BALANCE: { title: '잔고 부족' },
  EX_INVALID_STATE_TRANSITION: { title: '상태머신 불법 전이' },

  OP_PAUSE_TRIGGERED: { title: 'PAUSE 상태' },
  OP_RESUME_COMPLETED: { title: 'RESUME 완료' },
  OP_RESTART_RECOVERY: { title: '재시작 복구' },
  OP_MANUAL_REVIEW_REQUIRED: { title: '수동 검토 필요' },

  OC_COST_UNDERESTIMATED: { title: '비용 과소추정', hint: '수수료/스프레드/슬리피지' },
  OC_FALSE_BREAKOUT: { title: '가짜 돌파' },
  OC_REGIME_MISCLASSIFIED: { title: '레짐 오분류' },
  OC_EXECUTION_LATENCY: { title: '실행 지연' },
}

export function reasonTitleKo(code: string): string {
  return REASON_CODE_KO[code]?.title ?? code
}

