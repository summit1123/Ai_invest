# 멀티 에이전트 자동투자 비전/운영 설계 (PnL-first, Triggered-LLM, v1.1+)

## 0) 최종 목표(비전)
시세/피처는 상시(분/초 단위)로 수집하되, **LLM은 “회의에서 합의한 트리거”가 발생했을 때만 호출**한다.

- 멀티 에이전트는 “사고/협업/보고”를 수행한다(리서치/회의/가설/룰 패치 제안).
- **실행권은 Safe Judge**만 가진다(하드 게이트: ops/risk/recon/cost/pause).
- 모든 판단/회의/룰/결과는 DB에 이벤트로 남겨 **회귀(회고) 가능한 자산**으로 축적한다.

핵심 문장:
> 항상 수집, 조건부로 생각(LLM), 항상 기록, 실행은 Safe.


## 1) 상시 운영 루프
### 1.1 Market Watcher (상시, 15s~1m)
- Upbit public API/WS로 시세/호가/캔들 수집
- 피처 계산(ATR/RSI/VolZ 등) 및 저장
- 저장:
  - `events(MARKET_SNAPSHOT)`
  - `events(FEATURE_SNAPSHOT)`
  - `market_quotes`

### 1.2 Trigger Evaluator (상시, 결정적 룰)
회의에서 확정한 **Trigger Policy**(룰/임계값/시간대)를 기준으로 “지금 LLM/회의를 호출할지”를 결정한다.

트리거 예:
- 비용/유동성: spread 급등, 슬리피지 추정 초과
- 레짐: ATR 급등, 데이터 누락/지연
- 성과: 연속 손실/일 손실 임계 접근
- 운영: recon FAIL, pause 진입
- 시간: 하루 3회(8시간 단위) 정기 회의

트리거 발생 시:
- `meeting_sessions` 생성(=회의 시작) 또는 `events(LLM_TASK_CREATED)` 기록
- Agent 실행(LLM 포함) → `meeting_messages` 누적 → 요약/액션아이템 확정

### 1.3 Meeting (트리거 발생 시)
목표: “지금 왜 중요한지”를 정리하고 **Trade Plan(종목/비중/허용조건)**을 확정한다.

- 저장:
  - `meeting_sessions`
  - `meeting_messages`
  - `events(MEETING_STARTED / MEETING_MESSAGE / MEETING_SUMMARY / MEETING_ACTION_ASSIGNED)`
- 알림:
  - `tpl_meeting_summary`, `tpl_meeting_action_items` (Telegram/Slack)

### 1.4 Trade Plan(회의 산출물) → Safe Judge 입력
회의의 결론은 “말”이 아니라 **데이터(Trade Plan)**로 저장되어야 한다.

Trade Plan 최소 필드:
- `symbol`
- `target_position_pct`
- `valid_from` / `valid_to`
- `entry_allowed` 조건(예: max spread, regime allowed 등)
- `notes`(설명/근거)

이 Trade Plan은 Safe Judge가 최종 판단할 때 참고하되,
**ops/risk/recon/pause 같은 하드 게이트는 항상 우선**한다.


## 2) 시간 운영(권장)
정기 회의는 하루 3회(8시간 단위)를 기본으로 한다.

- A안(고정 8시간): 00:00 / 08:00 / 16:00 KST
- B안(시장 체감 시간대): 09:00 / 17:00 / 01:00 KST

추가로 “이상징후 트리거”는 시간과 무관하게 즉시 회의를 열 수 있다(Incident 회의).


## 3) 데이터 자산화(회귀/학습)
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


## 4) 개발/운영 방식(커밋 단위)
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

