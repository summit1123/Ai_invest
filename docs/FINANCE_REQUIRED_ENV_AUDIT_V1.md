# FINANCE_REQUIRED_ENV_AUDIT_V1.md

## 목적
현재 `AI_invest`가 paper 모드 기준으로도 실제 실행 전에 어떤 환경값을 요구하는지 정리한다.

---

## 1. 현재 `.env.example` 기준 주요 범주
- OpenAI
- Upbit
- Postgres
- Telegram
- Slack
- Vector DB
- GitHub
- runtime/reporting

---

## 2. 현재 `scripts/check_env.sh` 기준 필수값
- `OPENAI_API_KEY`
- `POSTGRES_DSN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID_OPS`
- `TELEGRAM_CHAT_ID_TRADING`
- `TELEGRAM_CHAT_ID_REVIEW`
- `TELEGRAM_CHAT_ID_RESEARCH`
- `TELEGRAM_CHAT_ID_MEETING`
- `TELEGRAM_CHAT_ID_ENGINEERING`
- `UPBIT_ACCESS_KEY`
- `UPBIT_SECRET_KEY`

즉,
현재 check script 기준으로는 paper 모드여도
OpenAI / Telegram / Upbit / Postgres가 모두 준비되어야 한다.

---

## 3. 실무적 해석
### 당장 필요한 것
- `.env` 실제 존재
- Postgres 연결
- Telegram 운영 알림
- Upbit 키

### 후속 확장
- Discord webhook
- Slack
- Vector store
- GitHub automation env

---

## 4. 개선 후보
향후에는 `check_env.sh`를 아래처럼 분리하는 것이 좋다.
- `paper` 최소 실행용 필수값
- `live` 필수값
- `notification` 선택값
- `governance/research` 확장값

---

## 5. 한 줄 결론
현재 기준으로 `AI_invest`는 paper 모드라도 필수 env 요구 범위가 넓으므로,
실행 전에 어떤 값이 이미 있고 어떤 값이 없는지 audit부터 해야 한다.
