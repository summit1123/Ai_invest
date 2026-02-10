# reason_codes.md - Reason Code 표준 사전 (v1.0)

> 목적: 판단/차단/실패/회고 원인을 코드로 고정해 재현 가능한 회고와 자동 집계를 가능하게 한다.
> 적용 범위: `decisions.selected_reasons`, `decisions.rejected_reasons`, `events.payload.reason_code`, `decision_outcomes.error_type`

---

## 1. 네이밍 규칙

- 형식: `DOMAIN_DETAIL` (대문자 + `_`)
- 의미:
  - `RG_*`: Safe Judge 게이트/리스크 판단
  - `EX_*`: 주문/체결/실행 실패
  - `OC_*`: 사후 회고(오판/원인)
  - `OP_*`: 운영/인프라 상태

---

## 2. Safe Judge 코드 (`RG_*`)

| 코드 | 기본 액션 | 설명 |
|---|---|---|
| `RG_RECON_FAIL` | `PAUSE` | 주문/체결/잔고 정합성 실패 |
| `RG_DAILY_LOSS_LIMIT_HIT` | `PAUSE` | 일손실 제한 도달 |
| `RG_DATA_BAD` | `PAUSE` | 결측/중복/지연 등 데이터 품질 불량 |
| `RG_RATE_LIMIT_STORM` | `PAUSE` | API 429 급증 |
| `RG_WS_UNSTABLE` | `PAUSE` | WS 재연결 과다 |
| `RG_RISK_VETO` | `HOLD` | Risk Agent veto |
| `RG_REGIME_BLOCKED` | `HOLD` | 허용되지 않은 레짐 |
| `RG_SPREAD_TOO_WIDE` | `HOLD` | 진입 시점 스프레드 과다 |
| `RG_SLIPPAGE_EST_TOO_HIGH` | `HOLD` | 예상 슬리피지 초과 |
| `RG_EDGE_TOO_LOW` | `HOLD` | 비용 반영 후 기대엣지 부족 |
| `RG_EXPOSURE_LIMIT` | `HOLD` | 익스포저 상한 초과 |
| `RG_MIN_ORDER_NOT_MET` | `HOLD` | 최소 주문금액 미충족 |
| `RG_COOLDOWN_ACTIVE` | `HOLD` | 연속 손실 쿨다운 구간 |
| `RG_SIGNAL_CONFLICT` | `HOLD` | 핵심 Agent 신호 충돌 |
| `RG_PASS` | `BUY/SELL` | 게이트 통과 |

---

## 3. Execution 코드 (`EX_*`)

| 코드 | 기본 액션 | 설명 |
|---|---|---|
| `EX_ORDER_SUBMIT_FAIL` | `RETRY/PAUSE` | 주문 제출 실패 |
| `EX_ORDER_REJECTED` | `HOLD` | 거래소 주문 거부 |
| `EX_ACK_TIMEOUT` | `CANCEL/RETRY` | ACK 수신 지연 |
| `EX_PARTIAL_FILL_TIMEOUT` | `CANCEL_REST` | 부분체결 후 잔량 장기 미체결 |
| `EX_CANCEL_FAILED` | `PAUSE` | 취소 실패/상태 불일치 |
| `EX_REPRICE_LIMIT_REACHED` | `HOLD` | 재호가 횟수 초과 |
| `EX_TICK_SIZE_INVALID` | `HOLD` | 호가단위 위반 |
| `EX_INSUFFICIENT_BALANCE` | `HOLD` | 잔고 부족 |
| `EX_INVALID_STATE_TRANSITION` | `PAUSE` | 상태머신 불법 전이 감지 |

---

## 4. Outcome 코드 (`OC_*`)

| 코드 | 설명 |
|---|---|
| `OC_FALSE_BREAKOUT` | 돌파 신호 후 즉시 되돌림 |
| `OC_REGIME_MISCLASSIFIED` | 레짐 판정 오류 |
| `OC_COST_UNDERESTIMATED` | 비용(스프레드/슬리피지) 과소 추정 |
| `OC_STOP_TOO_TIGHT` | 손절폭이 너무 좁아 노이즈 손절 |
| `OC_STOP_TOO_LOOSE` | 손절폭이 과도해 손실 확대 |
| `OC_LATE_ENTRY` | 신호 대비 진입 지연 |
| `OC_EARLY_EXIT` | 추세 지속 구간 조기 청산 |
| `OC_LIQUIDITY_DROPOUT` | 유동성 급감 구간에서 체결 품질 악화 |
| `OC_NEWS_SHOCK` | 돌발 이벤트 충격 |
| `OC_SIGNAL_OVERFIT` | 특정 구간 과적합 신호 |
| `OC_EXECUTION_LATENCY` | 실행 지연으로 엣지 소실 |
| `OC_RULE_DRIFT` | 룰 변경 후 분포 변화 미반영 |

---

## 5. 운영 코드 (`OP_*`)

| 코드 | 설명 |
|---|---|
| `OP_PAUSE_TRIGGERED` | PAUSE 전환 |
| `OP_RESUME_COMPLETED` | RESUME 완료 |
| `OP_RESTART_RECOVERY` | 재시작 복구 시퀀스 수행 |
| `OP_MANUAL_REVIEW_REQUIRED` | 수동 검토 필요 |

---

## 6. 저장 규칙

1. `decisions.selected_reasons`와 `decisions.rejected_reasons`에는 문자열 코드만 저장
2. 사용자 표시용 문구는 코드 -> 템플릿 매핑으로 렌더링
3. `decision_outcomes.error_type`은 `OC_*` 우선 사용
4. 다중 원인 시 최대 3개 코드까지 저장

---

## 7. 운영 규칙

1. `RG_*` 코드가 `PAUSE` 성격이면 Telegram `CRITICAL` 우선 전송
2. `EX_*`는 실패 유형별 집계(5분 단위)와 함께 전송
3. `OC_*`는 주간 리뷰 집계에 포함하고 룰 패치 후보 입력으로 사용
