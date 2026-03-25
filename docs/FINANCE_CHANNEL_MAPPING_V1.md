# FINANCE_CHANNEL_MAPPING_V1.md

## 목적
AI_invest 운영 이벤트를 Telegram과 Discord 중 어디로 보내야 하는지 1차 매핑한다.

## 1. Telegram 우선
### 보내야 하는 것
- 거래/실행 실패
- 긴급 오류
- 즉시 확인 필요한 경고
- 핵심 요약 알림

이유:
실시간성 우선

---

## 2. Discord 우선
### finance-ops
- 루프 상태
- 프로세스 점검 결과
- 운영 로그 요약

### finance-alerts
- 실패 이벤트 복제본
- 오류 원인 기록
- 반복 이슈 누적

### finance-reviews
- 일간 리뷰
- 주간 리뷰
- 전략 회고
- 개선 포인트

### finance-dev
- 코드 수정 논의
- 이슈 추적
- 구현 메모

### finance-qa
- 테스트 결과
- 배포 전 체크
- 회귀 검증

---

## 3. 원칙
- Telegram = 즉시성
- Discord = 누적성
- 같은 메시지를 무작정 양쪽에 다 보내지 않는다
- finance-reviews는 Telegram보다 Discord를 기준 저장소로 둔다
