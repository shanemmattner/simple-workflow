#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

RUNS_DIR="engines/github_claude/runs"

# Accept optional substring arg to select a specific run DB
if [ "${1:-}" != "" ]; then
    DB=$(ls -t "$RUNS_DIR"/*.db 2>/dev/null | grep "$1" | head -1 || true)
    [ -z "$DB" ] && { echo "No DB matching '$1' in $RUNS_DIR"; exit 1; }
else
    DB=$(ls -t "$RUNS_DIR"/*.db 2>/dev/null | head -1 || true)
    [ -z "$DB" ] && { echo "No .db files found in $RUNS_DIR"; exit 1; }
fi

echo "=== Run: $(basename "$DB") ==="
sqlite3 -header -column "$DB" \
    "SELECT id, repo, issue_number, status, model, started_at,
            ROUND(total_cost, 4) AS total_cost,
            review_verdict
     FROM run LIMIT 1;"

echo ""
echo "=== Phases ==="
sqlite3 -header -column "$DB" \
    "SELECT phase_name, status,
            ROUND(cost, 4) AS cost,
            ROUND(
                (julianday(COALESCE(finished_at, datetime('now'))) - julianday(started_at)) * 86400
            ) AS duration_s,
            failure_category
     FROM phase
     ORDER BY id;"
