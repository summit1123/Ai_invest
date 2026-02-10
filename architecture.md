# AI 자동투자 아키텍처 (PnL-first, v1.1 실전 하드닝)

## 1. 목적
- 최우선은 수익 극대화가 아니라 **생존(큰 손실 회피)**이다.
- 최종 실행권은 Python `Safe Judge`가 가진다.
- AI/LLM은 제안, 요약, 유사사례 검색, shadow 판단에 집중한다.

## 2. End-to-End 실행 구조
```text
Market Data (WS/REST)
  -> Normalize + Quality Check
  -> Feature/Regime
  -> Agent Opinions
  -> Research Daily Report / Agent Meeting Loop
  -> Judge Layer (Safe execution + AI shadow)
  -> Execution (OMS/EMS)
  -> Notification Router (Telegram + Slack)
  -> GitOps Change Manager (GitHub PR)
  -> Execution Quality (TCA-lite)
  -> Reconciliation + Pause/Resume
  -> Ledger/PnL
  -> Strategy Coordination (Weekly)
  -> Finance/Tax Reporting (Monthly)
  -> Replay/Backtest + Rule Versioning
```

## 3. 컴포넌트 책임
| 컴포넌트 | 책임 | 실패 시 동작 |
|---|---|---|
| Data Ingestor | Upbit WS/REST 수집, 정규화, 결측/중복 검출 | `PAUSE(reason=DATA_BAD)` |
| Quote/Orderbook Snapshotter | mid/spread/orderbook 스냅샷 저장 | 재시도 후 실패 누적 시 PAUSE 후보 |
| Feature Engine | 지표/레짐/비용 추정 피처 계산 | 해당 cycle HOLD |
| Agent Layer | market/regime/risk/ops/sentiment 의견 생성 | FAILED/TIMEOUT 기록 |
| Safe Judge | 하드 게이트 + 최종 action/size 결정 | HOLD/PAUSE |
| AI Judge | shadow 결정/설명 생성 | 실행권 없음 |
| Execution (OMS) | 주문 상태머신/체결 반영 | 불일치 시 recon 체크 유도 |
| Notification Router | 이벤트 기반 Telegram/Slack 알림 전송/재시도/중복제거 | 실패 기록 + fallback |
| Collaboration Hub | 리서치 보고/회의 로그/액션아이템 저장 및 채널 라우팅 | 누락 시 알림/재전송 |
| GitOps Change Manager | 변경 제안->브랜치->PR->CI 파이프라인 자동화 | 머지 차단/롤백 |
| TCA-lite | 슬리피지/체결품질 계산 | 지표 누락 알림 |
| Reconciliation Engine | 주문-체결-잔고 정합성 확인 | FAIL 시 즉시 PAUSE |
| Pause/Resume Manager | 중단/재개 정책 집행 | 수동 개입 요구 |
| Ledger/PnL Engine | 원장/실현손익/일별 KPI 계산 | 불일치 감지 알림 |
| Strategy Coordinator Agent | 주간 개선 우선순위/실험 선정 | 실행권 없음(제안만) |
| Finance/Tax Agent | 월말/연말 세금 산출 검증 | 거래 실행권 없음 |
| Learning Loop | casebook/룰패치/검증 파이프라인 | 검증 실패 시 룰 폐기 |

## 4. Judge Layer
### 4.1 Safe Judge (실행권자)
하드 게이트:
- `regime.trade_allowed=false` -> HOLD
- `risk.veto=true` -> HOLD
- `ops unhealthy` 또는 `RECONCILIATION_FAIL` -> PAUSE
- 일손실 제한 도달 -> PAUSE
- 스프레드 급등(비용 폭발) -> HOLD/PAUSE

소프트 평가:
- 신호강도, 기대 R/R, 예상비용(수수료+스프레드+슬리피지), 익스포저

### 4.2 AI Judge (shadow)
- 동일 입력에서 병렬 판단 결과를 `decisions(judge_type=AI)` 저장
- `shadow_policy`에 정의된 동일 조건(cost/fill/benchmark)으로만 평가
- 초기 실행권 없음

## 5. 이벤트 소싱 기준
- 모든 사건은 `events` append-only 저장
- 핵심 키: `event_id`, `ts`, `event_type`, `entity_type`, `entity_id`, `run_id`, `rule_version_id`, `payload`

