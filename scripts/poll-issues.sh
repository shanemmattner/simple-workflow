#!/usr/bin/env bash
set -uo pipefail

# Poll GitHub for issues labeled `sw:auto` and process them through the
# three-step engine. Designed to run via launchd (--once) or as a
# continuous loop (default).
#
# Usage:
#   ./scripts/poll-issues.sh              # continuous loop, 5-min sleep
#   ./scripts/poll-issues.sh --once       # single poll cycle, then exit
#
# Environment:
#   SW_ORGS        comma-separated orgs to poll (default: shanemmattner,personal-assistant-system)
#   SW_BUDGET      budget per issue (default: 3.00)
#   SW_INTERVAL    sleep seconds between polls in loop mode (default: 300)

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK_FILE="/tmp/sw-poll-issues.lock"
LOG_DIR="$REPO_ROOT/runs"
DATE_TAG="$(date +%Y-%m-%d)"
LOG_FILE="$LOG_DIR/poll-${DATE_TAG}.log"

ORGS="${SW_ORGS:-shanemmattner,personal-assistant-system}"
BUDGET="${SW_BUDGET:-3.00}"
INTERVAL="${SW_INTERVAL:-300}"
ONCE=false

[[ "${1:-}" == "--once" ]] && ONCE=true

# ── Logging ────────────────────────────────────────────────────────────
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# ── Lock file ──────────────────────────────────────────────────────────
acquire_lock() {
    if [[ -f "$LOCK_FILE" ]]; then
        EXISTING_PID="$(cat "$LOCK_FILE" 2>/dev/null)"
        if [[ -n "$EXISTING_PID" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
            log "Another instance running (PID $EXISTING_PID). Exiting."
            exit 0
        fi
        log "Stale lock file (PID $EXISTING_PID not running). Removing."
        rm -f "$LOCK_FILE"
    fi
    echo $$ > "$LOCK_FILE"
}

release_lock() {
    rm -f "$LOCK_FILE"
}
trap release_lock EXIT

# ── Source env ─────────────────────────────────────────────────────────
# shellcheck disable=SC1090
[[ -f "$HOME/.env" ]] && set -a && source "$HOME/.env" && set +a

# ── Ensure log dir exists ─────────────────────────────────────────────
mkdir -p "$LOG_DIR"

# ── Poll cycle ─────────────────────────────────────────────────────────
poll_once() {
    local found=0

    IFS=',' read -ra ORG_LIST <<< "$ORGS"
    for ORG in "${ORG_LIST[@]}"; do
        ORG="$(echo "$ORG" | xargs)"  # trim whitespace

        log "Searching org=$ORG for issues with label sw:auto..."

        # List repos in the org, then search each for sw:auto issues
        # gh search issues is simpler and searches across repos
        ISSUES="$(gh search issues \
            --label "sw:auto" \
            --owner "$ORG" \
            --state open \
            --json repository,number,title \
            --jq '.[] | select(.repository.name != null) | "\(.repository.owner.login)/\(.repository.name)#\(.number)\t\(.title)"' \
            2>/dev/null)" || true

        if [[ -z "$ISSUES" ]]; then
            log "  No sw:auto issues found in $ORG."
            continue
        fi

        while IFS=$'\t' read -r ISSUE_REF TITLE; do
            [[ -z "$ISSUE_REF" ]] && continue
            found=$((found + 1))

            # Extract owner/repo and number
            REPO_SLUG="${ISSUE_REF%#*}"
            ISSUE_NUM="${ISSUE_REF##*#}"

            # Check if already processed (has sw:done or sw:failed)
            LABELS="$(gh issue view "$ISSUE_NUM" --repo "$REPO_SLUG" --json labels --jq '.labels[].name' 2>/dev/null)" || true
            if echo "$LABELS" | grep -qE '^sw:(done|failed)$'; then
                log "  Skipping $ISSUE_REF (already has sw:done or sw:failed)"
                continue
            fi

            log "  Processing: $ISSUE_REF — $TITLE"
            log "  Budget: \$$BUDGET"

            # Run the three-step engine
            cd "$REPO_ROOT"
            if python3 -m engines.three_step "$ISSUE_REF" --budget "$BUDGET" >> "$LOG_FILE" 2>&1; then
                log "  SUCCESS: $ISSUE_REF"
                # Remove sw:auto, add sw:done
                gh issue edit "$ISSUE_NUM" --repo "$REPO_SLUG" \
                    --remove-label "sw:auto" --add-label "sw:done" 2>/dev/null || \
                    log "  WARNING: failed to update labels on $ISSUE_REF"
            else
                EXIT_CODE=$?
                log "  FAILED: $ISSUE_REF (exit code $EXIT_CODE)"
                # Remove sw:auto, add sw:failed
                gh issue edit "$ISSUE_NUM" --repo "$REPO_SLUG" \
                    --remove-label "sw:auto" --add-label "sw:failed" 2>/dev/null || \
                    log "  WARNING: failed to update labels on $ISSUE_REF"
            fi

            log "  ---"
        done <<< "$ISSUES"
    done

    log "Poll complete. $found issue(s) found."
}

# ── Main ───────────────────────────────────────────────────────────────
acquire_lock
log "=== poll-issues.sh started (PID $$, once=$ONCE) ==="

if $ONCE; then
    poll_once
else
    while true; do
        poll_once
        log "Sleeping ${INTERVAL}s..."
        sleep "$INTERVAL"
        # Rotate log file at midnight
        NEW_DATE="$(date +%Y-%m-%d)"
        if [[ "$NEW_DATE" != "$DATE_TAG" ]]; then
            DATE_TAG="$NEW_DATE"
            LOG_FILE="$LOG_DIR/poll-${DATE_TAG}.log"
            log "=== Log rotated ==="
        fi
    done
fi

log "=== poll-issues.sh finished ==="
