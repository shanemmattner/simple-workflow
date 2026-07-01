#!/usr/bin/env bash
# Wrapper for promptfoo exec provider — pipes the prompt to `claude -p`.
# Usage: run-claude.sh <prompt_text>
# promptfoo passes the rendered prompt as the first argument.
set -euo pipefail

PROMPT="$1"
MODEL="${PROMPTFOO_MODEL:-sonnet}"

claude -p --model "$MODEL" --output-format stream-json --max-turns 1 <<< "$PROMPT" 2>/dev/null \
  | grep '"type":"result"' \
  | python3 -c "import sys,json; print(json.loads(sys.stdin.readline()).get('result',''))"
