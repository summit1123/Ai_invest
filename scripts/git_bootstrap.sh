#!/usr/bin/env bash
set -euo pipefail

REMOTE_URL=${1:-https://github.com/summit1123/Ai_invest.git}

if [[ ! -d .git ]]; then
  git init
fi

git branch -M main

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

echo "[정보] origin 설정 완료: $REMOTE_URL"

if git fetch origin --quiet 2>/dev/null; then
  echo "[성공] origin fetch 성공"
else
  echo "[주의] origin fetch 실패 (인증 필요 가능성)"
  echo " - gh auth login 후 다시 실행하세요."
fi
