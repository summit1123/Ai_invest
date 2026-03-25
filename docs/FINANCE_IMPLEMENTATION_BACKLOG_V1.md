# FINANCE_IMPLEMENTATION_BACKLOG_V1.md

## 목적
finance 조직의 문서 설계를 실제 구현으로 옮기기 위한 1차 백로그.

---

## P0
### 1. Discord alerts webhook 연결
- 대상: `#finance-alerts`
- 이벤트: `PAUSE`, `RECON_FAIL`, `RESUME`
- 결과: 치명 운영 이벤트가 Discord에 즉시 남음

### 2. RESUME notification 경로 보강
- 현재 Telegram 기준 `notify_resume` 경로 확인/보강 필요
- Discord와 동일 이벤트 세트 맞추기

### 3. 운영 환경 변수 문서화
- Discord webhook env
- send toggle env
- timeout / fallback 정책

---

## P1
### 4. `#finance-ops` 상태 요약 연결
- orchestrator status snapshot
- worker restart 증가
- pause state 요약

### 5. `#finance-reviews` 일간/주간 리뷰 연결
- daily review
- weekly review
- 손실 원인 top tag

---

## P2
### 6. delivery logging 일반화
- Telegram / Discord 공통 기록 구조 정리
- 실패 분석 가능하게 개선

### 7. dev 액션아이템 생성 루프
- alerts/reviews 기반 개선 액션 자동 생성

### 8. recovery 지원 명령/스크립트 정리
- orchestrator restart
- status check
- pause explanation

---

## 한 줄 결론
지금 finance 조직의 구현 1순위는 수익 최적화가 아니라,
**Discord alerts 연결과 운영 상태 가시성 확보**다.