핵심 이벤트:
- Data: `MARKET_SNAPSHOT`, `FEATURE_SNAPSHOT`, `ORDERBOOK_SNAPSHOT`
- Decision: `AGENT_OPINION`, `SAFE_DECISION`, `AI_DECISION`
- Trading: `ORDER_SUBMITTED`, `ORDER_ACK`, `ORDER_CANCELED`, `ORDER_REJECTED`, `FILL`
- Ops/Risk: `RISK_VETO`, `REGIME_BLOCK`, `PAUSE`, `RESUME`, `RECONCILIATION_FAIL`
- Learning: `RULE_PROPOSAL`, `RULE_APPROVED`, `RULE_REJECTED`, `RULE_ACTIVATED`, `MISTAKE_TAG`
- Governance/Finance: `WEEKLY_PRIORITY_SET`, `IMPROVEMENT_ACTION_ASSIGNED`, `TAX_EXPORT_COMPLETED`, `TAX_EXPORT_FAILED`
- Research/Meeting: `AGENT_DAILY_REPORT`, `RESEARCH_DAILY_BRIEF`, `MEETING_STARTED`, `MEETING_MESSAGE`, `MEETING_SUMMARY`, `MEETING_ACTION_ASSIGNED`
- Change/GitOps: `CHANGE_PROPOSAL`, `CHANGE_VALIDATED`, `PR_OPENED`, `CI_PASSED`, `CI_FAILED`, `PR_READY`, `PR_APPROVED`, `PR_MERGED`, `ROLLBACK_EXECUTED`

알림 흐름:
- `events`/`orders`/`fills`/`reconciliation_checks`를 Notification Router가 구독
- 알림 규칙(심각도/중복제거/집계)을 적용
- Telegram/Slack으로 전송하고 결과를 `notification_deliveries`에 필수 기록(선택적으로 `events.payload` 요약 미러링)
- 상세 규격은 `notifications_telegram.md` 참조

GitHub 변경관리 흐름:
- Service Engineering Agent가 변경 제안 수집
- GitOps Change Manager가 브랜치/PR/CI 자동 실행
- 사용자 리뷰/승인 후 머지
- 상세 가드레일은 `engineering_change_management.md` 참조

거버넌스/재무 흐름:
- Strategy Coordinator가 주간 KPI 기반 개선 아젠다를 생성
- Finance/Tax Agent가 월말 산출물/정합성 검증 결과를 생성
- Outcome Evaluator가 거래 종료 후 오판 원인(`decision_outcomes`)을 기록
- 세 결과는 이벤트/알림으로 배포되고 다음 개선 루프의 입력으로 사용

## 6. 실행 품질(TCA-lite)
필수 저장:
- `decision_mid`, `submit_mid`, `fill_vwap`
- `slippage_bps_vs_decision`, `slippage_bps_vs_submit`
- `spread_bps_at_submit`, `filled_ratio`, latency

목적:
- “전략 문제”와 “집행 문제”를 분리해서 개선한다.

## 7. 정합성/복구
정합성 체크 대상:
- 주문 상태
- 체결 누락/중복
- 포지션 계산
- 잔고 free/locked

재시작 복구 순서:
1. open orders 재조회
2. fills 재동기화
3. balances 재조회
4. positions 재계산
5. recon OK 연속 충족 시 RESUME

## 8. 저장 계층
1. Time-series Lake: Parquet + DuckDB
2. Event Store: Postgres `events`
3. Vector Store: casebook 임베딩
4. Graph DB: v2 계획(후순위)

세부 DDL 기준 문서: `database.md`

## 9. 운영 원칙
- Fail Closed
- Deterministic
- Traceable
- Cost-aware
- Shadow fairness(동일 조건 비교)
- Contract-first (`rules.yaml`, `reason_codes.md`, `order_state_machine.md`)

관련 상세 문서:
- 알림: `notifications_telegram.md`
- 운영 대응: `ops_runbook.md`
- 세금/정산: `tax_reporting.md`
- GitHub 변경관리: `engineering_change_management.md`
- 초기 룰/리스크: `rules.yaml`
- 원인코드 표준: `reason_codes.md`
- 주문 상태머신: `order_state_machine.md`
- 초기값 근거/튜닝 규칙: `trading_contract_rationale.md`
