#!/usr/bin/env bash
set -euo pipefail

# Bootstraps a local Postgres (WSL/Linux) matching POSTGRES_DSN in .env:
# - installs postgres + pgvector (best-effort)
# - starts service
# - creates role/db if missing
# - enables pgvector extension in target db
#
# Notes:
# - This script uses sudo and will prompt for your password.
# - It reads POSTGRES_DSN from .env; it does NOT print the password.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[실패] .env 파일이 없습니다: ${ENV_FILE}"
  exit 1
fi

read_dsn_py=$(
  cat <<'PY'
from __future__ import annotations
from pathlib import Path
from urllib.parse import urlparse

env = {}
for line in Path(".env").read_text(encoding="utf-8", errors="replace").splitlines():
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    env[k.strip()] = v.strip()

dsn = env.get("POSTGRES_DSN", "")
if not dsn:
    raise SystemExit("POSTGRES_DSN missing")
if dsn.startswith("postgresql+psycopg://"):
    dsn = "postgresql://" + dsn[len("postgresql+psycopg://") :]

u = urlparse(dsn)
user = u.username or ""
pw = u.password or ""
host = u.hostname or ""
port = str(u.port or 5432)
db = (u.path or "").lstrip("/")

if not user or not db:
    raise SystemExit("POSTGRES_DSN must include user and db name")

print(user)
print(pw)
print(host)
print(port)
print(db)
PY
)

pushd "${ROOT_DIR}" >/dev/null
mapfile -t dsn_parts < <(python3 -c "${read_dsn_py}")
popd >/dev/null

PG_USER="${dsn_parts[0]}"
PG_PASS="${dsn_parts[1]}"
PG_HOST="${dsn_parts[2]}"
PG_PORT="${dsn_parts[3]}"
PG_DB="${dsn_parts[4]}"

if [[ "${PG_HOST}" != "localhost" && "${PG_HOST}" != "127.0.0.1" && "${PG_HOST}" != "" ]]; then
  echo "[실패] 이 스크립트는 로컬 Postgres만 부트스트랩합니다. host=${PG_HOST}"
  exit 2
fi
if [[ "${PG_PORT}" != "5432" ]]; then
  echo "[경고] POSTGRES_DSN port가 5432가 아닙니다: port=${PG_PORT}"
fi
if [[ -z "${PG_PASS}" ]]; then
  echo "[경고] POSTGRES_DSN에 비밀번호가 없습니다. (role 생성은 가능하지만 TCP 접속이 실패할 수 있습니다)"
fi

if ! command -v sudo >/dev/null 2>&1; then
  echo "[실패] sudo가 필요합니다."
  exit 3
fi

echo "[1/4] 패키지 설치 (postgres + pgvector)"
sudo apt-get update -y
sudo apt-get install -y postgresql postgresql-contrib

# pgvector package name is versioned on Ubuntu; try common versions.
vector_installed="no"
for v in 16 15 14 13; do
  if sudo apt-get install -y "postgresql-${v}-pgvector" >/dev/null 2>&1; then
    vector_installed="yes"
    break
  fi
done
if [[ "${vector_installed}" != "yes" ]]; then
  echo "[경고] pgvector 패키지 설치를 건너뜁니다(레포지토리/버전 문제일 수 있음). 이후 SQL로 확장 활성화가 실패할 수 있습니다."
else
  echo "[정보] pgvector 패키지 설치 완료"
fi

echo "[2/4] Postgres 서비스 시작"
if command -v systemctl >/dev/null 2>&1; then
  # systemd가 활성화된 환경이면 이 경로가 가장 안정적
  sudo systemctl enable --now postgresql || true
fi
sudo service postgresql start || true

echo "[3/4] role/db 생성 (idempotent)"
sql_escape_literal() {
  # Escape a string for a single-quoted SQL literal.
  # shellcheck disable=SC2001
  printf "%s" "$1" | sed "s/'/''/g"
}

role_exists="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$(sql_escape_literal "${PG_USER}")'" || true)"
if [[ -z "${role_exists//[[:space:]]/}" ]]; then
  sudo -u postgres createuser --login "${PG_USER}"
fi

escaped_pw="$(sql_escape_literal "${PG_PASS}")"
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER ROLE \"${PG_USER}\" WITH PASSWORD '${escaped_pw}';" >/dev/null

db_exists="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$(sql_escape_literal "${PG_DB}")'" || true)"
if [[ -z "${db_exists//[[:space:]]/}" ]]; then
  sudo -u postgres createdb -O "${PG_USER}" "${PG_DB}"
fi

echo "[4/4] pgvector 확장 활성화"
printf "CREATE EXTENSION IF NOT EXISTS vector;\n" | sudo -u postgres psql -d "${PG_DB}" -v ON_ERROR_STOP=1 >/dev/null || {
  echo "[경고] vector 확장 활성화 실패. pgvector 패키지 설치/버전을 확인하세요."
}

echo "[완료] 로컬 Postgres 부트스트랩 완료. 이제 아래를 실행하세요:"
echo "  python3 scripts/check_postgres_local.py"
echo "  python3 scripts/smoke_tests.py"
