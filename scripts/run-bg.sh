#!/usr/bin/env bash
set -uo pipefail

# Background wrapper for three_step engine.
# Runs the pipeline in a detached subshell, logs to /tmp, writes a
# notification JSON when done.
#
# Usage:
#   ./scripts/run-bg.sh owner/repo#123
#   ./scripts/run-bg.sh owner/repo#123 --budget 3.00 --model opus
#
# Prints the PID and log path, then returns immediately.

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ACTIVE_PIDS="/tmp/sw-active-pids.txt"

# ── Validate ────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <owner/repo#NNN> [--budget N] [--model M] [--repo-path P]" >&2
    exit 1
fi

ISSUE_REF="$1"

# Derive a short label for the notification file (e.g. "repo-123")
LABEL="${ISSUE_REF##*/}"        # repo#123
LABEL="${LABEL//#/-}"           # repo-123

# Model from args (fallback to "default")
MODEL="default"
PREV=""
for ARG in "$@"; do
    if [[ "$PREV" == "--model" ]]; then
        MODEL="$ARG"
        break
    fi
    PREV="$ARG"
done

# Pre-determine the log path so the caller and subshell agree
RUN_TAG="$(date +%s)-${RANDOM}"
LOG="/tmp/sw-run-${RUN_TAG}.log"

# Capture all args into an array before entering the subshell
ARGS=("$@")

# ── Launch background subshell ──────────────────────────────────────
(
    # Source API keys
    # shellcheck disable=SC1090
    [[ -f "$HOME/.env" ]] && set -a && source "$HOME/.env" && set +a

    export PYTHONPATH="$REPO_ROOT"

    # Get the subshell's actual PID for tracking
    MY_PID="$BASHPID"
    echo "$MY_PID" >> "$ACTIVE_PIDS"

    # Run the pipeline, capturing everything
    python3 -m engines.three_step "${ARGS[@]}" > "$LOG" 2>&1
    EXIT_CODE=$?

    # ── Parse the summary block from the log ────────────────────────
    STATUS="$(grep -m1 '^\s*Status:' "$LOG"  | sed 's/.*Status:\s*//'  | xargs)"
    COST="$(grep -m1 '^\s*Cost:' "$LOG"      | sed 's/.*Cost:\s*//'    | xargs)"
    PR_URL="$(grep -m1 '^\s*PR:' "$LOG"      | sed 's/.*PR:\s*//'      | xargs)"
    ERROR="$(grep -m1 '^\s*Error:' "$LOG"    | sed 's/.*Error:\s*//'   | xargs)"
    RUN_ID="$(grep -m1 '^\s*Run ID:' "$LOG"  | sed 's/.*Run ID:\s*//'  | xargs)"

    FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    TIMESTAMP="$(date +%s)"

    # ── Write notification JSON ─────────────────────────────────────
    NOTIFY="/tmp/sw-notify-${TIMESTAMP}-${MODEL}.json"
    cat > "$NOTIFY" <<ENDJSON
{
  "label": "$LABEL",
  "status": "${STATUS:-unknown}",
  "exit_code": $EXIT_CODE,
  "cost": "${COST:-\$0.0000}",
  "pr_url": "${PR_URL:-}",
  "error": "${ERROR:-}",
  "run_id": "${RUN_ID:-}",
  "log_path": "$LOG",
  "finished_at": "$FINISHED_AT"
}
ENDJSON

    # ── Remove this PID from active list ────────────────────────────
    if [[ -f "$ACTIVE_PIDS" ]]; then
        grep -v "^${MY_PID}\$" "$ACTIVE_PIDS" > "${ACTIVE_PIDS}.tmp" 2>/dev/null || true
        mv "${ACTIVE_PIDS}.tmp" "$ACTIVE_PIDS"
    fi

) </dev/null &>/dev/null &
disown

BG_PID=$!

echo "PID:  $BG_PID"
echo "Log:  $LOG"
echo "Label: $LABEL"
