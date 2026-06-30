#!/usr/bin/env bash
set -uo pipefail

# Poll memory-bank/issues/TASK-*.md files for tasks where status=open and
# assignee=pipeline, then run the three-step engine against each eligible task.
#
# Usage:
#   ./scripts/poll-tasks.sh              # continuous loop
#   ./scripts/poll-tasks.sh --once       # single poll cycle, then exit
#
# Environment:
#   SW_PA_ROOT     path to PA repo (default: ~/pa/main)
#   SW_BUDGET      budget per task (default: 3.00)
#   SW_INTERVAL    sleep seconds between polls in loop mode (default: 600)

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK_FILE="/tmp/sw-poll-tasks.lock"
LOG_DIR="$REPO_ROOT/runs"
DATE_TAG="$(date +%Y-%m-%d)"
LOG_FILE="$LOG_DIR/poll-tasks-${DATE_TAG}.log"

PA_ROOT="${SW_PA_ROOT:-$HOME/pa/main}"
BUDGET="${SW_BUDGET:-3.00}"
INTERVAL="${SW_INTERVAL:-600}"
ONCE=false

[[ "${1:-}" == "--once" ]] && ONCE=true

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

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

# shellcheck disable=SC1090
[[ -f "$HOME/.env" ]] && set -a && source "$HOME/.env" && set +a

mkdir -p "$LOG_DIR"

# Extract a scalar value from YAML frontmatter (between --- delimiters).
# Usage: frontmatter_get <file> <key>
frontmatter_get() {
    local file="$1" key="$2"
    # Read only between the first two --- lines, extract key: value
    awk '/^---/{f=!f; next} f && /^'"$key"':/{gsub(/^[^:]+:[[:space:]]*/,""); print; exit}' "$file"
}

