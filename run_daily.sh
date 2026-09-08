#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not installed or not on PATH" >&2
  exit 1
fi

if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null; then
  echo "Ollama server is not reachable at http://127.0.0.1:11434" >&2
  echo "Run: ollama serve" >&2
  exit 1
fi

python3 -m pip install -q -r requirements.txt
python3 scripts/daily_paper.py

git add papers/daily papers/seen.json
if ! git diff --cached --quiet; then
  git commit -m "docs: add daily paper $(date +%F)"
  git push
fi
