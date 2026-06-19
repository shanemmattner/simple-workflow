#!/usr/bin/env bash
set -uo pipefail

# Watch for completed pipeline runs and print their notification JSONs.
# Exits when no active PIDs remain and all notifications have been printed.
#
# Usage:
#   ./scripts/watch-runs.sh
#   ./scripts/watch-runs.sh --once   # print current notifications and exit

ACTIVE_PIDS="/tmp/sw-active-pids.txt"
SEEN_FILE="/tmp/sw-watch-seen.txt"
ONCE=false

[[ "${1:-}" == "--once" ]] && ONCE=true

# Track which notification files we've already printed
touch "$SEEN_FILE"

print_new_notifications() {
    local found=0
    for f in /tmp/sw-notify-*.json; do
        [[ -f "$f" ]] || continue
        if ! grep -qxF "$f" "$SEEN_FILE" 2>/dev/null; then
            echo "───────────────────────────────────────────"
            echo "  Notification: $(basename "$f")"
            echo "───────────────────────────────────────────"
            cat "$f"
            echo ""
            echo "$f" >> "$SEEN_FILE"
            found=1
        fi
    done
    return $found
}

active_count() {
    if [[ ! -f "$ACTIVE_PIDS" ]]; then
        echo 0
        return
    fi
    # Count PIDs that are still running
    local count=0
    while IFS= read -r pid; do
        [[ -z "$pid" ]] && continue
        if kill -0 "$pid" 2>/dev/null; then
            count=$((count + 1))
        fi
    done < "$ACTIVE_PIDS"
    echo "$count"
}

# ── Main loop ───────────────────────────────────────────────────────
if $ONCE; then
    print_new_notifications || true
    ACTIVE=$(active_count)
    echo "Active PIDs: $ACTIVE"
    exit 0
fi

echo "Watching for pipeline completions... (Ctrl-C to stop)"

while true; do
    print_new_notifications || true

    ACTIVE=$(active_count)
    if [[ $ACTIVE -eq 0 ]]; then
        # One final sweep in case a notification landed between check and count
        sleep 1
        print_new_notifications || true
        echo "No active runs remaining."
        break
    fi

    sleep 5
done