poll_once() {
    local found=0 skipped=0 processed=0

    log "Pulling latest PA repo at $PA_ROOT..."
    if ! git -C "$PA_ROOT" pull --ff-only >> "$LOG_FILE" 2>&1; then
        log "WARNING: git pull failed — proceeding with stale task files"
    fi

    ISSUES_DIR="$PA_ROOT/memory-bank/issues"
    if [[ ! -d "$ISSUES_DIR" ]]; then
        log "ERROR: issues dir not found: $ISSUES_DIR"
        return 1
    fi

    shopt -s nullglob
    TASK_FILES=("$ISSUES_DIR"/TASK-*.md)
    shopt -u nullglob

    log "Found ${#TASK_FILES[@]} task file(s) in $ISSUES_DIR"

    for TASK_FILE in "${TASK_FILES[@]}"; do
        TASK_ID="$(basename "$TASK_FILE" .md | grep -oE 'TASK-[0-9]+')"
        STATUS="$(frontmatter_get "$TASK_FILE" status)"
        ASSIGNEE="$(frontmatter_get "$TASK_FILE" assignee)"

        if [[ "$STATUS" != "open" ]] || [[ "$ASSIGNEE" != "pipeline" ]]; then
            log "  SKIP $TASK_ID: status=$STATUS assignee=$ASSIGNEE"
            skipped=$((skipped + 1))
            continue
        fi

        TARGET_REPO="$(frontmatter_get "$TASK_FILE" target_repo)"
        if [[ -z "$TARGET_REPO" ]] || [[ "$TARGET_REPO" == "null" ]]; then
            log "  SKIP $TASK_ID: target_repo is null — cannot run pipeline"
            skipped=$((skipped + 1))
            continue
        fi

        found=$((found + 1))
        GH_ISSUE="$(frontmatter_get "$TASK_FILE" gh_issue)"

        if [[ -z "$GH_ISSUE" ]] || [[ "$GH_ISSUE" == "null" ]]; then
            log "  $TASK_ID: gh_issue not set — exporting to GitHub via gh-sync.py..."
            GH_ISSUE="$(python3 "$PA_ROOT/scripts/gh-sync.py" --export "$TASK_ID" 2>> "$LOG_FILE")" || true
            if [[ -z "$GH_ISSUE" ]] || [[ "$GH_ISSUE" == "null" ]]; then
                log "  ERROR: gh-sync.py --export $TASK_ID returned empty gh_issue — skipping"
                skipped=$((skipped + 1))
                continue
            fi
            log "  $TASK_ID: created gh_issue=$GH_ISSUE"
        else
            log "  $TASK_ID: gh_issue=$GH_ISSUE already exists"
        fi

        ISSUE_REF="${TARGET_REPO}#${GH_ISSUE}"
        log "  $TASK_ID: starting pipeline for $ISSUE_REF budget=\$$BUDGET"

        # Mark in-progress before the run so a crash leaves a visible state
        python3 - "$TASK_FILE" "in-progress" <<'PYEOF' >> "$LOG_FILE" 2>&1
import sys, re, pathlib
f = pathlib.Path(sys.argv[1]); s = f.read_text()
s = re.sub(r'(?m)^(status:\s*).*$', r'\g<1>' + sys.argv[2], s, count=1)
f.write_text(s)
PYEOF

        cd "$REPO_ROOT"
        RUN_EXIT=0
        python3 -m engines.three_step "$ISSUE_REF" --budget "$BUDGET" >> "$LOG_FILE" 2>&1 || RUN_EXIT=$?

        log "  $TASK_ID: pipeline exit_code=$RUN_EXIT"

        # Find the most-recently modified run DB for this run
        RUN_DB="$(find "$REPO_ROOT/runs" -name "*.db" -newer "$TASK_FILE" \
            -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | awk '{print $2}')" || true

        if [[ -n "$RUN_DB" ]] && [[ -f "$RUN_DB" ]]; then
            log "  $TASK_ID: calling post-pipeline-update.py run_db=$RUN_DB"
            python3 "$PA_ROOT/scripts/post-pipeline-update.py" "$RUN_DB" "$TASK_ID" \
                >> "$LOG_FILE" 2>&1 || log "  WARNING: post-pipeline-update.py failed (non-fatal)"
        else
            log "  WARNING: no run DB found newer than task file — skipping post-pipeline-update"
        fi

        if [[ $RUN_EXIT -eq 0 ]]; then
            FINAL_STATUS="done"
        else
            FINAL_STATUS="failed"
        fi

        log "  $TASK_ID: setting status=$FINAL_STATUS"
        python3 - "$TASK_FILE" "$FINAL_STATUS" <<'PYEOF' >> "$LOG_FILE" 2>&1
import sys, re, pathlib, datetime
f = pathlib.Path(sys.argv[1]); s = f.read_text()
s = re.sub(r'(?m)^(status:\s*).*$', r'\g<1>' + sys.argv[2], s, count=1)
today = datetime.date.today().isoformat()
s = re.sub(r'(?m)^(updated:\s*).*$', r'\g<1>' + today, s, count=1)
f.write_text(s)
PYEOF

        git -C "$PA_ROOT" add "$TASK_FILE" >> "$LOG_FILE" 2>&1 || true
        git -C "$PA_ROOT" commit -m "pipeline: $TASK_ID → $FINAL_STATUS" \
            >> "$LOG_FILE" 2>&1 || log "  WARNING: git commit failed (nothing to commit?)"
        git -C "$PA_ROOT" push >> "$LOG_FILE" 2>&1 || log "  WARNING: git push failed"

        processed=$((processed + 1))
        log "  $TASK_ID: done status=$FINAL_STATUS"
        log "  ---"
    done

    log "Poll complete. eligible=$found skipped=$skipped processed=$processed"
}

acquire_lock
log "=== poll-tasks.sh started (PID $$, once=$ONCE, pa_root=$PA_ROOT) ==="

if $ONCE; then
    poll_once
else
    while true; do
        poll_once
        log "Sleeping ${INTERVAL}s..."
        sleep "$INTERVAL"
        NEW_DATE="$(date +%Y-%m-%d)"
        if [[ "$NEW_DATE" != "$DATE_TAG" ]]; then
            DATE_TAG="$NEW_DATE"
            LOG_FILE="$LOG_DIR/poll-tasks-${DATE_TAG}.log"
            log "=== Log rotated ==="
        fi
    done
fi

log "=== poll-tasks.sh finished ==="
