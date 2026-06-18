#!/usr/bin/env bash
set -euo pipefail
# mac-studio-setup.sh — Set up simple-workflow on Mac Studio
# Run from laptop: ssh studio 'bash -s' < workspace/mac-studio-setup.sh
# Or copy and run directly on Mac Studio.

REPO_URL="git@github.com:shanemmattner/simple-workflow.git"
INSTALL_DIR="$HOME/services/repos/simple-workflow"
BIN_DIR="$HOME/.local/bin"

log() { echo "[sw-setup] $*"; }

# --- 1. Clone repo if not present ---
if [ -d "$INSTALL_DIR/.git" ]; then
    log "Repo already cloned at $INSTALL_DIR — pulling latest"
    git -C "$INSTALL_DIR" fetch origin
    git -C "$INSTALL_DIR" reset --hard origin/main
else
    log "Cloning simple-workflow to $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# --- 2. Install Python dependencies ---
log "Installing Python dependencies via uv pip"
cd "$INSTALL_DIR"
uv pip install --system pydantic pyyaml 2>&1 || {
    log "uv pip failed — trying pip3"
    pip3 install --user pydantic pyyaml 2>&1
}

# Verify imports work
python3 -c "import pydantic; import yaml; print(f'pydantic={pydantic.__version__} pyyaml={yaml.__version__}')"
log "Dependencies installed"

# --- 3. Install sw-dispatch launcher script ---
log "Installing sw-dispatch launcher to $BIN_DIR/sw-dispatch"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/sw-dispatch" << 'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
# sw-dispatch — Run simple-workflow pipeline in a persistent tmux session.
# Usage: sw-dispatch owner/repo#123 [--budget 2.00] [--model opus]
#
# Each issue gets its own tmux session named sw-<repo>-<number>.
# Sessions survive SSH disconnect and laptop sleep.

REPO_DIR="$HOME/services/repos/simple-workflow"

if [ $# -lt 1 ]; then
    echo "Usage: sw-dispatch owner/repo#123 [--budget 2.00] [--model opus]"
    echo ""
    echo "Commands:"
    echo "  sw-dispatch <issue>          Run pipeline in tmux"
    echo "  sw-dispatch --list           List active sw sessions"
    echo "  sw-dispatch --attach <name>  Attach to a session"
    echo "  sw-dispatch --kill <name>    Kill a session"
    exit 1
fi

case "${1:-}" in
    --list)
        tmux list-sessions -F '#{session_name} #{session_created_string}' 2>/dev/null \
            | grep '^sw-' || echo "No active sw sessions"
        exit 0
        ;;
    --attach)
        tmux attach-session -t "${2:?session name required}" 2>/dev/null || {
            echo "Session not found. Active sessions:"
            tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^sw-' || echo "  (none)"
            exit 1
        }
        exit 0
        ;;
    --kill)
        tmux kill-session -t "${2:?session name required}" 2>/dev/null && echo "Killed ${2}" || echo "Session ${2} not found"
        exit 0
        ;;
esac

ISSUE_REF="$1"
shift

# Parse issue ref for session name: owner/repo#123 -> sw-repo-123
if [[ "$ISSUE_REF" =~ ^([^/]+)/([^#]+)#([0-9]+)$ ]]; then
    REPO_NAME="${BASH_REMATCH[2]}"
    ISSUE_NUM="${BASH_REMATCH[3]}"
    SESSION_NAME="sw-${REPO_NAME}-${ISSUE_NUM}"
else
    echo "ERROR: Issue ref must be owner/repo#NNN (got: $ISSUE_REF)"
    exit 1
fi

# Check if session already exists
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "Session $SESSION_NAME already running. Use:"
    echo "  sw-dispatch --attach $SESSION_NAME"
    exit 1
fi

# Pull latest before running
echo "Pulling latest simple-workflow..."
git -C "$REPO_DIR" pull --ff-only origin main 2>/dev/null || true

# Build the command
RUN_CMD="cd '$REPO_DIR' && PYTHONPATH='$REPO_DIR' python3 engines/github_claude/__main__.py '$ISSUE_REF' $*"

# Append exit trap so we can see results before session dies
FULL_CMD="$RUN_CMD; echo ''; echo '=== Pipeline complete. Press Enter to close. ==='; read"

echo "Dispatching: $ISSUE_REF"
echo "  Session: $SESSION_NAME"
echo "  Budget:  ${1:-default}"
echo ""

# Create tmux session (detached)
tmux new-session -d -s "$SESSION_NAME" -x 200 -y 50 "$FULL_CMD"

echo "Running in tmux session: $SESSION_NAME"
echo ""
echo "  Attach:  sw-dispatch --attach $SESSION_NAME"
echo "  List:    sw-dispatch --list"
echo "  Kill:    sw-dispatch --kill $SESSION_NAME"
LAUNCHER
chmod +x "$BIN_DIR/sw-dispatch"
log "sw-dispatch installed"

# --- 4. Ensure ~/.local/bin is on PATH ---
SHELL_RC="$HOME/.zshrc"
if [ -f "$HOME/.bashrc" ] && [ ! -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

if ! grep -q 'export PATH="\$HOME/.local/bin:\$PATH"' "$SHELL_RC" 2>/dev/null; then
    log "Adding ~/.local/bin to PATH in $SHELL_RC"
    echo '' >> "$SHELL_RC"
    echo '# simple-workflow launcher' >> "$SHELL_RC"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
else
    log "~/.local/bin already on PATH"
fi

# --- 5. Add sw alias ---
if ! grep -q 'alias sw=' "$SHELL_RC" 2>/dev/null; then
    log "Adding sw alias to $SHELL_RC"
    echo 'alias sw="sw-dispatch"' >> "$SHELL_RC"
else
    log "sw alias already exists"
fi

# --- 6. Create tmux config for sw sessions ---
TMUX_CONF="$HOME/.tmux-sw.conf"
if [ ! -f "$TMUX_CONF" ]; then
    log "Creating tmux config at $TMUX_CONF"
    cat > "$TMUX_CONF" << 'TMUXCONF'
# simple-workflow tmux config
set -g history-limit 50000
set -g remain-on-exit on
set -g status-left "[sw] "
set -g status-right "%Y-%m-%d %H:%M"
TMUXCONF
fi

# --- 7. Summary ---
echo ""
echo "============================================"
echo "  simple-workflow Mac Studio setup complete"
echo "============================================"
echo ""
echo "  Repo:     $INSTALL_DIR"
echo "  Launcher: $BIN_DIR/sw-dispatch"
echo "  Alias:    sw (after shell restart)"
echo ""
echo "  Usage:"
echo "    sw owner/repo#123 --budget 2.00"
echo "    sw --list"
echo "    sw --attach sw-repo-123"
echo ""
echo "  From laptop:"
echo "    ssh studio sw-dispatch owner/repo#123 --budget 2.00"
echo ""
echo "  To update simple-workflow later:"
echo "    ssh studio 'cd ~/services/repos/simple-workflow && git pull'"
echo ""
