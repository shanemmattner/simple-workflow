#!/usr/bin/env bash
set -euo pipefail
# launch.sh — Dispatch a simple-workflow pipeline run to Mac Studio via SSH.
# Usage: ./workspace/launch.sh owner/repo#123 [--budget 2.00] [--model opus]
#
# Runs on Mac Studio in a tmux session that survives laptop sleep.

STUDIO_HOST="studio"

if [ $# -lt 1 ]; then
    echo "Usage: ./workspace/launch.sh owner/repo#123 [--budget 2.00] [--model opus]"
    echo "       ./workspace/launch.sh --list"
    echo "       ./workspace/launch.sh --attach <session-name>"
    exit 1
fi

# Check SSH connectivity
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$STUDIO_HOST" true 2>/dev/null; then
    echo "ERROR: Cannot reach Mac Studio via SSH ($STUDIO_HOST)"
    echo "Check: Tailscale up? ssh studio config correct?"
    exit 1
fi

case "${1:-}" in
    --list)
        echo "Active sw sessions on Mac Studio:"
        ssh "$STUDIO_HOST" 'tmux list-sessions -F "#{session_name}  #{session_created_string}" 2>/dev/null | grep "^sw-"' || echo "  (none)"
        exit 0
        ;;
    --attach)
        echo "Attaching to ${2:?session name required} on Mac Studio..."
        ssh -t "$STUDIO_HOST" "tmux attach-session -t '${2}'"
        exit 0
        ;;
    --kill)
        ssh "$STUDIO_HOST" "tmux kill-session -t '${2:?session name required}'" && echo "Killed ${2}" || echo "Session ${2} not found"
        exit 0
        ;;
esac

# Dispatch to Mac Studio
echo "Dispatching to Mac Studio ($STUDIO_HOST)..."
ssh "$STUDIO_HOST" "export PATH=\"\$HOME/.local/bin:\$PATH\" && sw-dispatch $*"
