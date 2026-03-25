# FINANCE_PREFLIGHT_MAC_MINI_V1.md

## 목적
이 Mac mini 환경에서 `AI_invest`를 우선 `paper` 모드로 안정적으로 실행하기 위한 preflight 점검 기준.

---

## 1. 기본 원칙
1. 첫 실행은 `paper` 모드 기준으로 한다
2. live 전환은 별도 승인 후 진행한다
3. 실행 전 env / DB / 알림 / 런타임 경로를 모두 확인한다
4. preflight를 통과하지 못하면 실행보다 먼저 수정한다

---

## 2. 필수 확인 파일
- `.env`
- `rules.yaml`
- `runtime/`
- `scripts/check_env.sh`
- `scripts/check_postgres_local.py`
- `scripts/tool_status.sh`
- `scripts/check_gate_blocking.py`

---

## 3. env 기준
### `.env.example` 기준 핵심 필드
#### 필수에 가까운 것
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

#### paper 기준 추가 권장
- `PAPER_TRADING=true`
- `ENABLE_LIVE_TRADING=false`
- `ORCHESTRATOR_STATUS_PATH`
- `OPS_READ_API_KEY`

#### finance Discord 준비용
- `DISCORD_WEBHOOK_FINANCE_ALERTS` (구현 후 사용)

---

## 4. 실행 전 점검 순서
### Step 1. 도구 상태
```bash
cd /Users/kdh/.openclaw/workspace/projects/AI_invest
bash scripts/tool_status.sh
```

확인 목표:
- git 존재
- gh 존재
- psql 존재
- pg_isready 가능 여부

### Step 2. env 최소 점검
```bash
cd /Users/kdh/.openclaw/workspace/projects/AI_invest
bash scripts/check_env.sh
```

주의:
- 현재 스크립트는 paper 전용 최소 모드라기보다,
  OpenAI / Telegram / Upbit key까지 모두 요구한다
- 즉, paper라도 운영 기준 필수 env는 상당히 넓다

### Step 3. Postgres 확인
```bash
cd /Users/kdh/.openclaw/workspace/projects/AI_invest
uv run python scripts/check_postgres_local.py
```

확인 목표:
- DB 연결 성공
- 현재 user / db 확인
- pgvector 필요 여부 확인

### Step 4. 규칙 과차단 점검
```bash
cd /Users/kdh/.openclaw/workspace/projects/AI_invest
uv run python scripts/check_gate_blocking.py --hours 24 --limit 40000
```

확인 목표:
- HOLD 비율 과도 여부
- gate 차단 원인 상위 항목 확인

### Step 5. runtime 경로 확인
- `runtime/` 디렉토리 존재
- `runtime/orchestrator_status.json` 생성 가능 여부
- 로그 파일 기록 가능 여부

---

## 5. 첫 실행 기준
### 권장
- `paper` 모드
- orchestrator 단독 실행
- alerts는 Telegram 우선
- Discord는 구현 후 병행

예시:
```bash
cd /Users/kdh/.openclaw/workspace/projects/AI_invest
.venv/bin/python3 scripts/run_multi_orchestrator.py
```

---

## 6. 실패 시 우선순위
1. `.env` 누락 해결
2. Postgres 연결 해결
3. runtime/status path 해결
4. 알림 경로 해결
5. gate over-blocking 여부 확인

---

## 7. 현재 판단
이 시스템은 단순 샘플이 아니라,
OpenAI / Postgres / Telegram / Upbit / runtime status가 함께 맞물리는 구조다.
따라서 preflight 없이 바로 실행하는 방식은 위험하다.

---

## 8. 한 줄 결론
이 Mac mini에서 finance 조직 기준 `AI_invest`를 돌리려면,
먼저 **env / DB / runtime / alert / gate 상태를 통과시키는 preflight 절차**를 반드시 거쳐야 한다.
