#!/usr/bin/env bash
# stats.sh
# Description: Query the pipeline runs SQLite database for cost, performance, and review statistics.
# Usage: bash scripts/stats.sh <query> [args]
#   Queries: expensive-phase, turn-limit, cost-by-phase, parse-failures,
#            review-findings, total-runs, avg-cost, run-details <run_id>
# Outputs: Formatted table output to stdout via sqlite3 -header -column
# Dependencies: sqlite3 (must be installed and in PATH); runs/runs.db must exist
set -euo pipefail
cd "$(dirname "$0")/.."

DB="runs/runs.db"

if [ ! -f "$DB" ]; then
    echo "No database found at $DB"
    exit 1
fi

usage() {
    cat <<'USAGE'
Usage: ./scripts/stats.sh <query> [args]

Queries:
  expensive-phase        Most expensive phase across all runs
  turn-limit             Phases that hit the turn limit
  cost-by-phase          Cost by phase (last 10 runs)
  parse-failures         All parse failures
  review-findings        Review findings (last 20)
  total-runs             Total runs by status
  avg-cost               Average cost per run
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
            "SELECT phase, output_path, cost_usd
             FROM phase_logs
             ORDER BY cost_usd DESC
             LIMIT 1;"
        ;;
    turn-limit)
        sqlite3 -header -column "$DB" \
            "SELECT run_id, phase
             FROM phase_logs
             WHERE hit_turn_limit = 1;"
        ;;
    cost-by-phase)
        sqlite3 -header -column "$DB" \
            "SELECT phase, SUM(cost_usd) AS total_cost, AVG(cost_usd) AS avg_cost
             FROM phase_logs
             WHERE run_id IN (
                 SELECT run_id FROM pipeline_runs ORDER BY started_at DESC LIMIT 10
             )
             GROUP BY phase;"
        ;;
    parse-failures)
        sqlite3 -header -column "$DB" \
            "SELECT run_id, phase, validation_errors
             FROM phase_logs
             WHERE parse_success = 0;"
        ;;
    review-findings)
        sqlite3 -header -column "$DB" \
            "SELECT r.run_id, r.phase, r.verdict, r.score
             FROM reviews r
             ORDER BY created_at DESC
             LIMIT 20;"
        ;;
    total-runs)
        sqlite3 -header -column "$DB" \
            "SELECT status, COUNT(*) AS count
             FROM pipeline_runs
             GROUP BY status;"
        ;;
    avg-cost)
        sqlite3 -header -column "$DB" \
            "SELECT COUNT(*) AS runs,
                    AVG(spent_usd) AS avg_cost,
                    SUM(spent_usd) AS total_cost
             FROM pipeline_runs
             WHERE status != 'running';"
        ;;
    run-details)
        [ $# -lt 1 ] && { echo "Usage: stats.sh run-details <run_id>"; exit 1; }
        run_id="$1"
        echo "=== Run ==="
        sqlite3 -header -column "$DB" \
            "SELECT run_id, issue, status, model, spent_usd, error
             FROM pipeline_runs
             WHERE run_id = '$run_id';"
        echo ""
        echo "=== Phases ==="
        sqlite3 -header -column "$DB" \
            "SELECT phase, model, duration_s, cost_usd, num_turns, stop_reason, parse_success
             FROM phase_logs
             WHERE run_id = '$run_id'
             ORDER BY id;"
        echo ""
        echo "=== Reviews ==="
        sqlite3 -header -column "$DB" \
            "SELECT phase, reviewer_model, verdict, score
             FROM reviews
             WHERE run_id = '$run_id';"
        ;;
    *)
        echo "Unknown query: $query"
        usage
        ;;
esac
