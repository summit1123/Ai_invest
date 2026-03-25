# FINANCE_DISCORD_OPERATING_LOOP_V1.md

## 목적
`AI_invest`의 운영 이벤트를 Finance Discord 서버의 채널 구조에 맞춰
실제 운영 루프로 연결하기 위한 1차 문서.

대상 채널:
- `#finance-ops`
- `#finance-alerts`
- `#finance-reviews`
- `#finance-dev`

---

## 1. 시스템 해석
이 레포의 운영 핵심은 아래 루프다.

1. 시장 데이터 수집
2. Agent 판단
3. Safe Judge 최종 결정
4. Executor 실행
5. Storage/Ledger 기록
6. Notification 전송
7. Review / Governance / Learning 반영

즉, finance Discord는 단순 알림방이 아니라
**운영 / 경보 / 복기 / 개선**을 분리한 조직형 워크스페이스여야 한다.

---

## 2. 채널별 역할
### #finance-ops
역할:
- 오케스트레이터/워커 상태
- 루프 정상 동작 여부
- 런타임 상태 점검
- 배치/프로세스/상태 파일 확인

주요 소스:
- `runtime/orchestrator_status.json`
- `scripts/run_multi_orchestrator.py`
- `scripts/run_paper_loop.py`
- `scripts/run_live_loop.py`
- `GET /api/v1/ui/orchestrator/status`

올라와야 하는 내용:
- orchestrator alive/dead
- worker restart 증가
- pause/resume 상태 변화
- 일일 운영 점검 요약

---

### #finance-alerts
역할:
- 즉시 확인 필요한 장애/경고/임계 이벤트

주요 이벤트:
- `PAUSE`
- `RESUME`
- `RECON_FAIL`
- `ORDER_REJECTED`
- 심각한 `RATE_LIMIT`
- `DAILY_LOSS` 도달
- reconciliation 실패

관련 템플릿:
- `tpl_pause_critical`
- `tpl_resume_notice`
- `tpl_recon_fail`
- `tpl_order_rejected`

운영 원칙:
- 여기선 소음보다 즉시성을 우선
- 동일 이벤트는 dedupe 필요
- Telegram 실시간 알림과 중복될 수 있으나,
  Discord에는 원인 기록과 후속 조치까지 남겨야 함

---

### #finance-reviews
역할:
- 일간/주간 리뷰
- 손익/수수료/거래 수/최대낙폭 요약
- 전략 회고
- 개선 포인트 누적

주요 이벤트/문서:
- Daily Review (`tpl_daily_review`)
- Weekly Review (`tpl_weekly_review`)
- `decision_outcomes`
- `pnl_daily`
- casebook / outcome evaluator 결과

올라와야 하는 내용:
- 오늘 손익 요약
- 손실 원인 태그 Top
- 반복되는 실수 패턴
- 다음 개선 액션 1~3개

---

### #finance-dev
역할:
- 코드 수정 논의
- 운영 개선 작업
- rules / scripts / notifications / runbook 변경
- GitHub 이슈성 작업

주요 입력:
- alerts에서 넘어온 장애 원인
- reviews에서 나온 개선 과제
- ops에서 확인된 구조적 문제

주요 출력:
- 수정 계획
- 변경 파일 목록
- 구현 메모
- PR/커밋/배포 준비 사항

---

## 3. 운영 루프
### Loop A. 실시간 운영 루프
1. orchestrator / runtime loop 동작
2. 이상 발생 시 Telegram + `#finance-alerts`
3. 상태 점검 요약은 `#finance-ops`
4. 반복 이슈는 `#finance-dev`로 이동

### Loop B. 일간 리뷰 루프
1. Daily Review 생성
2. `#finance-reviews`에 기록
3. 주요 손실/오류 패턴 추출
4. 수정 필요 시 `#finance-dev` 액션 생성

### Loop C. 장애 대응 루프
1. `PAUSE` / `RECON_FAIL` / `ORDER_REJECTED` 발생
2. `#finance-alerts` 즉시 기록
3. runbook 기준 초기 조치
4. 원인/재발 방지안은 `#finance-dev`
5. 후속 회고는 `#finance-reviews`

### Loop D. 개선 루프
1. reviews/alerts/ops에서 개선 포인트 수집
2. `#finance-dev`에서 수정 계획 정리
3. 테스트/검증 후 반영
4. 다음 리뷰에서 효과 확인

---

## 4. 우선순위 높은 자동 연결 대상
1. `PAUSE` / `RECON_FAIL` / `RESUME` → `#finance-alerts`
2. orchestrator 상태 요약 → `#finance-ops`
3. Daily Review / Weekly Review → `#finance-reviews`
4. 반복 장애/개선 과제 → `#finance-dev`

---

## 5. 한 줄 결론
Finance Discord는 단순 대화방이 아니라,
`AI_invest`의 운영 이벤트를 **ops / alerts / reviews / dev**로 분리해 누적하는 운영 콘솔 역할을 해야 한다.
