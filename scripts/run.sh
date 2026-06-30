#!/usr/bin/env bash
set -euo pipefail
# Usage: ./scripts/run.sh <workflow-config-path> owner/repo#123 [--budget 2.00] [--model opus]
#
# <workflow-config-path>  Required. Path to a workflow.md file or a directory
#                          containing one, e.g. workflows/shftty-web/workflow.md
#                          or workflows/shftty-web. Workflow selection is always
#                          explicit -- there is no auto-detection.
# --engine claude          (default) uses engines/github_claude (Claude CLI subprocess)

cd "$(dirname "$0")/.."

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <workflow-config-path> owner/repo#123 [--budget 2.00] [--model opus] [--engine claude|three-step]" >&2
    exit 1
fi

WORKFLOW_PATH="$1"
shift

# Resolve workflow config path -> workflow dir -> workflow name.
if [[ -d "$WORKFLOW_PATH" ]]; then
    WF_DIR="$WORKFLOW_PATH"
    WF_FILE="$WF_DIR/workflow.md"
elif [[ -f "$WORKFLOW_PATH" && "$WORKFLOW_PATH" == *workflow.md ]]; then
    WF_FILE="$WORKFLOW_PATH"
    WF_DIR="$(dirname "$WORKFLOW_PATH")"
else
    echo "[run.sh] ERROR: workflow config path must exist and be a directory containing workflow.md, or a path ending in workflow.md (got: $WORKFLOW_PATH)" >&2
    exit 1
fi

if [[ ! -f "$WF_FILE" ]]; then
    echo "[run.sh] ERROR: workflow.md not found at $WF_FILE" >&2
    exit 1
fi

WORKFLOW_NAME="$(basename "$WF_DIR")"
echo "[run.sh] workflow: $WORKFLOW_NAME (from $WF_FILE)" >&2

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
        MODULE="engine.__main__"
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
        FULL_REPO="${ISSUE_REF%#*}"      # owner/repo
        REPO_NAME="${FULL_REPO##*/}"     # repo (strip owner/)
        PA_ROOT="$(cd "$PWD/.." && pwd)" # parent of simple-workflow = PA root
        CANDIDATE="$PA_ROOT/$REPO_NAME"
        if [[ -e "$CANDIDATE/.git" ]]; then
            echo "[run.sh] auto-detected repo: $CANDIDATE" >&2
            ARGS+=("--repo-path" "$CANDIDATE")
        else
            echo "[run.sh] WARNING: could not auto-detect repo path for '$REPO_NAME' — looked at $CANDIDATE" >&2
        fi
    fi
fi

# Workflow is always explicit -- pass the resolved name through.
HAS_WORKFLOW=false
for arg in "${ARGS[@]}"; do
    [[ "$arg" == "--workflow" ]] && HAS_WORKFLOW=true
done
if $HAS_WORKFLOW; then
    echo "[run.sh] ERROR: --workflow is set automatically from the first positional arg; do not pass it explicitly" >&2
    exit 1
fi
if [[ "$ENGINE" == "claude" ]]; then
    ARGS+=("--workflow" "$WORKFLOW_NAME")
else
    echo "[run.sh] NOTE: --engine $ENGINE does not support --workflow; workflow selection ($WORKFLOW_NAME) is only applied to the claude engine" >&2
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
