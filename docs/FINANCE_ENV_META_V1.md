# FINANCE_ENV_META_V1.md

## 목적
finance 조직이 운영하는 `AI_invest` 프로젝트의
환경/경로/실행 기준을 Jarvis가 일관되게 추적할 수 있도록 메타 정보를 정리한다.

---

## 1. 프로젝트 식별
- 조직: `finance`
- 프로젝트명(현재): `AI_invest`
- 권장 운영명: `ai-invest`
- 로컬 경로: `/Users/kdh/.openclaw/workspace/projects/AI_invest`
- 원격 저장소: `https://github.com/summit1123/Ai_invest`

---

## 2. 런타임
- 주 런타임: Python
- 권장 가상환경 이름: `.venv`
- 가상환경 위치: `/Users/kdh/.openclaw/workspace/projects/AI_invest/.venv`
- 전역 Python 의존성 사용: 금지

---

## 3. 환경 파일
- 실제 실행용 env: `/Users/kdh/.openclaw/workspace/projects/AI_invest/.env`
- 샘플 env: `/Users/kdh/.openclaw/workspace/projects/AI_invest/.env.example`

원칙:
- 실제 비밀값은 `.env`
- `.env`는 로컬 전용
- git commit/push 금지

---

## 4. 데이터/스토리지
- DB: PostgreSQL
- DB 이름: `ai_invest`
- role: `summit`
- DSN은 `.env`에서 관리
- 선택 확장: `pgvector`

---

## 5. 주요 실행 기준
### 설치/환경
- Postgres 서비스 필요
- Python 의존성은 `.venv` 안에 설치

### 주요 실행 명령(목표 기준)
- orchestrator: `.venv/bin/python scripts/run_multi_orchestrator.py`
- DB 체크: `.venv/bin/python scripts/check_postgres_local.py`
- gate 체크: `.venv/bin/python scripts/check_gate_blocking.py --hours 24 --limit 40000`

---

## 6. 주요 운영 채널
### Telegram
- 실시간 운영 알림

### Discord
- `finance-ops`
- `finance-alerts`
- `finance-reviews`
- `finance-dev`

---

## 7. 현재 구현 상태
### 완료
- Discord alerts webhook 준비
- `PAUSE` / `RECON_FAIL` / `RESUME` 알림 경로 준비
- Postgres 설치 및 DB/스키마 준비

### 진행 중
- `.venv` 생성 및 Python 의존성 설치
- runtime 실연결 점검
- 결함 탐지 루프 시작

---

## 8. Jarvis 운영 메모
Jarvis는 finance 조직을 아래처럼 인지한다.
- finance는 실거래 운영 안정화 조직
- 역할은 감시/로그분석/개선/복구/코드수정
- 무단 실행권 확대가 아니라 안정화와 고도화가 핵심

---

## 9. 한 줄 결론
`AI_invest`는 finance 조직의 1호 운영 시스템이며,
Jarvis는 이 프로젝트를 **DB/런타임/알림/가상환경이 분리된 안정화 대상 시스템**으로 추적한다.
