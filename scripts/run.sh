#!/usr/bin/env bash
set -euo pipefail
# Usage: ./scripts/run.sh owner/repo#123 [--budget 2.00] [--model opus]
cd "$(dirname "$0")/.."
PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" python3 engines/github_claude/__main__.py "$@"
