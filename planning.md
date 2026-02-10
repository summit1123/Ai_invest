# AI 자동투자 실행 계획 (Final, 5주 버전, 2026-02-09~2026-03-15)

## 1. 목적과 범위
- 목적: 수익을 내되, 큰 손실/운영사고를 먼저 막는 PnL-first 자동매매 플랫폼 구축
- 운영 모드: 24/7 실시간 감시 + 이벤트 기반 의사결정 + 일/주/월 회고
- 기본 채널: Telegram + Slack 병행
- 이 문서는 구현 순서/게이트/승격 기준을 고정한다.

포함:
- 실시간 거래 의사결정 루프(Agent -> Safe Judge -> Execution)
- 운영 안정성(recon, pause/resume, 알림, 감사 추적)
- 재무/세금 산출(원장, 실현손익, export/manifest)
- 주간 개선 루프(Strategy Coordinator), AI shadow 공정 비교

제외:
- Graph DB, 뉴스 확장(v1.2)
- 자동 무승인 머지/배포
- 수익 보장 약속

## 2. 문서 기준선 (현재 확정본)
- `architecture.md`: 시스템 흐름/컴포넌트 책임
- `agents.md`: Agent 계약/판단 책임/회고 루프
- `database.md`: DB-only 스키마 기준 원본
- `guidelines.md`: 운영/리스크/복구 정책
- `notifications_telegram.md`: 알림 규격/템플릿/재시도
- `ops_runbook.md`: 장애 대응 절차
- `tax_reporting.md`: 월말/연말 산출/검증 표준
- `engineering_change_management.md`: GitHub 변경관리 가드레일
- `react_design_plan.md`: 운영/복기 UI 설계
- `rules.yaml`: v1 초기 파라미터 고정값
- `reason_codes.md`: reason_code 표준 사전
- `order_state_machine.md`: 주문 전이 표준
- `trading_contract_rationale.md`: 초기값 근거/조정 규칙
- `fastapi_react_stack.md`: FastAPI/React 구현 기준
- `skills/fastapi-react-trading-platform/SKILL.md`: 풀스택 작업용 재사용 스킬
- `env_variables.md`: 환경변수 표준
- `vector_db_cloud_options.md`: 벡터DB 클라우드 선택 가이드

## 3. 사전 고정값 (Week 1 내 확정)
- 거래 범위: `KRW-BTC` 1심볼, 현물 Long-only
- 기본 모드: Paper trading
- 리스크 상한:
  - `max_risk_per_trade`
  - `max_daily_loss_pct`
  - `max_spread_bps`
- 실시간 손절 계산은 가격/비용 중심, 세금은 정산 레이어에서 분리
- `reason_code` 표준과 `decision_outcomes`(오판 원인 기록) 계약 확정

## 4. 5주 실행 로드맵

### Week 1 (2026-02-09 ~ 2026-02-15): 계약 동결 + DB 마이그레이션
산출물:
- 스키마 마이그레이션 초안(`events`~`notification_deliveries`~`change_proposals`)
- `rules.yaml` 초기값
- 주문 상태머신 전이표
- `reason_code`/`decision_outcomes` 명세

완료 기준(DoD):
- decision 1건 E2E 이벤트 체인 저장 확인
- 차단 사유가 `gates/selected_reasons/rejected_reasons`로 남음

### Week 2 (2026-02-16 ~ 2026-02-22): 코어 루프 구현
산출물:
- Data ingest(WS/REST) + feature/regime 파이프라인
- Market/Regime/Risk/Ops Agent + Safe Judge
- Paper OMS(`orders/fills`) 연동

완료 기준(DoD):
- `BUY/HOLD/PAUSE` 결정이 DB/이벤트에 누락 없이 기록
- Safe Judge 하드게이트 단위 테스트 통과

### Week 3 (2026-02-23 ~ 2026-03-01): 운영 하드닝
산출물:
- `reconciliation_checks`, `pause_log`, 복구 시퀀스
- `market_quotes`, `execution_metrics` 계산
- Telegram/Slack 실시간 알림 + `notification_deliveries` 저장

완료 기준(DoD):
- recon FAIL 주입 시 즉시 PAUSE
- PAUSE/RESUME 알림과 전송결과 저장 검증

### Week 4 (2026-03-02 ~ 2026-03-08): 재무/회고 루프 연결
산출물:
- `ledger_entries`, `realized_trades`, `tax_export_runs`
- `TaxExportManifestV1` 생성/체크섬
- `decision_outcomes` 기반 오판 원인 기록
- 주간 개선(`strategy_reviews`) 생성/알림

완료 기준(DoD):
- 월간 샘플 구간에서 손익/수수료 합계 검증 통과
- 손실 건 최소 1건에 대해 원인/가설/후속 액션 추적 가능

### Week 5 (2026-03-09 ~ 2026-03-15): 24/7 드라이런 + Go/No-Go
산출물:
- 7일 연속 Paper 24/7 드라이런
- 운영 장애 대응 리허설(재시작/복구)
- Safe vs AI shadow 비교 리포트

완료 기준(DoD):
- 주요 게이트 통과 시 소액 라이브 전환 후보
- 미통과 시 원인별 보완 백로그 생성

## 5. Go/No-Go 게이트 (실거래 전 필수)
- Gate A: 이벤트/원장/알림 전달로그 누락 0
- Gate B: recon FAIL -> PAUSE 100% 강제
- Gate C: 일손실 제한 100% 강제
- Gate D: 주문 상태머신 무한 루프/유실 없음
- Gate E: Shadow 비교는 동일 policy에서만 수행
- Gate F: 세금 산출물 체크섬 재현성 확보

## 6. KPI 기준 (Paper 단계)
- 성과: Profit Factor >= 1.10
- 리스크: Max Drawdown <= 5%, 일손실 제한 위반 0
- 집행: 평균 슬리피지/스프레드 기준치 이내
- 운영: recon FAIL 미복구 사고 0, 알림 누락 0
- 학습: 손실 태그 -> 주간 실험 액션 연결률 100%

## 7. 리스크와 대응
- 과도한 범위 확장:
  - 대응: v1.2(뉴스/Graph/다채널)는 고정 보류
- AI 과신:
  - 대응: Safe Judge 실행권 고정, AI는 shadow 중심
- 운영 복잡성:
  - 대응: Runbook 자동 점검 항목 우선 구현
- 회고 품질 저하:
  - 대응: `decision_outcomes`를 필수 데이터로 강제

## 8. 즉시 실행 액션 (이번 주)
1. `database.md` 기준 SQL migration 파일 작성
2. `reason_code` enum/표준 사전 작성
3. `decision_outcomes` 테이블 + 이벤트 매핑 확정
4. Safe Judge 하드게이트 테스트 케이스 작성
5. Telegram/Slack Critical/High 알림 E2E 검증

## 8.1 개발 루프 (커밋 단위 실행)
- 목적: 작업 케이스를 세밀하게 쪼개고, `개발 -> 테스트 -> 검증 -> 커밋 -> 푸시`를 반복
- 스크립트:
  - `scripts/dev_cycle.sh`: 테스트 + 스모크 + git status
  - `scripts/commit_push.sh "msg"`: add/commit/push
- 운영 방식:
  - 작은 변경 단위로 커밋 메시지 고정
  - 실패 시 다음 커밋으로 넘어가지 않고 원인 수정 후 재시도

## 9. 최종 판정
- 현재 상태: 설계 문서는 개발 착수 가능한 수준
- 목표 시점: 2026-03-15까지 Paper 운영 안정화 완료
- 라이브 전환: Go 게이트 통과 시에만 제한적으로 시작
