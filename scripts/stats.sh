#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

RUNS_DIR="engines/github_claude/runs"

# Resolve DB: optional arg = substring match on filename
if [ "${1:-}" != "" ] && [ "${1:-}" != "expensive-phase" ] && [ "${1:-}" != "turn-limit" ] && \
   [ "${1:-}" != "cost-by-phase" ] && [ "${1:-}" != "parse-failures" ] && \
   [ "${1:-}" != "review-findings" ] && [ "${1:-}" != "total-runs" ] && \
   [ "${1:-}" != "avg-cost" ] && [ "${1:-}" != "run-details" ]; then
    # First arg looks like a DB path or substring
    SUBSTR="$1"
    shift
    if [ -f "$SUBSTR" ]; then
        DB="$SUBSTR"
    else
        DB=$(ls -t "$RUNS_DIR"/*.db 2>/dev/null | grep "$SUBSTR" | head -1 || true)
        [ -z "$DB" ] && { echo "No DB matching '$SUBSTR' in $RUNS_DIR"; exit 1; }
    fi
elif [ $# -ge 1 ]; then
    # Default: most recent DB
    DB=$(ls -t "$RUNS_DIR"/*.db 2>/dev/null | head -1 || true)
    [ -z "$DB" ] && { echo "No .db files found in $RUNS_DIR"; exit 1; }
else
    DB=$(ls -t "$RUNS_DIR"/*.db 2>/dev/null | head -1 || true)
    [ -z "$DB" ] && { echo "No .db files found in $RUNS_DIR"; exit 1; }
fi

usage() {
    cat <<'USAGE'
Usage: ./scripts/stats.sh [db-substring] <query> [args]

Queries:
  expensive-phase        Most expensive phase across all runs
  turn-limit             Phases that hit the turn limit (failure_category = 'turn_limit')
  cost-by-phase          Cost by phase name
  parse-failures         Phases with failure_category set
  review-findings        Runs with review verdicts
  total-runs             Total runs by status
  avg-cost               Average cost per completed run
  run-details <run_id>   Details for a specific run
USAGE
    exit 1
}

[ $# -lt 1 ] && usage

query="$1"
shift

case "$query" in
    expensive-phase)
        sqlite3 -header -column "$DB" \
            "SELECT phase_name, model, cost, tokens_in, tokens_out
             FROM phase
             ORDER BY cost DESC
             LIMIT 10;"
        ;;
    turn-limit)
        sqlite3 -header -column "$DB" \
            "SELECT p.run_id, p.phase_name, p.failure_category, p.cost
             FROM phase p
             WHERE p.failure_category = 'turn_limit'
             ORDER BY p.started_at DESC;"
        ;;
    cost-by-phase)
        sqlite3 -header -column "$DB" \
            "SELECT phase_name,
                    COUNT(*) AS runs,
                    ROUND(SUM(cost), 4) AS total_cost,
                    ROUND(AVG(cost), 4) AS avg_cost
             FROM phase
             GROUP BY phase_name
             ORDER BY total_cost DESC;"
        ;;
    parse-failures)
        sqlite3 -header -column "$DB" \
            "SELECT run_id, phase_name, failure_category, status
             FROM phase
             WHERE failure_category IS NOT NULL
             ORDER BY started_at DESC;"
        ;;
    review-findings)
        sqlite3 -header -column "$DB" \
            "SELECT id, repo, issue_number, review_verdict, review_summary, total_cost
             FROM run
             WHERE review_verdict IS NOT NULL
             ORDER BY started_at DESC
             LIMIT 20;"
        ;;
    total-runs)
        sqlite3 -header -column "$DB" \
            "SELECT status, COUNT(*) AS count
             FROM run
             GROUP BY status;"
        ;;
    avg-cost)
        sqlite3 -header -column "$DB" \
            "SELECT COUNT(*) AS runs,
                    ROUND(AVG(total_cost), 4) AS avg_cost,
                    ROUND(SUM(total_cost), 4) AS total_cost
             FROM run
             WHERE status != 'running';"
        ;;
    run-details)
        [ $# -lt 1 ] && { echo "Usage: stats.sh run-details <run_id>"; exit 1; }
        run_id="$1"
        echo "=== Run ==="
        sqlite3 -header -column "$DB" \
            "SELECT id, repo, issue_number, branch, status, model,
                    started_at, finished_at,
                    ROUND(total_cost, 4) AS total_cost,
                    review_verdict
             FROM run
             WHERE id LIKE '%${run_id}%';"
        echo ""
        echo "=== Phases ==="
        sqlite3 -header -column "$DB" \
            "SELECT phase_name, model, status,
                    ROUND(cost, 4) AS cost,
                    tokens_in, tokens_out,
                    failure_category,
                    started_at, finished_at
             FROM phase
             WHERE run_id LIKE '%${run_id}%'
             ORDER BY id;"
        ;;
    *)
        echo "Unknown query: $query"
        usage
        ;;
esac
