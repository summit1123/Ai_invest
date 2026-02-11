# notifications_telegram.md - Telegram + Slack 알림 표준 (v1.2)

> 목적: 회의/회고/투자/거래/리서치 알림을 **Telegram + Slack 멀티채널**로 표준화한다.  
> 범위: 메시지 규격, 트리거, 템플릿, 전송 신뢰성, 소음 제어, 팀별 채널 라우팅.  
> 비범위: Bot 토큰 발급, 실제 배포.

---

## 1. 채널 정책

- 채널 정책: Telegram + Slack 병행
- 메시지 언어: 한국어 고정
- 기준 시간대: KST (`UTC+09:00`)
- 확장 정책: Discord/Notion은 후순위(선택)

운영 원칙:
- Telegram과 Slack을 `notification_deliveries.channel`로 각각 추적
- Bot 토큰/시크릿은 비밀 저장소에서 관리하고 로그 출력 금지
- 팀별 방(채널) 분리:
  - `ops-critical`: 장애/PAUSE/RECON_FAIL
  - `trading-feed`: 결정/주문/체결
  - `review-report`: 일/주간 리포트
  - `research-daily`: 리서치팀 일일 조사 브리프
  - `agent-meeting`: Agent 회의 로그/회의록/액션아이템
  - `engineering-change`: PR/CI/변경관리

### 1.1 Slack 워크스페이스 권장 구조
- 워크스페이스: `ai-invest`
- 채널 네이밍:
  - `ai-invest-ops-critical`
  - `ai-invest-trading-feed`
  - `ai-invest-review-report`
  - `ai-invest-research-daily`
  - `ai-invest-agent-meeting`
  - `ai-invest-engineering-change`
  - `ai-invest-governance`

---

## 2. 표준 이벤트 타입

### 2.1 NotificationEventV1 (JSON)

```json
{
  "event_id": "uuid",
  "ts_utc": "2026-02-09T10:15:30Z",
  "severity": "CRITICAL",
  "category": "OPS",
  "run_id": "uuid",
  "decision_id": "uuid-or-null",
  "symbol": "KRW-BTC",
  "title_ko": "정합성 FAIL로 거래 중단",
  "body_ko": "reconciliation FAIL 감지, 시스템 PAUSE 상태",
  "dedupe_key": "OPS:RECON_FAIL:KRW-BTC:2026-02-09T10:15",
  "payload": {
    "event_type": "RECONCILIATION_FAIL",
    "reason_type": "RECON_FAIL",
    "details": {}
  }
}
```

`category` enum:
- `RISK`
- `OPS`
- `ORDER`
- `FILL`
- `DECISION`
- `RESEARCH`
- `MEETING`
- `GOVERNANCE`
- `FINANCE`
- `ENGINEERING`
- `DAILY_REVIEW`
- `WEEKLY_REVIEW`

`severity` enum:
- `CRITICAL`
- `HIGH`
- `NORMAL`

---

## 3. 트리거 매트릭스

