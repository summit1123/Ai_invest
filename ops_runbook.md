# ops_runbook.md - 운영 Runbook (PAUSE/RESUME/장애대응, v1.1)

> 목적: 거래 중단/복구 절차를 표준화해 운영 사고 시 손실 확산을 막는다.  
> 범위: 장애 감지, PAUSE 사유별 대응, 재시작 절차, 수동 승인 규칙, 알림 연계.

관련 문서:
- 알림 규격: `notifications_telegram.md`
- 운영 정책: `guidelines.md`
- DB 스키마: `database.md`

---

## 1. 운영 상태 모델

- `ACTIVE`: 정상 거래 가능
- `PAUSED`: 신규 거래 중단
- `RECOVERY`: 복구 검증 단계
- `MANUAL_REVIEW`: 수동 승인 대기

상태 전이 원칙:
- 위험 신호는 Fail Closed로 즉시 `PAUSED`
- 자동 재개는 제한적으로 허용

---

## 2. PAUSE 사유별 대응표

| reason_type | 초기 조치 | 자동 재개 | 수동 승인 | 최대 허용 중단 |
|---|---|---|---|---|
| `DATA_BAD` | WS/REST 헬스체크, 결측/중복 점검 | 가능 | 선택 | 10분 |
| `RATE_LIMIT` | 호출 감속, 백오프, 큐 정리 | 가능 | 선택 | 15분 |
| `RECON_FAIL` | 주문/체결/잔고 전체 재동기화 | 불가 | 필수 | 제한 없음 |
| `HIGH_VOL` | 신규 진입 차단, 리스크 축소 | 가능 | 선택 | 30분 |
| `DAILY_LOSS` | 당일 거래 중단 | 불가(당일) | 필수(익일) | 익일 개장 전 |
| `MANUAL` | 운영자 판단 | 불가 | 필수 | 운영자 결정 |

---

## 3. 재시작/복구 절차

### 3.1 표준 복구 시퀀스

1. `open orders` 거래소 조회
2. `fills` 재동기화
3. `balances` 재조회
4. `positions` 재계산
5. `reconciliation_checks` 실행
6. `OK` 연속 N회 충족 시 `RESUME`

권장 기준:
- `N = 3`
- 샘플링 간격: 20초

### 3.2 복구 실패 시

- `action_taken = MANUAL_REVIEW`
- 상태를 `MANUAL_REVIEW`로 전환
- 원인/증적 첨부 후 운영자 승인 대기

---

## 4. 수동 승인 필수 케이스

- `RECON_FAIL` 발생 건
- `DAILY_LOSS` 도달 건
- 1시간 내 `RATE_LIMIT` 재발 3회 이상
- 동일 심볼 `ORDER_REJECTED` 5회 이상 누적

승인 기록:
- 승인자, 시각, 승인 사유, 변경 파라미터, 재개 조건

---

## 5. 알림 연계 (Telegram + Slack)

| Runbook 단계 | 템플릿 ID | 전송 조건 |
|---|---|---|
| PAUSE 진입 | `tpl_pause_critical` | 즉시 |
| RECON_FAIL 감지 | `tpl_recon_fail` | 즉시 |
| 주문 거부 반복 | `tpl_order_rejected` | 즉시/집계 |
| 복구 성공(RESUME) | `tpl_resume_notice` | 즉시 |
| 일일 운영 요약 | `tpl_daily_review` | 23:10 KST |

`tpl_resume_notice`는 운영 편의를 위한 확장 템플릿이며 상세 포맷은 `notifications_telegram.md` 정책에 맞춘다.

---

## 6. 운영 체크리스트

### 6.0 런타임 상태 확인

1. 오케스트레이터 상태 파일: `runtime/orchestrator_status.json`
2. API 상태 조회: `GET /api/v1/ui/orchestrator/status`
3. worker 상태(`paper_loop/work_loop/governance_loop/review_loop`)가 `alive=true`인지 확인
4. `restarts`가 급증하면 해당 worker 로그/원인 점검

### 6.1 장 시작 전

1. WS 연결 상태 확인
2. API key/nonce 정상 확인
3. 전일 미해결 주문/포지션 확인
4. 전일 `RECON_FAIL` 원인 종료 확인
5. Telegram/Slack 알림 테스트 1회

### 6.2 장중

1. 주문 실패율 모니터링
2. 체결 지연/슬리피지 추이 확인
3. `reconciliation_checks` FAIL 여부 확인
4. PAUSE 상태 전환 발생 시 즉시 원인 분석

### 6.3 장 종료 후

1. `pnl_daily` 정합성 확인
2. `ledger_entries` 누락 확인
3. Daily Review 전송 확인 (`reporting.daily_review_time_kst`, 기본 23:10 KST)
4. 장애 이벤트 RCA 메모(`AAR_NOTE`) 작성
5. 주간 종료일(SUN)에는 Weekly Review 전송 확인 (`reporting.weekly_review_time_kst`, 기본 21:00 KST)

---

## 7. 증적/감사 기록

필수 저장:
- 사고 시작/종료 시각
- 영향 범위(심볼, 주문 수, 손익 영향)
- 적용 조치와 결과
- 관련 이벤트 ID 목록

권장:
- 동일 유형 사고 재발 여부 태깅
- 다음 주 개선 액션 1건 연결

---

## 8. 테스트 시나리오

1. `RECON_FAIL` 주입 시 즉시 `PAUSED` 전환
2. 복구 시퀀스 성공 전에는 거래 재개 금지
3. `DAILY_LOSS` 당일 자동 재개 금지 검증
4. PAUSE/RESUME 전환 시 Telegram/Slack 알림 누락 없음
5. 수동 승인 기록 필드 누락 시 재개 거부
