# AI 자동투자 Agents 상세 설계서 (PnL-first, v1.1)

## 1. 목적
- Agent는 의견을 생성하고, 실행은 Safe Judge가 담당한다.
- v1.1에서는 **Execution/Recon/Cost 정보**를 Agent 판단에 포함한다.

## 2. 공통 원칙
- Agent는 주문 직접 실행 금지
- JSON 스키마 검증 후 저장
- `events(AGENT_OPINION)` + `agent_opinions` 동시 추적
- 실패/타임아웃도 기록
- 판단 근거는 자유문장 대신 `reason_code`(표준 코드) 우선 사용

## 3. 공통 입력 계약
```json
{
  "run_id": "uuid",
  "rule_version_id": "uuid",
  "decision_id": "uuid",
  "timestamp_utc": "2026-02-06T08:30:00Z",
  "symbol": "KRW-BTC",
  "snapshot": {
    "last_price": 43120500,
    "best_bid": 43120000,
    "best_ask": 43121000,
    "mid_price": 43120500,
    "spread_bps": 2.32
  },
  "features": {
    "atr_pct": 1.12,
    "rsi_14": 58.4,
    "vol_zscore": 1.7,
    "missing_rate_1m": 0.0
  },
  "ops": {
    "rate_limit_alert": false,
    "reconciliation_status": "OK",
    "pause_state": false
  },
  "context": {
    "account": {"daily_loss_pct": 0.4},
    "risk_limits": {"max_daily_loss_pct": 1.5, "max_slippage_bps": 8.0}
  }
}
```

## 4. Runtime Agents
### 4.1 Market Agent
- 출력: `signal`, `confidence`, `target_position_pct`, `reason[]`, `reason_codes[]`
- 비용 과다/스프레드 급등 시 HOLD 편향

### 4.2 Regime Agent
- 출력: `regime`, `trade_allowed`, `reason[]`, `reason_codes[]`
- `HIGH_VOL` + 유동성 악화 시 차단 권고

### 4.3 Risk Agent
- 출력: `veto`, `max_position_pct`, `max_loss_per_trade_pct`, `reason_codes[]`
- 일손실 임계 접근, 슬리피지 예상 초과 시 veto

### 4.4 Ops Agent (v1.1 강화)
- 출력: `system_state`, `veto`, `alerts[]`, `reconciliation_status`, `reason_codes[]`
- 입력 기준:
  - WS 재연결
  - REST 429 빈도
  - recon status
  - fill 지연
  - spread 급등
- `reconciliation_status=FAIL`이면 veto=true

### 4.5 Sentiment Agent (선택)
- 방향성 직접 제안 금지
- 리스크 상향 플래그만 제공

### 4.6 Strategy Coordinator Agent (CEO 역할, v1.1 핵심)
- 역할: 데이터 기반 주간 회의 주도, 개선 우선순위/실험 1건 선정
- 출력: `weekly_priority`, `hypothesis`, `owner`, `deadline`, `success_criteria`
- 입력 데이터:
  - `pnl_daily`, `realized_trades`, `execution_metrics`
  - `casebook_docs`, `rule_versions`, `reconciliation_checks`
- 권한: 거래/코드 직접 실행권 없음 (제안/조정만)

### 4.7 Finance/Tax Agent (v1.1 핵심)
- 역할: 세금/정산 산출물 검증, 원장 정합성 점검
- 출력: `tax_export_status`, `validation_report`, `discrepancy_alerts`, `manifest_ref`
- 입력 데이터:
  - `ledger_entries`, `realized_trades`, `fills`, `pnl_daily`
- 권한: 거래/전략/코드 변경 권한 없음

### 4.8 Research Agent (상시)
- 역할: 일일 시장 조사/이슈 정리, 전략팀/회의 입력 데이터 생산
- 출력: `daily_brief`, `key_findings`, `risk_watchlist`, `next_actions`
- 저장:
  - `agent_daily_reports`
  - `events(RESEARCH_DAILY_BRIEF)`
- 알림:
  - `tpl_research_daily_brief`를 `research-daily` 채널로 전송

## 5. Judge Agents
### 5.1 Safe Judge (실행권자)
저장:
- `decisions(judge_type=SAFE)`
- `events(SAFE_DECISION)`

하드 게이트:
- `regime.trade_allowed=false` -> HOLD
- `risk.veto=true` -> HOLD
- `ops.veto=true` -> PAUSE
- `ops.reconciliation_status=FAIL` -> PAUSE
- `spread_bps > spread_limit` -> HOLD/PAUSE
- 일손실 제한 도달 -> PAUSE

출력:
- `action(BUY/SELL/HOLD/PAUSE)`
- `score`, `confidence`
- `gates`, `selected_reasons`, `expected_cost_bps`, `expected_rr`

### 5.2 AI Judge (shadow)
저장:
- `decisions(judge_type=AI)`
- `events(AI_DECISION)`

규칙:
- 실행권 없음
- `shadow_policy`의 동일 비용/체결/벤치마크 조건으로만 평가

## 6. Learning Agents
### 6.1 Casebook Builder
- 손실/주요 거래를 카드화
- `casebook_docs` + embedding 저장

