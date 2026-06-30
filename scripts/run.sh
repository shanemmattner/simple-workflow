#!/usr/bin/env bash
set -euo pipefail
# Usage: ./scripts/run.sh <workflow-config-path> <repo-path> <git-ref> [--issue 896] [--budget 2.00] [--model opus] [--engine claude|three-step]
#
# <workflow-config-path>  Required. Path to a workflow.md file or a directory
#                          containing one, e.g. workflows/shftty-web/workflow.md
#                          or workflows/shftty-web. Workflow selection is always
#                          explicit -- there is no auto-detection.
# <repo-path>              Required. Local filesystem path to the target git
#                          repo, e.g. /path/to/repos/shftty.
# <git-ref>                Required. Commit hash, branch name, or tag the
#                          pipeline worktree branches from (replaces the old
#                          hardcoded "main").
# --issue <number>         Optional. Issue number for context -- if given,
#                          triage gets the issue body; status comments are
#                          posted back to it. Owner/repo is auto-derived from
#                          the repo's `git remote get-url origin`.
# --engine claude          (default) uses engines/github_claude (Claude CLI subprocess)

cd "$(dirname "$0")/.."

if [[ $# -lt 3 ]]; then
    echo "Usage: $0 <workflow-config-path> <repo-path> <git-ref> [--issue 896] [--budget 2.00] [--model opus] [--engine claude|three-step]" >&2
    exit 1
fi

WORKFLOW_PATH="$1"
REPO_PATH="$2"
GIT_REF="$3"
shift 3

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

if [[ ! -e "$REPO_PATH/.git" ]]; then
    echo "[run.sh] ERROR: repo-path does not look like a git repo (no .git): $REPO_PATH" >&2
    exit 1
fi
echo "[run.sh] repo-path: $REPO_PATH" >&2
echo "[run.sh] git-ref:   $GIT_REF" >&2

# Extract --engine and --issue flags before passing remaining args to Python.
ENGINE="claude"
ISSUE_NUMBER=""
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --engine)
            ENGINE="$2"
            shift 2
            ;;
        --issue)
            ISSUE_NUMBER="$2"
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
        # three-step's CLI still takes a single owner/repo#NNN positional and
        # has no --repo-path/--base passthrough -- incompatible with this
        # script's repo-path + git-ref positional contract. Use
        # `python -m engines.three_step owner/repo#123` directly instead.
        echo "[run.sh] ERROR: --engine three-step is not supported via run.sh's repo-path + git-ref contract." >&2
        echo "[run.sh]        Invoke it directly: python -m engines.three_step owner/repo#123 [--budget N] [--model M]" >&2
        exit 1
        ;;
    *)
        echo "Unknown engine: $ENGINE (valid: claude, three-step)" >&2
        exit 1
        ;;
esac

# Derive owner/repo from the repo's git remote (used for issue fetching and
# status comments when --issue is given; harmless if omitted).
REPO_SLUG=""
REMOTE_URL="$(git -C "$REPO_PATH" remote get-url origin 2>/dev/null || true)"
if [[ -n "$REMOTE_URL" ]]; then
    # Matches:
    #   git@github.com:owner/repo.git
    #   https://github.com/owner/repo.git
    #   https://gitlab.com/owner/repo
    if [[ "$REMOTE_URL" =~ [:/]([^/:]+/[^/]+)$ ]]; then
        REPO_SLUG="${BASH_REMATCH[1]}"
        REPO_SLUG="${REPO_SLUG%.git}"
        echo "[run.sh] auto-detected repo slug from git remote: $REPO_SLUG" >&2
    fi
fi
if [[ -z "$REPO_SLUG" ]]; then
    echo "[run.sh] WARNING: could not derive owner/repo from $REPO_PATH's git remote -- issue fetch/comments will be skipped" >&2
fi

# --engine is always "claude" at this point (three-step exits earlier).
ARGS+=("--repo-path" "$REPO_PATH" "--base" "$GIT_REF")
[[ -n "$ISSUE_NUMBER" ]] && ARGS+=("--issue" "$ISSUE_NUMBER")
[[ -n "$REPO_SLUG" ]] && ARGS=("$REPO_SLUG" "${ARGS[@]}")

# Workflow is always explicit -- pass the resolved name through.
HAS_WORKFLOW=false
for arg in "${ARGS[@]}"; do
    [[ "$arg" == "--workflow" ]] && HAS_WORKFLOW=true
done
if $HAS_WORKFLOW; then
    echo "[run.sh] ERROR: --workflow is set automatically from the first positional arg; do not pass it explicitly" >&2
    exit 1
fi
ARGS+=("--workflow" "$WORKFLOW_NAME")

# Per-issue/per-ref run lock: prevent two pipeline runs from racing against the
# same target (the $23 retry storm — 11 runs in 39 min — happened because
# nothing stopped concurrent invocations). Lock key is derived from the
# repo-path plus either the issue number (if provided) or the git ref,
# sanitized for use as a filename. Non-blocking: a second run against the
# same target fails fast instead of queuing, so retry storms surface
# immediately instead of compounding cost.
#
# macOS does not ship flock(1) (it's Linux/util-linux only), so the lock is
# acquired via scripts/lock_exec.py (fcntl.flock + os.execvp) — it wraps the
# actual pipeline invocation and holds the lock for the run's full lifetime
# because exec() preserves open file descriptors across the process image
# replacement. The lock releases automatically on exit, success or failure.
REPO_PATH_KEY="$(basename "$REPO_PATH")"
if [[ -n "$ISSUE_NUMBER" ]]; then
    LOCK_KEY="${REPO_PATH_KEY}-issue-${ISSUE_NUMBER}"
else
    LOCK_KEY="${REPO_PATH_KEY}-ref-${GIT_REF}"
fi
LOCK_KEY="${LOCK_KEY//\//-}"
LOCK_KEY="${LOCK_KEY//#/-}"
LOCK_FILE="/tmp/sw-lock-${LOCK_KEY}.lock"

# Layer 2: hard wall-clock cap via gtimeout (brew install coreutils).
# Exit code 124 means gtimeout killed the process.
# Falls back to running without timeout if gtimeout isn't available (e.g. plain macOS).
WALL_TIMEOUT="${CLAUDE_WALL_TIMEOUT_S:-3600}"  # 60 min default

if command -v gtimeout &>/dev/null; then
    PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" python3 scripts/lock_exec.py "$LOCK_FILE" \
        gtimeout --kill-after=30s "${WALL_TIMEOUT}s" \
        python3 -m "$MODULE" "${ARGS[@]}"
    EXIT_CODE=$?
    if [[ $EXIT_CODE -eq 124 ]]; then
        echo "[run.sh] ERROR: pipeline killed by gtimeout after ${WALL_TIMEOUT}s wall-clock limit (exit 124)" >&2
    fi
    exit $EXIT_CODE
else
    echo "[run.sh] WARNING: gtimeout not found — running without wall-clock cap (install coreutils: brew install coreutils)" >&2
    PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" python3 scripts/lock_exec.py "$LOCK_FILE" \
        python3 -m "$MODULE" "${ARGS[@]}"
fi
