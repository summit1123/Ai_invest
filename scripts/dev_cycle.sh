#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[dev-cycle] Running unit tests"
if command -v uv >/dev/null 2>&1; then
  uv run pytest -q
else
  pytest -q --capture=sys
fi

echo "[dev-cycle] Running smoke tests (DB/Telegram/Upbit)"
if command -v uv >/dev/null 2>&1; then
  uv run python scripts/smoke_tests.py || true
else
  python3 scripts/smoke_tests.py || true
fi

echo "[dev-cycle] Git status"
git status --short --branch
