# 멀티 에이전트 자동투자 비전/운영 설계 (PnL-first, Scheduled Governance + Real-time Judge, v1.1+)

## 0) 최종 목표(비전)
시세/피처는 상시(분/초 단위)로 수집하고, **실시간 의사결정은 결정적(Deterministic) 게이트 + Safe Judge로 즉시 처리**한다.

LLM은 다음 용도로만 사용한다(실행 의존 금지):
- 리서치(뉴스/이슈) 요약 및 리스크 watchlist 작성
- 회의록(Secretary) 생성: “누가/무엇을/왜” 결정했는지 한국어로 자산화
- Strategy Coordinator(CEO) 제안: 정시 회의에서 Trade Plan(종목/비중/제약) 및 주간 우선순위 제안
- 룰 패치 제안(RULE_PATCH) 생성(실제 적용은 Validator/승인 절차)

원칙:
- 멀티 에이전트는 “사고/협업/보고”를 수행한다(리서치/회의/가설/룰 패치 제안).
- **실행권은 Safe Judge**만 가진다(하드 게이트: ops/risk/recon/cost/pause).
- 모든 판단/회의/룰/결과는 DB에 이벤트로 남겨 **회귀(회고) 가능한 자산**으로 축적한다.

핵심 문장:
> 항상 수집, 항상 기록, 실행은 Safe(결정적), LLM은 리서치/설명/거버넌스에만.


## 1) 실시간 운영 루프(상시)
### 1.1 Market Watcher (15s~1m)
- Upbit public API/WS로 시세/호가/캔들 수집
- 피처 계산(ATR/RSI/VolZ 등) 및 저장
- 저장:
  - `events(MARKET_SNAPSHOT)`
  - `events(FEATURE_SNAPSHOT)`
  - `market_quotes`

### 1.2 Quant Agents (결정적, LLM 금지)
퀀트/운영 판단은 **지연이 없어야 하므로** LLM 없이 결정적으로 수행한다.

- Market Agent: RSI/VolZ/스프레드 기반 시그널(롱/청산/홀드)
- Regime Agent: ATR/데이터 품질 기반 trade_allowed
- Risk Agent: 일손실/익스포저 기반 veto
- Ops Agent: recon/429/pause 기반 veto

출력은 `agent_opinions` + `events(AGENT_OPINION)`로 자산화한다.

### 1.3 Safe Judge (실행권자, 결정적)
실시간 트리거(손절/익절/비용 급등/레짐 차단/운영 장애)는 “회의”를 기다리지 않고 **즉시 Safe Judge**가 HOLD/PAUSE/BUY/SELL을 결정한다.

- 하드 게이트: ops/risk/recon/pause/cost/spread
- 저장:
  - `decisions(judge_type=SAFE)`
  - `events(SAFE_DECISION)`
- 실행:
  - `PaperExecutor`(현재)
  - `LiveExecutor`(추후, 승인+스위치 필요)

중요:
- LLM 실패/지연이 실시간 매매에 영향을 주면 안 된다(실행 의존 금지).
- LLM은 “설명/요약/회고”에만 사용한다.


## 2) 거버넌스 루프(정시 회의)
실시간과 분리된 거버넌스는 **정해진 시간(예: 하루 2회)**에만 수행한다.

회의 목표:
- Trade Plan(종목/비중/허용조건) 갱신
- 룰 패치 제안/실험 1건 선정
- 지난 기간 이슈(비용/레짐/정합성/손실 원인) 회고

저장:
- `meeting_sessions`
- `meeting_messages`
- `events(MEETING_STARTED / MEETING_MESSAGE / MEETING_SUMMARY / MEETING_ACTION_ASSIGNED)`
- `events(TRADE_PLAN_SET)` (회의 산출물)

알림:
- `tpl_meeting_summary`, `tpl_meeting_action_items`
- `tpl_meeting_summary`는 Secretary Agent(LLM optional)가 “사람이 읽는 회의록”을 생성한다.
- Trade Plan의 종목/비중 선택은 Strategy Coordinator(LLM optional)가 담당한다(실패 시 deterministic fallback).

### 2.1 Trade Plan(회의 산출물) → 실시간 루프 입력
회의의 결론은 “말”이 아니라 **데이터(Trade Plan)**로 저장되어야 한다.

Trade Plan 최소 필드:
- `symbol`
- `target_position_pct`
- `valid_from` / `valid_to`
- `constraints`(예: max spread, regime allowed 등)
- `notes`(설명/근거)

실시간 루프는 Trade Plan을 참고하되,
**ops/risk/recon/pause 같은 하드 게이트는 항상 우선**한다.


## 3) 리서치 루프(Research Agent, LLM optional)
리서치는 “투자 실행”이 아니라 **정보 자산화/요약/리스크 상향 플래그** 역할이다.

- 입력: 뉴스 헤드라인(RSS), 현재 스냅샷/피처, ops/recon 상태
- 출력: `agent_daily_reports` + `events(RESEARCH_DAILY_BRIEF)`
- 알림: `tpl_research_daily_brief` (한국어 요약 + watchlist)

중요:
- Research Agent는 방향성(BUY/SELL)을 직접 제안하지 않는다.
- Quant/Market Agent와 역할을 분리한다.


## 4) 시간 운영(권장)
정기 회의는 하루 2회를 기본으로 한다.

- 예시: 09:00 / 21:00 KST
- 필요 시 3회 이상으로 조정 가능(LLM 호출 비용/지연 고려)

운영 장애는 Incident 성격으로 별도 기록하되,
실행 차단/복구는 Safe Judge + Ops Guard가 즉시 수행한다.


## 5) 데이터 자산화(회귀/학습)
모든 의사결정은 아래 키로 연결되어 회귀 가능해야 한다.

- `run_id`, `rule_version_id`
- `decision_id`
- `meeting_id`
- `event_id`

주요 저장소:
- `events`: 모든 핵심 이벤트의 단일 소스
- `agent_opinions`: 에이전트 의견(원문 포함)
- `meeting_*`: “협업/사고 과정” 로그
- `rule_versions`: 룰 스냅샷(변경 추적)
- `decision_outcomes`: 결과/오판 원인(OC_*)


## 6) 개발/운영 방식(커밋 단위)
원칙: **작게 변경 → 테스트/검증 → 커밋**.

권장 커밋 단위:
1) 문서(계약/설계/운영) 1개
2) DB 스키마/마이그레이션 1개
3) API 1~2개(테스트 포함)
4) 에이전트 1개(프롬프트/룰/테스트 포함)
5) 알림 템플릿 1개(실제 전송 로그 검증 포함)

검증 기준:
- `pytest` 통과
- 핵심 API curl OK
- `notification_deliveries`에 `SENT/FAILED/SKIPPED`가 남음

## 7) LLM 모델 라우팅(Agent별)
Agent별 모델/엔드포인트/effort는 `rules.yaml`의 `llm:` 섹션으로 제어한다.
- 예: Research/Secretary/Strategy Coordinator는 `gpt-5.2-pro`, 실시간 루프는 LLM 비의존
