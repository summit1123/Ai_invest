#!/usr/bin/env bash
set -euo pipefail

if command -v gh >/dev/null 2>&1; then
  GH_BIN=$(command -v gh)
elif [[ -x "$HOME/.local/bin/gh" ]]; then
  GH_BIN="$HOME/.local/bin/gh"
else
  echo "[실패] gh 명령을 찾지 못했습니다."
  exit 1
fi

"$GH_BIN" auth login --git-protocol https --web
"$GH_BIN" auth status
