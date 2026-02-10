#!/usr/bin/env bash
set -euo pipefail

if command -v docker >/dev/null 2>&1; then
  DOCKER_BIN=$(command -v docker)
elif [[ -x "$HOME/.local/bin/docker" ]]; then
  DOCKER_BIN="$HOME/.local/bin/docker"
else
  echo "[실패] docker 명령을 찾지 못했습니다."
  exit 1
fi

CONTAINER=${CONTAINER:-ai_invest_postgres}
if "$DOCKER_BIN" ps -a --format '{{.Names}}' | rg -x "$CONTAINER" >/dev/null 2>&1; then
  "$DOCKER_BIN" rm -f "$CONTAINER" >/dev/null
  echo "[성공] 컨테이너 중지/삭제: $CONTAINER"
else
  echo "[정보] 컨테이너 없음: $CONTAINER"
fi
