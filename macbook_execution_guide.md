# macbook_execution_guide.md

## 목적
이 문서는 macOS(맥북)에서 `ai_invest`를 실행하고 접속하는 표준 절차를 제공합니다.

- 대상 1: 맥북에서 로컬로 백엔드/프론트/오케스트레이터 실행
- 대상 2: 맥북에서 원격 서버(리눅스/윈도우 WSL)에 떠 있는 서비스에 접속

---

## 1) 사전 준비 (macOS)

### 1.1 필수 도구 설치
```bash
xcode-select --install
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install uv node postgresql@16
```

### 1.2 PostgreSQL 서비스 시작
```bash
brew services start postgresql@16
```

---

## 2) 프로젝트 준비

### 2.1 코드 받기
```bash
git clone <YOUR_REPO_URL> ai_invest
cd ai_invest
```

### 2.2 환경변수 파일 생성
```bash
cp .env.example .env
```

`.env`에서 최소 아래 항목은 반드시 채우세요.

- `POSTGRES_DSN`
- `OPENAI_API_KEY` (LLM 리포트/회의 고도화 필요 시)
- `UPBIT_ACCESS_KEY`, `UPBIT_SECRET_KEY` (실제 업비트 조회/거래 루프 용)

예시:
```env
POSTGRES_DSN=postgresql+psycopg://aiinvest:your_password@localhost:5432/aiinvest
APP_AUTOSTART_ORCHESTRATOR=true
```

---

## 3) DB 초기화

### 3.1 DB/계정 생성 (최초 1회)
```bash
psql postgres <<'SQL'
CREATE ROLE aiinvest WITH LOGIN PASSWORD 'your_password';
CREATE DATABASE aiinvest OWNER aiinvest;
SQL
```

이미 있으면 위 SQL은 에러가 날 수 있습니다. 그 경우 건너뛰고 다음 단계로 진행하면 됩니다.

### 3.2 Python 의존성 + 스키마 적용
```bash
uv sync --dev
uv run python scripts/init_schema_v1_1.py
uv run python scripts/check_postgres_local.py
```

`[성공]` 메시지가 나오면 DB 준비 완료입니다.

---

## 4) 실행 (로컬)

아래는 터미널 2개(또는 3개) 기준입니다.

### 터미널 A: FastAPI 백엔드
```bash
cd ai_invest
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

참고:
- 기본 설정에서 백엔드 기동 시 오케스트레이터를 자동 시작합니다.
- 자동 시작을 끄고 싶으면 `.env`에 `APP_AUTOSTART_ORCHESTRATOR=false` 설정.

### 터미널 B: React 프론트
```bash
cd ai_invest/frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

### 터미널 C: (선택) 오케스트레이터 수동 실행
자동 시작을 껐다면 수동으로 실행합니다.
```bash
cd ai_invest
uv run python scripts/run_multi_orchestrator.py
```

---

## 5) 접속 주소

- 프론트: `http://localhost:5173`
- 백엔드 헬스체크: `http://localhost:8000/healthz`
- 오케스트레이터 상태: `http://localhost:8000/api/v1/ui/orchestrator/status`

빠른 확인:
```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/api/v1/ui/orchestrator/status
```

---

## 6) 맥북에서 원격 서버에 접속하는 방법 (SSH 터널)

원격 서버에 백엔드(8000), 프론트(5173)가 이미 떠 있다면 맥북에서 아래처럼 터널링합니다.

```bash
ssh -L 8000:localhost:8000 -L 5173:localhost:5173 <USER>@<SERVER_HOST>
```

그 다음 맥북 브라우저에서:
- `http://localhost:5173`
- `http://localhost:8000/healthz`

주의:
- 서버 방화벽/보안그룹에서 SSH(22) 접근 허용 필요
- 서버 내부에서 실제 서비스가 해당 포트에 실행 중이어야 함

---

## 7) 운영 확인 포인트

- `runtime/orchestrator_status.json` 파일이 갱신되는지 확인
- `logs/orchestrator.autostart.log` 또는 수동 실행 터미널 로그 확인
- 대시보드에서 최신 Safe/AI 결정, 회의, 리서치 보고가 갱신되는지 확인

---

## 8) 종료/재시작

### 프로세스 종료
```bash
pkill -f "uvicorn app.main:app"
pkill -f "run_multi_orchestrator.py"
```

### 포트 점유 확인
```bash
lsof -i :8000
lsof -i :5173
```

---

## 9) 자주 발생하는 문제

### 9.1 `POSTGRES_DSN` 연결 실패
- `.env` DSN 값 오탈자 확인
- PostgreSQL 서비스 상태 확인: `brew services list`
- 계정/DB 존재 확인: `psql -l`

### 9.2 프론트는 열리는데 데이터가 안 보임
- 백엔드(`:8000`)가 떠 있는지 확인
- `http://localhost:8000/healthz` 응답 확인
- 프론트는 Vite 프록시로 `/api`를 `:8000`에 전달함

### 9.3 오케스트레이터가 안 도는 것 같음
- `GET /api/v1/ui/orchestrator/status` 확인
- 자동시작 비활성(`APP_AUTOSTART_ORCHESTRATOR=false`) 여부 확인
- 수동으로 `uv run python scripts/run_multi_orchestrator.py` 실행

---

## 10) 권장 실행 순서 (요약)

```bash
# 1) DB
brew services start postgresql@16
uv run python scripts/init_schema_v1_1.py

# 2) 백엔드
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 3) 프론트
cd frontend && npm install && npm run dev -- --host 0.0.0.0 --port 5173
```

완료 후 `http://localhost:5173` 접속.
