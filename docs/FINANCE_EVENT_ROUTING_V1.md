# FINANCE_EVENT_ROUTING_V1.md

## 목적
`AI_invest` 주요 이벤트를 Finance Discord 채널과 Telegram에 어떻게 라우팅할지 1차 기준을 정의한다.

---

## 1. Telegram 우선
### 실시간 우선 이벤트
- `PAUSE`
- `RECON_FAIL`
- `RESUME`
- `ORDER_REJECTED`
- `DAILY_LOSS`
- 치명적 `RATE_LIMIT`

원칙:
- 즉시성 우선
- 운영자가 바로 봐야 하는 이벤트

---

## 2. Discord 채널 라우팅
### #finance-ops
- orchestrator 상태 스냅샷
- worker restart 급증
- 주기 점검 결과
- 운영 상태 일일 요약

### #finance-alerts
- `PAUSE`
- `RECON_FAIL`
- `RESUME`
- `ORDER_REJECTED`
- 기타 HIGH/CRITICAL 운영 경고

### #finance-reviews
- `tpl_daily_review`
- `tpl_weekly_review`
- 손익/수수료/드로우다운 요약
- decision outcome 기반 회고

### #finance-dev
- 반복 장애 RCA
- 수정이 필요한 rules/scripts/notification 변경
- reviews에서 나온 개선 과제

---

## 3. 핸드오프 규칙
- alerts에서 끝내지 않는다
- 반복 이슈는 반드시 dev로 넘긴다
- 리뷰에서 나온 개선안은 dev로 넘긴다
- 수정 후 검증 결과는 다음 review에서 확인한다

---

## 4. 우선 구현 순서
1. alerts 라우팅
2. daily/weekly review 라우팅
3. ops 상태 요약 라우팅
4. dev 액션아이템 자동 생성은 후순위
