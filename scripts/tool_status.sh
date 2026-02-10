#!/usr/bin/env bash
set -euo pipefail

echo "[Tool Status]"
if command -v git >/dev/null 2>&1; then
  echo "- git: $(git --version)"
else
  echo "- git: 없음"
fi

if command -v gh >/dev/null 2>&1; then
  echo "- gh: $(gh --version | head -n1)"
elif [[ -x "$HOME/.local/bin/gh" ]]; then
  echo "- gh: $($HOME/.local/bin/gh --version | head -n1) (local)"
else
  echo "- gh: 없음"
fi

if command -v psql >/dev/null 2>&1; then
  echo "- psql: $(psql --version)"
else
  echo "- psql: 없음"
fi

if command -v pg_isready >/dev/null 2>&1; then
  echo "- pg_isready: 사용 가능"
else
  echo "- pg_isready: 없음"
fi
