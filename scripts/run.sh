#!/usr/bin/env bash
set -euo pipefail
# Usage: ./scripts/run.sh owner/repo#123 [--budget 2.00] [--model opus]
#
# --engine claude     (default) uses engines/github_claude (Claude CLI subprocess)

cd "$(dirname "$0")/.."

# Extract --engine flag before passing remaining args to Python
ENGINE="claude"
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --engine)
            ENGINE="$2"
            shift 2
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

case "$ENGINE" in
    claude)
        MODULE="engines.github_claude.__main__"
        ;;
    three-step)
        MODULE="engines.three_step.__main__"
        ;;
    *)
        echo "Unknown engine: $ENGINE (valid: claude, three-step)" >&2
        exit 1
        ;;
esac

# Auto-detect --repo-path from the issue ref (owner/repo#NNN)
# Looks for repos/<repo> relative to the parent of this checkout
HAS_REPO_PATH=false
for arg in "${ARGS[@]}"; do
    [[ "$arg" == "--repo-path" ]] && HAS_REPO_PATH=true
done

if ! $HAS_REPO_PATH; then
    ISSUE_REF="${ARGS[0]:-}"
    if [[ "$ISSUE_REF" == *"#"* ]]; then
        REPO_NAME="${ISSUE_REF%#*}"      # owner/repo
        REPO_NAME="${REPO_NAME##*/}"     # repo (strip owner/)
        PA_ROOT="$(cd "$PWD/.." && pwd)" # parent of simple-workflow = PA root
        CANDIDATE="$PA_ROOT/$REPO_NAME"
        if [[ -d "$CANDIDATE/.git" ]]; then
            echo "[run.sh] auto-detected repo: $CANDIDATE" >&2
            ARGS+=("--repo-path" "$CANDIDATE")
        else
            echo "[run.sh] WARNING: could not auto-detect repo path for '$REPO_NAME' — looked at $CANDIDATE" >&2
        fi
    fi
fi

# Layer 2: hard wall-clock cap via gtimeout (brew install coreutils).
# Exit code 124 means gtimeout killed the process.
# Falls back to running without timeout if gtimeout isn't available (e.g. plain macOS).
WALL_TIMEOUT="${CLAUDE_WALL_TIMEOUT_S:-3600}"  # 60 min default

run_engine() {
    PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" python3 -m "$MODULE" "${ARGS[@]}"
}

if command -v gtimeout &>/dev/null; then
    gtimeout --kill-after=30s "${WALL_TIMEOUT}s" \
        env PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" python3 -m "$MODULE" "${ARGS[@]}"
    EXIT_CODE=$?
    if [[ $EXIT_CODE -eq 124 ]]; then
        echo "[run.sh] ERROR: pipeline killed by gtimeout after ${WALL_TIMEOUT}s wall-clock limit (exit 124)" >&2
    fi
    exit $EXIT_CODE
else
    echo "[run.sh] WARNING: gtimeout not found — running without wall-clock cap (install coreutils: brew install coreutils)" >&2
    run_engine
fi