### 6.2 Rule Patch Proposer (LLM)
- 코드 직접 변경 금지
- `RULE_PATCH` JSON 제안만 허용
- `RULE_PROPOSAL` 이벤트 저장

### 6.3 Rule Validator (Python)
- 스키마/범위/금지경로 검증
- 백테스트/리플레이 통과 시 `RULE_APPROVED`

### 6.4 Outcome Evaluator (필수)
- 역할: 종료된 거래/의사결정의 정오 판단 및 원인 분류
- 출력: `outcome_label`, `error_type`, `root_cause`, `fix_hypothesis`
- 저장: `decision_outcomes` + `events(DECISION_OUTCOME_RECORDED)`
- 원인 코드는 `reason_codes.md`의 `OC_*`만 허용

## 7. 협업 프로토콜
### 7.1 실시간 의사결정 루프
1. Pre-check: Ops + Risk
2. Round 1 병렬: Market/Regime/Risk/Ops/Sentiment
3. Conflict detect
4. Round 2 follow-up
5. Final: Safe 결정 + AI shadow 저장

### 7.2 주간 개선 루프 (Strategy Coordinator 주도)
1. 주간 KPI/손실원인 집계
2. 개선 우선순위 1건 선정
3. `RULE_PATCH` 또는 운영개선 제안 생성
4. 백테스트/리플레이 기준 정의
5. 승인/실험/회고 결과 기록

### 7.3 월간 정산 루프 (Finance/Tax 주도)
1. 월말 산출물 생성
2. 정합성 검증(`ledger_entries` vs `realized_trades`)
3. `TaxExportManifestV1` 생성
4. 실패 시 경고/재실행, 성공 시 보관

### 7.4 오판 회고 루프 (Outcome Evaluator 주도)
1. 포지션 종료 이벤트 수집
2. `outcome_label`(WIN/LOSS/FLAT/MISS) 판정
3. `error_type`/`root_cause`/`fix_hypothesis` 기록
4. 주간 개선 루프 입력(`strategy_reviews`)으로 연결

### 7.5 일일 리서치/회의 루프 (Research + Coordinator)
1. Agent별 일일 보고 생성(`agent_daily_reports`)
2. 회의 세션 시작(`meeting_sessions`, `events(MEETING_STARTED)`)
3. 토론 메시지 저장(`meeting_messages`, `events(MEETING_MESSAGE)`)
4. 회의 종료 요약/액션아이템 확정(`events(MEETING_SUMMARY)`, `events(MEETING_ACTION_ASSIGNED)`)
5. Telegram/Slack 팀 채널로 자동 전송

## 8. 테스트 체크리스트
- 스키마 위반 저장 차단
- timeout fallback
- recon FAIL -> PAUSE 강제
- spread 급등 시 HOLD/PAUSE 테스트
- AI shadow 실행권 분리
- 동일 입력 재실행 일관성

## 9. MVP 우선순위
1. market/regime/risk/ops/safe_judge
2. execution + recon + pause
3. execution_metrics + market_quotes
4. ai_judge shadow + shadow_policy
5. casebook + rule patch
6. strategy coordinator 주간 개선 루프
7. finance/tax 월말 정산 루프

## 10. 알림 이벤트 연계
- Agent/Judge/Ops에서 발생한 핵심 이벤트는 `NotificationEventV1`로 매핑 가능해야 한다.
- 표준 매핑 예:
  - `SAFE_DECISION` -> `category=DECISION`
  - `ORDER_REJECTED` -> `category=ORDER`
  - `FILL` -> `category=FILL`
  - `PAUSE`, `RESUME`, `RECONCILIATION_FAIL` -> `category=OPS`
  - `AGENT_DAILY_REPORT`, `RESEARCH_DAILY_BRIEF` -> `category=RESEARCH`
  - `MEETING_SUMMARY`, `MEETING_ACTION_ASSIGNED` -> `category=MEETING`
  - `WEEKLY_PRIORITY_SET` -> `category=GOVERNANCE`
  - `TAX_EXPORT_COMPLETED`, `TAX_EXPORT_FAILED` -> `category=FINANCE`
- 상세 템플릿/재시도/중복제거 규칙은 `notifications_telegram.md`를 따른다.

## 11. 확장 지원 Agent (v1.2)
### 11.1 CTO Ops Agent
- 역할: 시스템 안정성 총괄(지연, recon, 장애 빈도, 운영 리스크)
- 출력: `system_health_score`, `incident_summary`, `risk_level`
- 권한: 거래/코드 변경 실행권 없음, 차단 권고만 가능

### 11.2 Service Engineering Agent (GitHub 자동화)
- 역할: `ChangeProposalV1` 기반으로 브랜치/PR/CI 자동화
- 권한:
  - feature branch 생성/PR 생성 가능
  - `main` 직접 푸시/무승인 머지 금지
- 상세 워크플로우: `engineering_change_management.md`

## 12. 계약 문서
- 초기 파라미터: `rules.yaml`
- 원인코드 표준: `reason_codes.md`
- 주문 전이 규칙: `order_state_machine.md`
