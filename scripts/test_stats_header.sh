#!/usr/bin/env bash
# Test: stats.sh has required header comment fields
set -euo pipefail

SCRIPT="$(dirname "$0")/stats.sh"
fail=0

for field in "stats.sh" "Description:" "Usage:" "Outputs:" "Dependencies:"; do
    if ! grep -q "^# .*${field}" "$SCRIPT" && ! grep -q "^# ${field}" "$SCRIPT"; then
        echo "FAIL: missing header field: $field"
        fail=1
    fi
done

if [ $fail -eq 0 ]; then
    echo "PASS: all required header fields present in stats.sh"
fi

exit $fail
