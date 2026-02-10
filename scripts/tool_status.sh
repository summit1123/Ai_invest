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

if command -v docker >/dev/null 2>&1; then
  echo "- docker: $(docker --version)"
elif [[ -x "$HOME/.local/bin/docker" ]]; then
  echo "- docker: $($HOME/.local/bin/docker --version) (local)"
else
  echo "- docker: 없음"
fi
