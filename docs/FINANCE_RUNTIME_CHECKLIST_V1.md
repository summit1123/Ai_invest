# FINANCE_RUNTIME_CHECKLIST_V1.md

## 목적
finance 조직이 `AI_invest`를 실제로 실행/점검/복구할 때 따라야 하는 최소 운영 체크리스트.

---

## 1. 실행 전 체크
### 환경
- `.env` 존재 여부
- 필수 시크릿 존재 여부
- Discord webhook / Telegram bot 설정 여부
- DB 연결 가능 여부

### 설정
- `rules.yaml` 확인
- `universe.mode` 확인 (`paper` / `live`)
- 리스크 한도 확인
- notification dedupe 설정 확인

### 파일
- `runtime/` 경로 존재 여부
- `runtime/orchestrator_status.json` 경로 확인
- log 출력 경로 확인

---

## 2. 실행 체크
### 오케스트레이터
- `scripts/run_multi_orchestrator.py` 실행 가능 여부
- 프로세스 정상 기동 여부
- 상태 파일 갱신 여부

### API
- `/healthz`
- `/api/v1/ops/status`
- `/api/v1/ops/why-paused`

### 알림
- Telegram 테스트
- Discord alerts 테스트

---

## 3. 운영 중 체크
### `#finance-ops`
- orchestrator alive
- worker restart 이상 여부
- 루프 지연 여부

### `#finance-alerts`
- `PAUSE`
- `RECON_FAIL`
- `ORDER_REJECTED`
- rate limit / daily loss

### `#finance-reviews`
- daily review 도착 여부
- weekly review 도착 여부

---

## 4. 장애 시 체크
1. 현재 pause 상태인지
2. orchestrator dead인지
3. recon fail인지
4. 단순 알림 실패인지
5. 재기동 전 원인 파악했는지

---

## 5. 재기동 후 체크
- 상태 파일 재갱신
- worker alive 복구
- alerts 재발 여부
- pause 유지/해제 상태
- review 루프 정상 여부

---

## 6. 일일 마감 체크
- 당일 손익 확인
- 수수료 확인
- 주요 장애/경보 정리
- 개선 액션 1~3개 생성

---

## 7. 한 줄 결론
finance 조직은 실행보다 먼저 체크리스트를 따르고,
장애 시에는 재기동보다 원인 확인을 우선해야 한다.
