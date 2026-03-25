# FINANCE_DISCORD_ADAPTER_DESIGN_V1.md

## 목적
`AI_invest`의 기존 Telegram 중심 알림 구조를 깨지 않고,
Finance Discord 서버를 보조 운영 채널로 붙이기 위한 1차 어댑터 설계 문서.

우선 범위:
- `PAUSE`
- `RECON_FAIL`
- `RESUME`
를 `#finance-alerts`로 보내는 최소 구조

---

## 1. 설계 원칙
1. 기존 Telegram 알림은 유지한다
2. Discord는 finance 조직의 운영 콘솔로 추가한다
3. 처음엔 Discord Bot보다 **Webhook** 기반이 단순하고 안정적이다
4. 알림 실패가 거래 실행을 막으면 안 된다
5. delivery 결과는 가능한 한 저장/추적 가능해야 한다

---

## 2. 제안 구조
### 현재
- `NotificationService`
  - Telegram 전송
  - delivery logging
  - template render

### 추가
- `ai_invest/notifications/discord_client.py`
  - webhook 전송 전담

- `NotificationService._deliver_discord_webhook(...)`
  - Discord webhook 전송 래퍼

- finance 전용 webhook env
  - `DISCORD_WEBHOOK_FINANCE_ALERTS`

---

## 3. 환경 변수
### 필수(초기)
- `DISCORD_WEBHOOK_FINANCE_ALERTS`

### 선택(후속)
- `SEND_DISCORD`
- `DISCORD_NOTIFY_FINANCE_ALERTS_ENABLED`
- `DISCORD_TIMEOUT_SEC`

초기 버전은 단순하게:
- webhook URL이 있으면 전송
- 없으면 skip

---

## 4. 연결 방식
### notify_pause
- Telegram 유지
- Discord `#finance-alerts` 추가 전송

### notify_recon_fail
- Telegram 유지
- Discord `#finance-alerts` 추가 전송

### notify_resume
- 현재 구조상 별도 notify 함수 추가 필요
- 이후 동일하게 Discord `#finance-alerts` 전송

---

## 5. 실패 처리 원칙
- Discord 전송 실패는 거래 루프를 중단시키지 않는다
- 실패는 로깅만 하고 넘어간다
- Telegram 성공/실패와 Discord 성공/실패는 분리 기록한다

---

## 6. 단계별 구현 순서
### 1단계
- `discord_client.py` 추가
- webhook POST 최소 구현
- `notify_pause`, `notify_recon_fail`에서 Discord 병행 전송

### 2단계
- `notify_resume` 추가
- Discord/Telegram 공통 전송 구조 정리

### 3단계
- `#finance-reviews`, `#finance-ops`용 webhook 확장
- delivery logging 구조 일반화

---

## 7. 왜 webhook부터 가는가
- 서버에 `jarvis`/봇이 이미 있어도,
  채널별 권한/이벤트/봇 복잡도를 바로 올리지 않는 편이 좋다
- webhook은 채널 단위로 빠르게 붙일 수 있다
- finance 조직 초기 안정화 단계에 적합하다

---

## 8. 한 줄 결론
Finance Discord 알림은 초기에는 **Webhook 기반 보조 어댑터**로 붙이고,
Telegram을 유지한 채 `PAUSE / RECON_FAIL / RESUME`부터 단계적으로 확장하는 것이 가장 안전하다.
