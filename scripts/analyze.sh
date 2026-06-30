#!/bin/bash
set -euo pipefail

# Analyze pipeline run timing and cost
# Usage: ./scripts/analyze.sh [db-path-or-substring]

RUNS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../engines/github_claude/runs" && pwd)"

# Find DB file
if [[ $# -eq 0 ]]; then
  DB_PATH=$(ls -t "$RUNS_DIR"/*.db 2>/dev/null | head -1)
  if [[ -z "$DB_PATH" ]]; then
    echo "No database found in $RUNS_DIR" >&2
    exit 1
  fi
else
  DB_PATH=$(ls -t "$RUNS_DIR"/*"$1"*.db 2>/dev/null | head -1)
  if [[ -z "$DB_PATH" ]]; then
    echo "No database matching '$1' in $RUNS_DIR" >&2
    exit 1
  fi
fi

echo "=== Pipeline Run Analysis ==="
echo "DB: $(basename "$DB_PATH")"
echo ""

# Run summary
echo "=== Run Summary ==="
sqlite3 "$DB_PATH" <<'SUMMARY'
SELECT 'Repo: ' || repo,
       'Issue: #' || issue_number,
       'Status: ' || status,
       'Model: ' || COALESCE(model, 'N/A'),
       'Cost: $' || ROUND(COALESCE(total_cost, 0), 2),
       'Duration: ' || COALESCE(
         printf('%02d:%02d:%02d',
           CAST((julianday(finished_at) - julianday(started_at)) * 24 AS INTEGER),
           CAST(((julianday(finished_at) - julianday(started_at)) * 24 * 60) % 60 AS INTEGER),
           CAST(((julianday(finished_at) - julianday(started_at)) * 24 * 3600) % 60 AS INTEGER)),
         'running')
FROM run LIMIT 1;
SUMMARY

echo ""
echo "=== Phase Breakdown ==="
printf "%-25s %10s %8s %10s %10s %12s\n" "Phase" "Duration" "Cost" "Tokens In" "Tokens Out" "Status"
echo "───────────────────────────────────────────────────────────────────────────"

# Create temp file for phase query results
PHASE_TEMP=$(mktemp)
sqlite3 "$DB_PATH" > "$PHASE_TEMP" <<'PHASES'
SELECT
  SUBSTR(COALESCE(phase_name, 'N/A'), 1, 24) || '|' ||
  COALESCE(
    printf('%2dm %2ds',
      CAST(((julianday(finished_at) - julianday(started_at)) * 24 * 60) % 60 AS INTEGER),
      CAST(((julianday(finished_at) - julianday(started_at)) * 24 * 3600) % 60 AS INTEGER)),
    '—') || '|' ||
  COALESCE(cost, 0) || '|' ||
  COALESCE(tokens_in, 0) || '|' ||
  COALESCE(tokens_out, 0) || '|' ||
  status
FROM phase
ORDER BY id;
PHASES

while IFS='|' read -r phase dur cost in out status; do
  printf "%-25s %10s %8.2f %10s %10s %12s\n" \
    "$(echo "$phase" | cut -c1-24)" \
    "$dur" \
    "$cost" \
    "${in:-0}" \
    "${out:-0}" \
    "$status"
done < "$PHASE_TEMP"
rm -f "$PHASE_TEMP"

echo ""
echo "=== Time Analysis ==="

# Extract total, exec, and overhead seconds
TOTALS=$(sqlite3 "$DB_PATH" <<'TOTALS'
SELECT
  CAST(COALESCE((MAX(julianday(finished_at)) - MIN(julianday(started_at))) * 86400, 0) AS INTEGER) as total_sec,
  CAST(COALESCE(SUM(CASE WHEN phase_name NOT IN ('triage', 'verify', 'plan', 'test_plan', 'wave_planner', 'review', 'improve', 'improve-brain-pipeline')
    THEN (julianday(finished_at) - julianday(started_at)) * 86400 ELSE 0 END), 0) AS INTEGER) as exec_sec,
  CAST(COALESCE(SUM(CASE WHEN phase_name IN ('triage', 'verify', 'plan', 'test_plan', 'wave_planner', 'review', 'improve', 'improve-brain-pipeline')
    THEN (julianday(finished_at) - julianday(started_at)) * 86400 ELSE 0 END), 0) AS INTEGER) as overhead_sec
FROM phase WHERE finished_at IS NOT NULL;
TOTALS
)

IFS='|' read -r total_sec exec_sec overhead_sec <<< "$TOTALS"

total_h=$((total_sec / 3600))
total_m=$(((total_sec % 3600) / 60))
total_s=$((total_sec % 60))

exec_h=$((exec_sec / 3600))
exec_m=$(((exec_sec % 3600) / 60))
exec_s=$((exec_sec % 60))

overhead_h=$((overhead_sec / 3600))
overhead_m=$(((overhead_sec % 3600) / 60))
overhead_s=$((overhead_sec % 60))

pct_exec=0
if [[ $total_sec -gt 0 ]]; then
  pct_exec=$(( (exec_sec * 100) / total_sec ))
fi
pct_overhead=$(( 100 - pct_exec ))

printf "Wall clock time:    %dh %2dm %2ds\n" "$total_h" "$total_m" "$total_s"
printf "Execute time:       %dh %2dm %2ds (%d%%)\n" "$exec_h" "$exec_m" "$exec_s" "$pct_exec"
printf "Overhead time:      %dh %2dm %2ds (%d%%)\n" "$overhead_h" "$overhead_m" "$overhead_s" "$pct_overhead"

echo ""
echo "=== Task Count & Efficiency ==="

TASK_DATA=$(sqlite3 "$DB_PATH" <<'TASKDATA'
SELECT
  COUNT(CASE WHEN phase_name LIKE 'plan-task-%' THEN 1 END) as task_count,
  COALESCE((SELECT total_cost FROM run LIMIT 1), 0) as total_cost
FROM phase;
TASKDATA
)

IFS='|' read -r tasks cost_total <<< "$TASK_DATA"

printf "Tasks created:      %s\n" "$tasks"
printf "Total cost:         \$%.2f\n" "$cost_total"
if [[ "$tasks" != "0" ]]; then
  cost_per=$(echo "scale=2; $cost_total / $tasks" | bc)
  printf "Cost per task:      \$%s\n" "$cost_per"
fi

echo ""
echo "=== Efficiency Flags ==="

# Check for stalled phases (0 cost but >60s)
STALLED=$(sqlite3 "$DB_PATH" <<'STALLEDQ'
SELECT COUNT(*) FROM phase
WHERE finished_at IS NOT NULL
  AND COALESCE(cost, 0) = 0
  AND (julianday(finished_at) - julianday(started_at)) * 86400 > 60;
STALLEDQ
)

if [[ $STALLED -gt 0 ]]; then
  echo "⚠ Stalled phases detected: $STALLED (likely watchdog kills)"
  sqlite3 "$DB_PATH" <<'STALLEDLIST'
SELECT '  ' || SUBSTR(phase_name, 1, 20) || ' (' ||
  PRINTF('%dm %ds',
    CAST(((julianday(finished_at) - julianday(started_at)) * 24 * 60) % 60 AS INTEGER),
    CAST(((julianday(finished_at) - julianday(started_at)) * 24 * 3600) % 60 AS INTEGER)) || ')'
FROM phase
WHERE finished_at IS NOT NULL
  AND COALESCE(cost, 0) = 0
  AND (julianday(finished_at) - julianday(started_at)) * 86400 > 60
ORDER BY phase_name;
STALLEDLIST
fi

# Check triage duration
TRIAGE_SECS=$(sqlite3 "$DB_PATH" "SELECT CAST((julianday(finished_at) - julianday(started_at)) * 86400 AS INTEGER) FROM phase WHERE phase_name = 'triage' AND finished_at IS NOT NULL LIMIT 1" 2>/dev/null || echo "0")
if [[ $TRIAGE_SECS -gt 120 ]]; then
  echo "⚠ Triage took ${TRIAGE_SECS}s (>120s threshold)"
fi

# Check high task count
if [[ "$tasks" != "0" && $tasks -gt 2 ]]; then
  echo "⚠ High task count: $tasks tasks (>2 threshold)"
fi

if [[ $STALLED -eq 0 && $TRIAGE_SECS -le 120 && ! ($tasks -gt 2) ]]; then
  echo "✓ No efficiency issues detected"
fi

echo ""
echo "✓ Analysis complete"
