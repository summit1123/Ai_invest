#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f .env ]]; then
  echo "[실패] .env 파일이 없습니다."
  exit 1
fi

required=(
  OPENAI_API_KEY
  POSTGRES_DSN
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID_OPS
  TELEGRAM_CHAT_ID_TRADING
  TELEGRAM_CHAT_ID_REVIEW
  TELEGRAM_CHAT_ID_RESEARCH
  TELEGRAM_CHAT_ID_MEETING
  TELEGRAM_CHAT_ID_ENGINEERING
  UPBIT_ACCESS_KEY
  UPBIT_SECRET_KEY
)

missing=()
for key in "${required[@]}"; do
  value=$(grep -E "^${key}=" .env | head -n1 | cut -d= -f2- | tr -d '\r')
  if [[ -z "${value}" ]]; then
    missing+=("$key")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "[실패] 필수 환경변수 누락:" 
  for m in "${missing[@]}"; do
    echo " - $m"
  done
  exit 2
fi

echo "[성공] 필수 환경변수가 모두 설정되어 있습니다."
