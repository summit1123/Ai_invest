# FINANCE_DAILY_IMPROVEMENT_LOOP_V1.md

## 목적
finance 조직이 `AI_invest`를 매일 점검하고,
문제를 누적/개선/검증하는 일일 운영 루프를 정의한다.

---

## 1. 일일 루프
### Step 1. 운영 상태 확인
채널:
- `#finance-ops`

체크 항목:
- orchestrator alive 여부
- worker restart 증가 여부
- pause 상태 여부
- 최근 오류/경보 존재 여부

### Step 2. 경보 확인
채널:
- `#finance-alerts`

체크 항목:
- `PAUSE`
- `RECON_FAIL`
- `ORDER_REJECTED`
- `DAILY_LOSS`
- rate limit 문제

### Step 3. 리뷰 정리
채널:
- `#finance-reviews`

체크 항목:
- 일간 손익
- 수수료
- 거래 수
- 최대 낙폭
- 손실 원인 태그
- 반복 패턴

### Step 4. 개선 액션 생성
채널:
- `#finance-dev`

생성 대상:
- 수정 필요한 rules
- 스크립트 개선점
- 알림 구조 개선
- 운영 자동화 개선
- 버그 수정

### Step 5. 반영 결과 확인
다음 review에서 확인:
- 문제 재발 여부
- 성능 변화
- 안정성 변화
- 잡음 감소 여부

---

## 2. 일일 출력물
매일 최소 남겨야 하는 것:
1. 운영 상태 한 줄 요약
2. 경보/이상 이벤트 목록
3. 손익/리뷰 요약
4. 개선 액션 1~3개

---

## 3. 핵심 원칙
- alerts에서 끝내지 않는다
- review는 반드시 개선 액션으로 이어져야 한다
- dev 변경은 다음 review에서 효과를 검증한다
- 일일 루프는 짧아도 구조화되어야 한다

---

## 4. 한 줄 결론
finance 조직은 매일
**상태 확인 → 경보 확인 → 리뷰 → 개선 액션 → 효과 검증**
루프로 움직여야 한다.
