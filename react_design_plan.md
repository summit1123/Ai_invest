# Agent 회의 기반 자동매매 시스템 - React 디자인/UX 실행 계획 (v1.1)

## 1. 제품 정체성
- 자동매매를 "수익 자랑 UI"가 아니라 **판단 학습 코치**로 설계한다.
- 동시에 v1.1에서는 **실행 품질/정합성/중단 이력**을 핵심 화면에 포함한다.

## 2. 핵심 UX 흐름
`오늘 시장 -> Agent 회의 -> Judge 결론 -> 실행 품질 -> 정합성/중단 상태 -> 복기`

## 3. 정보 구조(IA)
```text
/ (Today)
  |- /conference/[decisionId]
  |- /decision/[decisionId]
  |- /timeline
  |- /collaboration/rooms
  |- /collaboration/meetings/[meetingId]
  |- /research/daily
  |- /execution-quality
  |- /ops/reconciliation
  |- /ops/pause-log
  |- /ops/notifications
  |- /review/weekly
  |- /settings/risk
```

## 4. 화면 설계
### 4.1 Today
- Regime / Volatility / Risk Mode / Trade State
- 최신 decision 및 최근 PAUSE 상태

### 4.2 Agent Conference
- Agent 카드 + 충돌 패널 + follow-up 스레드
- Ops 카드에 `reconciliation_status`, `spread_bps` 강조

### 4.3 Judge Decision
- Safe 결과와 AI Shadow 결과 나란히 노출
- HOLD/PAUSE 이유 최상단 고정

### 4.4 Timeline
- 이벤트 체인: 시장 -> 의견 -> 결정 -> 주문/체결 -> 결과
- 손실 이벤트는 reason과 함께 강조

### 4.5 Execution Quality (신규)
- 지표:
  - slippage vs decision mid
  - slippage vs submit mid
  - spread at submit
  - fill ratio
  - decision->submit / submit->fill latency
- 데이터 소스: `execution_metrics`

### 4.6 Reconciliation Status (신규)
- 최근 정합성 체크 결과(OK/WARN/FAIL)
- scope별 ORDER/FILL/POSITION/BALANCE 이슈
- 데이터 소스: `reconciliation_checks`, `balances_snapshots`

### 4.7 Pause Log (신규)
- reason_type, severity, auto_resumable, resume_policy
- pause -> resume 이력 타임라인
- 데이터 소스: `pause_log`

### 4.8 Weekly Review
- 총 거래/승률/손익
- 손실 태그 Top N
- Agent 정확도 + Safe vs AI shadow 비교 요약
- Strategy Coordinator의 주간 우선순위/실험 과제 노출
- Finance/Tax Agent의 월말 산출 상태(완료/실패) 요약

### 4.9 Notification Delivery (추가)
- 알림 전송 상태(성공/실패/재시도 횟수) 조회
- 필터: severity/category(`ENGINEERING`/`GOVERNANCE`/`FINANCE` 포함)/template_id/date range
- 중복 제거(dedupe) 적용 내역 및 fallback 전송 내역
- 데이터 소스: `notification_deliveries` (필수), `events.payload.notification_delivery`는 선택 미러링

### 4.10 Collaboration Rooms (신규)
- 팀별 채널 상태:
  - ops-critical
  - trading-feed
  - review-report
  - research-daily
  - agent-meeting
  - engineering-change
- 채널별 최근 알림/실패 건/미확인 건 조회
- 데이터 소스: `communication_rooms`, `notification_deliveries`

### 4.11 Research Daily (신규)
- Agent별 일일 조사 보고 카드
- 핵심 인사이트/리스크 watchlist/다음 액션 표시
- 데이터 소스: `agent_daily_reports`, `events(RESEARCH_DAILY_BRIEF)`

### 4.12 Meeting Transcript (신규)
- 회의 단위 타임라인:
  - 주장/근거/질문/제안/액션아이템
- 회의 요약과 결정사항 표시
- 데이터 소스: `meeting_sessions`, `meeting_messages`

## 5. 컴포넌트 구조 (확장)
```text
components/
  execution/
    ExecutionQualityCard.tsx
    SlippageTable.tsx
    FillRatioChart.tsx
  ops/
    ReconciliationPanel.tsx
    PauseLogTable.tsx
    NotificationDeliveryTable.tsx
    CollaborationRoomsTable.tsx
  collaboration/
    MeetingTranscriptPanel.tsx
    ActionItemsTable.tsx
  research/
    DailyResearchCard.tsx
    SystemHealthBadge.tsx
```

## 6. API 계약 (FastAPI)
- `GET /api/v1/ui/today-overview`
- `GET /api/v1/ui/conference/{decision_id}`
- `GET /api/v1/ui/judge/{decision_id}`
- `GET /api/v1/ui/timeline`
- `GET /api/v1/ui/execution-quality`
- `GET /api/v1/ui/reconciliation-status`
- `GET /api/v1/ui/pause-log`
- `GET /api/v1/ui/notifications-delivery`
- `GET /api/v1/ui/review/weekly`
- `GET /api/v1/ui/collaboration/rooms`
- `GET /api/v1/ui/research/daily`
- `GET /api/v1/ui/meetings`
- `GET /api/v1/ui/meetings/{meeting_id}`

## 7. 디자인 <-> DB 매핑
| 화면 | 테이블 |
|---|---|
| 회의 | `agent_opinions`, `events(AGENT_OPINION)` |
| Judge | `decisions(SAFE/AI)` |
| 타임라인 | `events`, `orders`, `fills` |
| 실행 품질 | `execution_metrics`, `market_quotes` |
| 정합성 | `reconciliation_checks`, `balances_snapshots` |
| 중단 이력 | `pause_log` |
| 알림 전송 | `notification_deliveries` |
| 채널/방 | `communication_rooms` |
| 리서치 일보 | `agent_daily_reports` |
| 회의/회의록 | `meeting_sessions`, `meeting_messages` |
| 결과/회계 | `realized_trades`, `ledger_entries`, `pnl_daily` |
| 거버넌스 | `strategy_reviews` |
| 세금 산출 | `tax_export_runs` |
| 학습 | `casebook_docs`, `shadow_trades` |

## 8. 상태관리/데이터흐름
- Server state: TanStack Query
- UI state: Zustand
- 실시간: polling(5~10초), 이후 SSE/WS

## 9. 접근성/반응형
- WCAG AA
- 색상 외 라벨 중복 제공
- 모바일 1열, 데스크탑 다열

## 10. 구현 스프린트
### Sprint 1
- Today/Conference/Judge
### Sprint 2
- Timeline + Execution Quality
### Sprint 3
- Reconciliation + Pause Log
### Sprint 4
- Weekly Review + Shadow 비교
### Sprint 5 (Ops 확장)
- Notification Delivery 화면 + 운영자 필터

## 11. 완료 기준
- Safe/AI 결과 비교가 decision 단위로 노출
- 슬리피지/체결 지표가 일 단위 조회 가능
- recon FAIL/PAUSE 이력이 누락 없이 조회
- 알림 전송 성공/실패/재시도 이력이 조회 가능
- 손실 이유와 개선 포인트가 복기 화면에 연결
