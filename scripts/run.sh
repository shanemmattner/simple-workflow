#!/usr/bin/env bash
set -euo pipefail
# Usage: ./scripts/run.sh owner/repo#123 [--engine openhands] [--budget 2.00] [--model opus]
#
# --engine claude     (default) uses engines/github_claude (Claude CLI subprocess)
# --engine openhands  uses engines/github_openhands (OpenHands SDK + OpenRouter)

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
    openhands)
        MODULE="engines.github_openhands.__main__"
        ;;
    *)
        echo "Unknown engine: $ENGINE (valid: claude, openhands)" >&2
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
            ARGS+=("--repo-path" "$CANDIDATE")
        fi
    fi
fi

PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" python3 "$MODULE" "${ARGS[@]}"
