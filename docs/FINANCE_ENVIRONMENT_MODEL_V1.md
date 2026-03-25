# FINANCE_ENVIRONMENT_MODEL_V1.md

## 목적
finance 조직이 `AI_invest`를 이 환경에서 안정적으로 운영하기 위해,
필요한 환경 변수/시크릿/실행 경로를 구조적으로 정리한다.

---

## 1. 원칙
1. 시크릿은 코드에 하드코딩하지 않는다
2. Telegram / Discord / 거래소 키를 분리한다
3. 운영용 값과 개발용 값을 구분한다
4. 실행 전 필수 환경 검증이 가능해야 한다

---

## 2. 핵심 환경 영역
### A. 거래 실행/데이터
- Upbit API key/secret
- live/paper 모드 구분
- rules.yaml 기반 실행 설정

### B. 알림
- Telegram bot token
- Telegram chat ids
- Discord webhook URLs

### C. 운영 API
- `OPS_READ_API_KEY`
- status path 관련 env

### D. 런타임
- orchestrator 실행 경로
- log 파일 경로
- runtime status 파일 경로

---

## 3. finance 조직 기준 주요 환경 변수
### Telegram
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID_OPS`
- `TELEGRAM_CHAT_ID_TRADING`
- `TELEGRAM_CHAT_ID_REVIEW`
- `TELEGRAM_CHAT_ID_RESEARCH`
- `TELEGRAM_CHAT_ID_MEETING`
- `TELEGRAM_CHAT_ID_ENGINEERING`

### Discord
- `DISCORD_WEBHOOK_FINANCE_ALERTS`
- `DISCORD_WEBHOOK_FINANCE_OPS` (후속)
- `DISCORD_WEBHOOK_FINANCE_REVIEWS` (후속)

### Notification control
- `SEND_TELEGRAM`
- `NOTIFICATION_DEDUPE_WITHIN_SEC`
- `NOTIFY_SAFE_DECISION_ENABLED`
- `NOTIFY_SAFE_DECISION_HOLD`
- `NOTIFY_SAFE_DECISION_CHANGE_ONLY`
- `SEND_DISCORD` (추가 예정)

### Ops API
- `OPS_READ_API_KEY`
- `ORCHESTRATOR_STATUS_PATH`

---

## 4. 권장 운영 방식
### paper 우선
- 기본은 `paper` 모드
- live는 별도 승인/검증 후

### env 검증 우선
실행 전 항상 확인:
- 필수 키 존재 여부
- status path 존재 여부
- DB 연결 가능 여부
- Telegram/Discord 테스트 가능 여부

### 시크릿 저장
- `.env` 또는 별도 비밀 저장소
- git tracked file에는 절대 저장 금지

---

## 5. 다음 구현 연결점
이 문서는 이후 아래와 연결된다.
- `discord_client.py`
- notification service 확장
- ops health check
- preflight script

---

## 6. 한 줄 결론
finance 조직의 환경 모델은
**거래 / 알림 / 운영 API / 런타임**을 분리하고,
모든 시크릿을 코드 밖에서 관리하는 구조로 고정해야 한다.
