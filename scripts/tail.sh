#!/usr/bin/env bash
# tail.sh — live phase completion feed for the latest (or specified) run DB
# Polls every 10s, prints phase status changes, exits when run is no longer 'running'.
set -euo pipefail
cd "$(dirname "$0")/.."

RUNS_DIR="engines/github_claude/runs"
INTERVAL=10

# Accept optional substring to select a specific run DB
if [ "${1:-}" != "" ]; then
    DB=$(ls -t "$RUNS_DIR"/*.db 2>/dev/null | grep "$1" | head -1 || true)
    [ -z "$DB" ] && { echo "No DB matching '$1' in $RUNS_DIR"; exit 1; }
else
    DB=$(ls -t "$RUNS_DIR"/*.db 2>/dev/null | head -1 || true)
    [ -z "$DB" ] && { echo "No .db files found in $RUNS_DIR"; exit 1; }
fi

echo "[tail.sh] watching: $(basename "$DB")"
echo ""

# Print run header
sqlite3 -header -column "$DB" \
    "SELECT id, repo, issue_number, status, model, started_at FROM run LIMIT 1;"
echo ""

declare -A SEEN_PHASES

while true; do
    # Get current run status
    RUN_STATUS=$(sqlite3 "$DB" "SELECT status FROM run LIMIT 1;" 2>/dev/null || echo "unknown")

    # Get all phases with their current state
    while IFS='|' read -r pid phase_name status cost duration failure; do
        KEY="${pid}:${status}"
        if [ -z "${SEEN_PHASES[$KEY]:-}" ]; then
            SEEN_PHASES[$KEY]=1
            NOW=$(date '+%H:%M:%S')
            if [ "$status" = "running" ]; then
                printf "[%s] %-20s  %-10s  elapsed: %ss\n" "$NOW" "$phase_name" "$status" "${duration:-?}"
            else
                printf "[%s] %-20s  %-10s  cost: \$%s  %s\n" \
                    "$NOW" "$phase_name" "$status" "${cost:-0}" "${failure:+(failure: $failure)}"
            fi
        fi
    done < <(sqlite3 "$DB" \
        "SELECT id, phase_name, status,
                ROUND(cost, 4),
                ROUND((julianday(COALESCE(finished_at, datetime('now'))) - julianday(started_at)) * 86400),
                COALESCE(failure_category, '')
         FROM phase
         ORDER BY id;" 2>/dev/null)

    # Exit when run is no longer running
    if [ "$RUN_STATUS" != "running" ]; then
        echo ""
        echo "[tail.sh] run finished with status: $RUN_STATUS"
        sqlite3 -header -column "$DB" \
            "SELECT ROUND(total_cost, 4) AS total_cost, review_verdict FROM run LIMIT 1;"
        exit 0
    fi

    sleep "$INTERVAL"
done
