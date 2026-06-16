#!/usr/bin/env bash
set -euo pipefail
# Usage: ./scripts/run.sh owner/repo#123 [--budget 2.00] [--model opus]
cd "$(dirname "$0")/.."
python3 -m engine.orchestrator "$@"
