#!/usr/bin/env bash
set -euo pipefail

DOCKER_BIN=${DOCKER_BIN:-}
if [[ -z "$DOCKER_BIN" ]]; then
  if command -v docker >/dev/null 2>&1; then
    DOCKER_BIN=$(command -v docker)
  elif [[ -x "$HOME/.local/bin/docker" ]]; then
    DOCKER_BIN="$HOME/.local/bin/docker"
  else
    echo "[실패] docker 명령을 찾지 못했습니다."
    exit 1
  fi
fi

IMAGE=${IMAGE:-pgvector/pgvector:pg16}
CONTAINER=${CONTAINER:-ai_invest_postgres}
DB_USER=${DB_USER:-summit}
DB_PASSWORD=${DB_PASSWORD:-ehdgusdl11!}
DB_NAME=${DB_NAME:-ai_invest}
DB_PORT=${DB_PORT:-5432}

if ! "$DOCKER_BIN" info >/dev/null 2>&1; then
  echo "[실패] Docker daemon에 연결할 수 없습니다."
  echo " - Docker Desktop을 실행하고 WSL Integration을 켠 뒤 다시 실행하세요."
  exit 1
fi

if "$DOCKER_BIN" ps -a --format '{{.Names}}' | rg -x "$CONTAINER" >/dev/null 2>&1; then
  echo "[정보] 기존 컨테이너 제거: $CONTAINER"
  "$DOCKER_BIN" rm -f "$CONTAINER" >/dev/null
fi

echo "[정보] 이미지 pull: $IMAGE"
"$DOCKER_BIN" pull "$IMAGE" >/dev/null

echo "[정보] 컨테이너 시작: $CONTAINER"
"$DOCKER_BIN" run -d \
  --name "$CONTAINER" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASSWORD" \
  -e POSTGRES_DB="$DB_NAME" \
  -p "$DB_PORT:5432" \
  --health-cmd="pg_isready -U $DB_USER -d $DB_NAME" \
  --health-interval=10s \
  --health-timeout=5s \
  --health-retries=5 \
  -v ai_invest_pgdata:/var/lib/postgresql/data \
  "$IMAGE" >/dev/null

echo "[정보] 헬스체크 대기"
for i in {1..30}; do
  status=$("$DOCKER_BIN" inspect --format='{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "starting")
  if [[ "$status" == "healthy" ]]; then
    echo "[성공] postgres(pgvector) 준비 완료"
    echo " - container: $CONTAINER"
    echo " - port: $DB_PORT"
    exit 0
  fi
  sleep 2
done

echo "[실패] 컨테이너 헬스체크 타임아웃"
"$DOCKER_BIN" logs "$CONTAINER" | tail -n 50 || true
exit 2