| 트리거 | 소스 | 심각도 | SLA | 템플릿 ID | dedupe 기준 | 권장 채널 |
|---|---|---|---|---|---|
| `PAUSE` | `events(PAUSE)` | `CRITICAL` | 5초 이내 | `tpl_pause_critical` | `event_id` | Telegram+Slack `ops-critical` |
| `RESUME` | `events(RESUME)` | `NORMAL` | 10초 이내 | `tpl_resume_notice` | `event_id` | Telegram+Slack `ops-critical` |
| `RECON_FAIL` | `reconciliation_checks(status=FAIL)` | `CRITICAL` | 5초 이내 | `tpl_recon_fail` | `event_id` | Telegram+Slack `ops-critical` |
| `ORDER_REJECTED` | `events(ORDER_REJECTED)` | `HIGH` | 10초 이내 | `tpl_order_rejected` | `order_id + reason` | Telegram+Slack `trading-feed` |
| `FILL` | `fills` | `NORMAL` | 10초 이내 | `tpl_fill_notice` | `fill_id` | Telegram+Slack `trading-feed` |
| `SAFE_DECISION` | `decisions(judge_type=SAFE)` | `NORMAL` | 10초 이내 | `tpl_safe_decision` | `decision_id` | Telegram+Slack `trading-feed` |
| `AGENT_DAILY_REPORT` | `agent_daily_reports` | `NORMAL` | 5분 이내 | `tpl_agent_daily_report` | `report_id` | Telegram+Slack `research-daily` |
| `RESEARCH_DAILY_BRIEF` | `events(RESEARCH_DAILY_BRIEF)` | `NORMAL` | 5분 이내 | `tpl_research_daily_brief` | `brief_date + team` | Telegram+Slack `research-daily` |
| `MEETING_SUMMARY` | `meeting_sessions` | `NORMAL` | 5분 이내 | `tpl_meeting_summary` | `meeting_id` | Telegram+Slack `agent-meeting` |
| `MEETING_ACTION_ASSIGNED` | `meeting_messages` | `HIGH` | 5분 이내 | `tpl_meeting_action_items` | `meeting_id + action_hash` | Telegram+Slack `agent-meeting` |
| `TRADE_PLAN_SET` | `events(TRADE_PLAN_SET)` | `HIGH` | 5분 이내 | `tpl_trade_plan_set` | `slot_key + symbol + plan_hash` | Telegram+Slack `agent-meeting` |
| `PR_OPENED` | 변경관리 파이프라인 | `NORMAL` | 30초 이내 | `tpl_pr_opened` | `proposal_id + pr_number` | Telegram+Slack `engineering-change` |
| `CI_FAILED` | 변경관리 파이프라인 | `HIGH` | 30초 이내 | `tpl_ci_failed` | `proposal_id + ci_run_id` | Telegram+Slack `engineering-change` |
| `PR_READY` | 변경관리 파이프라인 | `NORMAL` | 30초 이내 | `tpl_pr_ready` | `proposal_id + pr_number` | Telegram+Slack `engineering-change` |
| `PR_MERGED` | 변경관리 파이프라인 | `NORMAL` | 30초 이내 | `tpl_pr_merged` | `proposal_id + pr_number` | Telegram+Slack `engineering-change` |
| `WEEKLY_PRIORITY_SET` | Strategy Coordinator | `NORMAL` | 30초 이내 | `tpl_weekly_priority` | `week_start + priority_id` | Telegram+Slack `governance` |
| `TAX_EXPORT_COMPLETED` | Finance/Tax Agent | `NORMAL` | 30초 이내 | `tpl_tax_export_done` | `export_id` | Telegram+Slack `review-report` |
| `TAX_EXPORT_FAILED` | Finance/Tax Agent | `HIGH` | 30초 이내 | `tpl_tax_export_fail` | `export_id + error_code` | Telegram+Slack `review-report` |
| Daily Review | `pnl_daily`, `realized_trades` | `NORMAL` | D+0 23:10 KST | `tpl_daily_review` | `date` | Telegram+Slack `review-report` |
| Weekly Review | `casebook_docs`, 룰 결과 집계 | `NORMAL` | 매주 일 21:00 KST | `tpl_weekly_review` | `week_start` | Telegram+Slack `review-report` |

---

## 4. 템플릿 표준

필수 템플릿 ID:
- `tpl_pause_critical`
- `tpl_recon_fail`
- `tpl_order_rejected`
- `tpl_fill_notice`
- `tpl_daily_review`
- `tpl_weekly_review`

추가 템플릿 ID:
- `tpl_safe_decision`
- `tpl_resume_notice`
- `tpl_agent_daily_report`
- `tpl_research_daily_brief`
- `tpl_meeting_summary`
- `tpl_meeting_action_items`
- `tpl_trade_plan_set`
- `tpl_tax_export_fail`
- `tpl_tax_export_done`
- `tpl_weekly_priority`
- `tpl_pr_opened`
- `tpl_ci_failed`
- `tpl_pr_ready`
- `tpl_pr_merged`

### 4.1 템플릿 예시 (한글)

`tpl_pause_critical`
```text
[운영][치명] 거래 중단 (PAUSE)
- 시각: {ts_kst}
- 사유: {reason_type}
- 심볼: {symbol}
- 실행ID: {run_id}
- 조치: 자동 거래 중단, Runbook 확인 필요
```

`tpl_recon_fail`
```text
[운영][치명] 정합성 실패 (RECON_FAIL)
- 시각: {ts_kst}
- 범위: {scope}
- 요약: {diff_summary}
- 조치: {action_taken}
```

`tpl_resume_notice`
```text
[운영] 거래 재개 (RESUME)
- 시각: {ts_kst}
- 사유: {resume_reason}
- 검증: recon OK {recon_ok_count}회
- 실행ID: {run_id}
```

`tpl_order_rejected`
```text
[거래][높음] 주문 거부
- 시각: {ts_kst}
- 심볼: {symbol}
- order_id: {order_id}
- 사유: {reject_reason}
```

`tpl_fill_notice`
```text
[거래] 체결 알림 ({symbol})
- 시각: {ts_kst}
- 매수/매도·수량: {side}/{quantity}
- 체결가: {price}
- 수수료: {fee} {fee_currency}
```

`tpl_daily_review`
```text
[리뷰][일간] {day}
- 실현손익: {realized_pnl}
- 수수료: {fees_paid}
- 거래 수: {trades_count}
- 최대 낙폭: {max_drawdown}
- 주요 손실 태그: {top_loss_tags}
```

`tpl_weekly_review`
```text
[리뷰][주간] {week_label}
- 주간 손익: {weekly_pnl}
- 승률: {win_rate}
- 손실 원인 Top3: {loss_tags_top3}
- 룰 패치 상태: {rule_patch_status}
```

