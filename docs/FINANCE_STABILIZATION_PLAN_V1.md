# FINANCE_STABILIZATION_PLAN_V1.md

## 목적
finance 조직이 `AI_invest`를 단순 분석 대상이 아니라,
**안정적으로 운영되고 지속적으로 개선되는 시스템**으로 관리하기 위한 1차 안정화 계획.

---

## 1. 최상위 목표
1. 시스템이 죽지 않게 한다
2. 죽어도 빨리 감지하고 복구한다
3. 운영 상태가 항상 보이게 한다
4. 매일 개선 포인트가 쌓이게 한다

즉, 수익률 최적화 이전에
**운영 안정성 / 가시성 / 복구 가능성 / 개선 루프**를 먼저 확보한다.

---

## 2. 안정화 우선순위
### P0. 치명 운영 안정성
- orchestrator 생존 여부
- worker 비정상 종료 감지
- `PAUSE` / `RECON_FAIL` 즉시 감지
- 재기동 절차 표준화

### P1. 운영 가시성
- 현재 상태를 한 번에 볼 수 있어야 함
- `runtime/orchestrator_status.json` 기반 상태 확인
- review / alerts / ops 채널 역할 분리

### P2. 복구 가능성
- 장애 유형별 표준 대응
- pause → recovery → resume 기준 명확화
- 재시작 후 검증 체크리스트 확보

### P3. 일일 개선 루프
- daily review
- 반복 장애 RCA
- 개선 액션 생성
- 반영 후 효과 확인

---

## 3. 운영 감시 대상
### 런타임
- `scripts/run_multi_orchestrator.py`
- `scripts/run_paper_loop.py`
- `scripts/run_live_loop.py`
- governance / review / adaptive tuning loop

### 상태 파일 / API
- `runtime/orchestrator_status.json`
- `GET /api/v1/ops/status`
- `GET /api/v1/ops/why-paused`
- `GET /api/v1/ops/pnl-today`

### 핵심 이벤트
- `PAUSE`
- `RESUME`
- `RECON_FAIL`
- `ORDER_REJECTED`
- `SAFE_DECISION`
- `FILL`
- Daily / Weekly Review

---

## 4. finance 조직 책임
### 운영 안정화
- orchestrator 상태 점검
- 이상 징후 감지
- 운영 경보 확인
- 재기동 판단

### 시스템 고도화
- logs / reviews 분석
- rules/scripts/notifications 개선
- 코드 수정
- 구조 개선 문서화

### 지속 개선
- daily review 확인
- 반복 문제 누적
- 개선 액션 생성
- 다음 리뷰에서 효과 검증

---

## 5. 성공 기준
- 장애 감지가 늦지 않다
- 장애 후 재기동/복구 절차가 문서화돼 있다
- Discord 채널만 봐도 현재 상태를 이해할 수 있다
- 하루 단위로 개선 포인트가 쌓인다
- 수정이 review와 연결된다

---

## 6. 한 줄 결론
finance 조직의 첫 임무는 수익 극대화가 아니라,
`AI_invest`를 **안정적으로 돌고, 보이고, 복구되고, 매일 개선되는 시스템**으로 만드는 것이다.
