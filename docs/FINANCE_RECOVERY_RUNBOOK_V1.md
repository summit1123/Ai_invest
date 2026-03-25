# FINANCE_RECOVERY_RUNBOOK_V1.md

## 목적
`AI_invest` 운영 중 장애 발생 시,
finance 조직이 오케스트레이터 재기동과 복구 판단을 일관되게 수행하기 위한 복구 런북.

---

## 1. 주요 복구 대상
- orchestrator down
- worker 비정상 종료
- `PAUSE`
- `RECON_FAIL`
- 반복 `ORDER_REJECTED`
- rate limit storm

---

## 2. 기본 대응 원칙
1. 먼저 원인을 확인한다
2. 무조건 재기동부터 하지 않는다
3. `RECON_FAIL`은 정합성 확인 전 자동 재개 금지
4. 재기동 후 반드시 상태 검증을 한다

---

## 3. 오케스트레이터 재기동 기준
### 재기동 고려 조건
- orchestrator dead
- worker restart 급증
- 상태 파일 갱신 중단
- API status 이상

### 기본 재기동 절차
```bash
pkill -TERM -f "scripts/run_multi_orchestrator.py" || true
sleep 1
setsid .venv/bin/python3 scripts/run_multi_orchestrator.py > runtime/orchestrator.log 2>&1 < /dev/null &
```

### 재기동 후 확인
- `runtime/orchestrator_status.json` 갱신 여부
- 주요 worker `alive=true` 여부
- 최근 경보 재발 여부
- pause 상태 유지 여부 확인

---

## 4. RECON_FAIL 대응
1. open orders 확인
2. fills 재동기화
3. balances 재조회
4. positions 재계산
5. reconciliation OK 연속 충족 확인
6. 충족 전 자동 resume 금지

---

## 5. Discord 채널 사용 규칙
### `#finance-alerts`
- 장애 발생 즉시 기록
- 원인 요약
- 초기 조치

### `#finance-ops`
- 재기동 여부
- 상태 회복 여부
- worker 상태 요약

### `#finance-dev`
- 재발 방지 수정안
- 스크립트/rules 변경

### `#finance-reviews`
- 사후 회고
- 재발 방지 액션 누적

---

## 6. 수동 개입 필수 조건
- `RECON_FAIL`
- `DAILY_LOSS`
- 동일 장애 반복
- 운영 상태 불명확

---

## 7. 한 줄 결론
finance 조직은 장애 시
**alerts에서 감지하고, ops에서 복구 확인하고, dev에서 수정하고, reviews에서 재발 방지를 남기는 구조**로 움직여야 한다.