`tpl_weekly_priority`
```text
[거버넌스][주간] 개선 우선순위
- 주차: {week_label}
- 우선순위: {priority_title}
- 가설: {hypothesis}
- 담당: {owner}
- 성공 기준: {success_criteria}
```

`tpl_agent_daily_report`
```text
[리서치][일간] 에이전트 보고 ({agent_name})
- 보고일: {report_date}
- 핵심 인사이트: {insights}
- 위험 경고: {risks}
- 제안 액션: {proposed_actions}
```

`tpl_research_daily_brief`
```text
[리서치][일간] 데일리 브리프
- 기준일: {report_date}
- 시장 요약: {market_summary}
- 주요 이슈: {key_findings}
- 내일 확인 포인트: {next_watch_items}
```

`tpl_meeting_summary`
```text
[회의] 회의록 요약
- 회의ID: {meeting_id}
- 참석팀: {participants}
- 결론: {meeting_outcome}
- 보류/리스크: {open_risks}
```

`tpl_meeting_action_items`
```text
[회의][액션] 후속 조치
- 회의ID: {meeting_id}
- 담당자: {owner}
- 액션: {action_item}
- 기한: {due_date}
```

`tpl_trade_plan_set`
```text
[거버넌스] 트레이드 플랜 확정
- 회의/슬롯: {meeting_id}/{slot_key}
- 심볼/목표비중: {symbol}/{target_position_pct}
- 유효시간: {valid_from_kst} ~ {valid_to_kst}
- 허용액션/제약/근거 요약: {allowed_actions}/{constraints}/{rationale_summary}
```

`tpl_tax_export_done`
```text
[재무] 세금 산출 완료
- 기간: {period_label}
- export_id: {export_id}
- 건수: {row_counts}
- 체크섬: {checksum_sha256}
```

---

## 5. 전송 신뢰성

전송 규칙:
- HTTP timeout: 3초
- 재시도: 최대 3회
- backoff: 1초 -> 3초 -> 9초
- 최종 실패 시: `fallback` 요약 메시지 1회 시도

중복 방지:
- 1차 키: `event_id`
- 보조 키: `dedupe_key`
- TTL: 24시간 캐시

실패 처리:
- 실패 payload에 `error_code`, `attempt`, `last_error` 기록
- 기록 위치:
  - 필수: `notification_deliveries` 테이블
  - 선택: `events.payload.notification_delivery` 요약

채널 라우팅:
- `channel=TELEGRAM` 또는 `channel=SLACK`로 개별 delivery row 저장
- 동일 이벤트를 Telegram+Slack 동시 발송 시 `delivery_id`는 별개로 기록
- `channel_target`(`ops-critical`, `research-daily` 등) 값을 payload에 포함

---

## 6. 소음 제어

집계 규칙:
- 동일 원인의 `ORDER_REJECTED`가 5분 내 3회 이상이면 개별 알림 대신 집계 알림 1회
- `FILL` 알림은 심볼 단위 10초 윈도우 내 배치 가능

우선순위 라우팅:
- `CRITICAL`: 즉시 전송 + 재시도
- `HIGH`: 즉시 전송, 실패 시 1회 fallback
- `NORMAL`: 큐 배치 허용(최대 10초 지연)

---

## 7. 보고서 스케줄

- Daily Review: 매일 23:10 KST
- Weekly Review: 매주 일요일 21:00 KST
- Agent Daily Report: 매일 20:30 KST
- Research Daily Brief: 매일 21:30 KST
- Agent Meeting Summary: 회의 종료 후 5분 이내
- 월말 세금 산출 완료 알림: 매월 말일 23:30 KST

세부 산출 로직은 `tax_reporting.md`를 따른다.

---

## 8. 테스트 시나리오 (문서 기준)

1. `PAUSE` 발생 후 5초 내 `tpl_pause_critical` 1회 전송
2. `RESUME` 발생 시 `tpl_resume_notice` 전송 검증
3. 동일 `dedupe_key` 중복 이벤트 수신 시 1회만 전송
4. `ORDER_REJECTED` 연속 발생 시 집계 규칙 적용
5. Telegram API 실패 시 재시도/실패기록 남김
6. `CI_FAILED` 발생 시 `tpl_ci_failed` 전송 및 중복 방지 검증
7. `WEEKLY_PRIORITY_SET` 발생 시 `tpl_weekly_priority` 전송 검증
8. `TAX_EXPORT_COMPLETED/FAILED` 알림 분기 검증
9. Daily/Weekly 리포트 시각과 숫자 정합성 검증
10. `RESEARCH_DAILY_BRIEF`가 `research-daily` 채널로 전송되는지 검증
11. `MEETING_SUMMARY`/`MEETING_ACTION_ASSIGNED`가 `agent-meeting` 채널로 전송되는지 검증
12. 동일 이벤트 Telegram+Slack 동시 전송 시 `notification_deliveries` 2건 기록 검증
