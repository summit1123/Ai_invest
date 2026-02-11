# observer_mode_notifications.md

## 1) 목적
- 운영자는 UI를 열지 않아도 텔레그램 메시지 만으로 현재 상태를 이해해야 한다.
- 알림은 "사건 사실"만 전달하는 수준을 넘어서, "판단 근거"와 "운영자 액션"을 함께 제공해야 한다.
- 실행 권한은 계속 Safe Judge에 고정한다.

## 2) 알림 가독성 원칙
- 모든 메시지는 한국어 우선.
- 내부 코드(`RG_PASS` 등)는 보조 정보로만 사용하고, 본문은 자연어 설명 우선.
- 각 메시지는 아래 4블록을 기본으로 사용.
  - 한 줄 요약
  - 왜 이런 결론이 나왔는가(근거/게이트)
  - 지금 상태(리스크/운영/포지션)
  - 운영자 확인 포인트(있으면)

## 3) 이벤트별 목표 포맷
### 3.1 SAFE_DECISION
- 필수: 액션(BUY/SELL/HOLD/PAUSE), 심볼, 시각
- 근거: 핵심 reason(한국어), 주요 지표(spread/rsi/atr/vol)
- 컨텍스트: market/regime/risk/ops 요약
- 계획 연결: 현재 trade plan slot/target 표시
- 운영자 확인 포인트:
  - PAUSE 또는 HOLD(게이트 차단)일 때 확인 항목 제시

### 3.2 MEETING_SUMMARY
- 필수: 회의 ID, 생성 방식(LLM/Deterministic), 최종 결론
- 회의록은 아래 순서로 정규화
  - 결론
  - 핵심 근거
  - 제약/게이트
  - 남은 리스크/미확인 항목
  - 액션 아이템

### 3.3 TRADE_PLAN_SET (신규 알림)
- 필수: 유효 시간(valid_from~valid_to), 심볼, 목표 비중
- 제약: allowed_actions, cooldown, rebalance band, cost/risk 한도
- 근거: 회의 충돌 해결 로그 요약(있을 때)
- 운영자 확인 포인트: TTL 만료/매수금지 플랜 여부

### 3.4 RESEARCH_DAILY_BRIEF
- 필수: 일자, 요약, watchlist
- 확장: 주요 뉴스 링크(최대 3개) 포함
- 금지: 방향성 매수/매도 직접 권고

## 4) 구현 단계
1. 템플릿 고도화
- `tpl_safe_decision`/`tpl_meeting_summary`/`tpl_research_daily_brief` 가독성 개편
- 내부 코드 출력 비중 축소, 한국어 설명 강화

2. 회의 결과 플랜 알림 추가
- `tpl_trade_plan_set` 추가
- `NotificationService.notify_trade_plan_set()` 추가
- 거버넌스 회의 종료 시 `TRADE_PLAN_SET` 이벤트와 함께 즉시 알림 발송

3. 입력 데이터 확장
- 연구 브리프 알림에 headline 링크를 전달할 수 있게 payload 확장

4. 테스트 및 검증
- 템플릿 단위 테스트: 핵심 텍스트/필드 노출 검증
- 서비스 단위 테스트: dedupe 키, payload 전달, 전송 상태 기록 검증
- 통합 테스트: 회의 1회 실행 후 `MEETING_SUMMARY`, `MEETING_ACTION_ASSIGNED`, `TRADE_PLAN_SET` 알림 이력 확인

## 5) 완료 기준(DoD)
- 텔레그램 메시지에서 내부 코드만 보지 않고도 의미가 전달된다.
- 회의가 끝나면 요약 + 액션아이템 + 트레이드플랜 알림이 모두 남는다.
- 리서치 브리프는 요약 + 리스크 + 링크를 제공한다.
- 기존 테스트와 신규 테스트가 모두 통과한다.
