#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ $# -lt 1 ]]; then
  echo "Usage: scripts/commit_with_report.sh \"commit message\""
  exit 1
fi

MESSAGE="$1"

echo "[commit] Running unit tests (pytest)"
uv run pytest -q

echo "[commit] Committing"
git add -A
git commit -m "$MESSAGE"

COMMIT="$(git rev-parse --short HEAD)"

echo "[commit] Sending Telegram report (engineering)"
uv run python scripts/notify_engineering_commit.py --commit "$COMMIT" --message "$MESSAGE" --tests "pytest: PASS"

echo "[done] commit=$COMMIT"

