# AI 자동투자 운영/개발 가이드라인 (PnL-first, v1.1)

## 1. 시스템 원칙
- Fail Closed: 불확실/불일치 상황은 거래 중단
- Python Control: 최종 실행권은 Safe Judge
- AI Guardrail: LLM은 제안/요약/검색/shadow 판단 전용
- Traceable: `run_id`, `rule_version_id`, `event_id`로 추적

## 2. 리스크 가드레일
- 일손실 한도 도달 시 `PAUSE`
- `risk.veto=true` 신규 진입 금지
- 스프레드/슬리피지 임계 초과 시 HOLD/PAUSE
- 동시 익스포저 상한 강제

## 3. 자동 PAUSE 사유 타입
- `DATA_BAD`
- `RATE_LIMIT`
- `RECON_FAIL`
- `HIGH_VOL`
- `DAILY_LOSS`
- `MANUAL`

## 4. RESUME 정책
- 자동 재개는 제한적으로만 허용
- 기본 조건: 안정상태 연속 유지 + recon OK 연속 N회
- `RECON_FAIL`, `DAILY_LOSS`는 자동 재개 금지(수동 확인)

## 5. 정합성 규칙
- `events` append-only
- order/fill/position/balance 정합성 확인 필수
- recon FAIL 발생 시 `reconciliation_checks` 기록 + 즉시 PAUSE
- 종료된 결정/거래는 `decision_outcomes`에 원인 분류를 필수 기록

## 6. 재시작 복구 절차
1. open orders 재조회
2. fills 재동기화
3. balances 재조회
4. positions 재계산
5. recon OK 확인 후 RESUME

상세 장애 대응 절차는 `ops_runbook.md`를 따른다.

## 7. 룰 변경관리
- 룰 변경은 `rule_versions`만 사용
- LLM은 `RULE_PATCH` JSON 제안만 허용
- 반영 순서:
  1. `RULE_PROPOSAL`
  2. Python 검증
  3. 백테스트 + 리플레이
  4. `RULE_APPROVED` 후 활성화
- 라이브 즉시 반영 금지

초기 운영 기준:
- 시작 값은 `rules.yaml`을 기준으로 고정
- 변경은 `rule_versions` + 검증 통과 후에만 반영

## 8. Shadow 공정성 원칙
- AI Shadow는 실행권 없음
- Safe vs AI 비교는 동일 `shadow_policy`로만 수행
- 동일 benchmark/비용/체결 가정 불일치 시 비교 무효

## 9. Upbit 인증
- `Authorization: Bearer <JWT>`
- payload: `access_key`, `nonce`, 필요 시 `query_hash`
- `nonce` 재사용 금지
- Secret Key 원문 사용

## 10. 모니터링/알림
필수 지표(요약):
- 주문 실패율, 체결 지연
- slippage/spread(TCA-lite)
- recon FAIL 건수
- PAUSE/RESUME 빈도
- realized PnL, fees, MDD

필수 알림(요약):
- `PAUSE` 발생
- `RESUME` 발생
- recon FAIL
- 연속 주문 실패
- DB 쓰기 실패

기록 원칙:
- 알림 전송 결과(성공/실패/재시도)는 `notification_deliveries`에 필수 저장
- 상태 전이/운영/거버넌스/재무/GitOps 이벤트는 `events`에 필수 저장

알림 이벤트/템플릿/재시도/중복제거 상세는 `notifications_telegram.md`를 따른다.

## 11. 리더/재무 Agent 운영 원칙
- Strategy Coordinator:
  - 주간 우선순위 1건만 선정
  - 근거 데이터(`pnl_daily`, `execution_metrics`, `casebook_docs`) 기반 의사결정
  - 실행권 없음, 제안/조정만 수행
- Finance/Tax:
  - 월말/연말 산출물 생성 및 정합성 검증 수행
  - `TaxExportManifestV1`로 추적
  - 거래/전략 변경 권한 없음
- 공통:
  - 결과는 이벤트(`WEEKLY_PRIORITY_SET`, `TAX_EXPORT_*`)로 기록
  - Telegram/Slack 알림 연계 필수
  - 손실/미스 판단 결과는 `DECISION_OUTCOME_RECORDED` 이벤트로 기록

## 12. 문서 운영
- 스키마 변경: `database.md` 우선 반영
- 아키텍처 변경: `architecture.md` 동기화
- Agent 계약 변경: `agents.md` 동기화
- UI 매핑 변경: `react_design_plan.md` 동기화
- 알림 정책 변경: `notifications_telegram.md` 동기화
- 운영 절차 변경: `ops_runbook.md` 동기화
- 세금 산출 규격 변경: `tax_reporting.md` 동기화

## 13. GitHub 변경관리 가드레일
- 변경 자동화는 PR 생성/CI 실행까지 허용
- 기본 브랜치 직접 푸시 금지
- 사용자 승인 전 자동 머지 금지
- 필수 체크:
  - 테스트 통과
  - 보안/비밀정보 스캔 통과
  - 롤백 계획 첨부
  - 문서 동기화 확인

상세 워크플로우와 이벤트 규격은 `engineering_change_management.md`를 따른다.

## 14. 실행 계약 문서
- 초기 룰/리스크/스프레드/손절: `rules.yaml`
- 판단/실패/회고 코드 사전: `reason_codes.md`
- 주문 상태 전이 규칙: `order_state_machine.md`
- 초기값 설계 근거/조정 규칙: `trading_contract_rationale.md`
