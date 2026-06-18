#!/usr/bin/env bash
set -euo pipefail
#
# mac-studio-server-setup.sh — Configure Mac Studio as an always-on headless server
#
# Run directly on the Mac Studio (not over SSH) because several commands
# require sudo with interactive password entry.
#
# Safe to run multiple times (idempotent).
#

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

section() {
    echo ""
    echo "========================================================================"
    echo "  $1"
    echo "========================================================================"
    echo ""
}

ok()   { echo "  [OK]   $*"; }
warn() { echo "  [WARN] $*"; }
fail() { echo "  [FAIL] $*"; }
info() { echo "  [INFO] $*"; }

WARNINGS=()
MANUAL_STEPS=()

# ---------------------------------------------------------------------------
# 1. Power & Sleep Settings
# ---------------------------------------------------------------------------
section "1/6  Power & Sleep Settings"

echo "Configuring pmset for always-on headless operation..."
echo "(sudo will prompt for your password)"
echo ""

sudo pmset -a autorestart 1       # restart after power failure
sudo pmset -a sleep 0             # never system-sleep
sudo pmset -a disablesleep 1      # disable sleep entirely
sudo pmset -a standby 0           # no standby
sudo pmset -a hibernatemode 0     # no hibernation
sudo pmset -a disksleep 0         # no disk sleep
sudo pmset -a displaysleep 0      # no display sleep
sudo pmset -a womp 1              # wake on magic packet (WOL)
sudo pmset -a tcpkeepalive 1      # keep TCP connections alive during sleep
sudo pmset -a powernap 0          # no power nap (avoid spurious wakes)

ok "Power & sleep settings applied"
echo ""
echo "Current pmset configuration:"
pmset -g

# ---------------------------------------------------------------------------
# 2. Auto-login Setup
# ---------------------------------------------------------------------------
section "2/6  Auto-login Setup"

# Check FileVault status first — auto-login requires FileVault OFF
FV_STATUS=$(fdesetup status 2>&1 || true)

if echo "$FV_STATUS" | grep -qi "on"; then
    fail "FileVault is ON. Auto-login requires FileVault to be disabled."
    echo ""
    echo "  To disable FileVault:"
    echo "    System Settings → Privacy & Security → FileVault → Turn Off"
    echo ""
    echo "  After FileVault finishes decrypting (may take hours), re-run this script."
    echo ""
    WARNINGS+=("FileVault is ON — auto-login was NOT configured")
    MANUAL_STEPS+=("Disable FileVault, then re-run this script for auto-login")
else
    ok "FileVault is OFF"
    echo ""
    echo "Setting auto-login for user 'shane'."
    echo "You will be prompted for the user password:"
    echo ""
    sudo sysadminctl -autologin set -userName shane -password -
    ok "Auto-login configured for user 'shane'"
fi

# ---------------------------------------------------------------------------
# 3. Verify launchd plists
# ---------------------------------------------------------------------------
section "3/6  Verify LaunchAgents plists"

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
EXPECTED_PLISTS=(
    "com.pa.dashboard.plist"
    "com.pa.mobile-pa.plist"
    "com.pa.ttyd.plist"
)

for plist in "${EXPECTED_PLISTS[@]}"; do
    path="$LAUNCH_AGENTS_DIR/$plist"
    if [ -f "$path" ]; then
        ok "$plist exists"
    else
        warn "$plist NOT FOUND at $path"
        WARNINGS+=("Missing LaunchAgent: $plist")
    fi
done

# ---------------------------------------------------------------------------
# 4. Verify cron health check
# ---------------------------------------------------------------------------
section "4/6  Verify Cron Health Check"

if crontab -l 2>/dev/null | grep -q "service-health"; then
    ok "Health check cron entry found:"
    crontab -l 2>/dev/null | grep "service-health" | sed 's/^/       /'
else
    warn "No cron entry matching 'service-health' found"
    WARNINGS+=("Missing cron health check entry")
    echo ""
    echo "  Current crontab:"
    crontab -l 2>/dev/null | sed 's/^/       /' || echo "       (empty)"
fi

# ---------------------------------------------------------------------------
# 5. Verify services are running
# ---------------------------------------------------------------------------
section "5/6  Verify Running Services"

declare -A SERVICE_PORTS
SERVICE_PORTS=(
    [4080]="Dashboard"
    [7681]="ttyd"
    [4082]="Mobile PA"
)

for port in 4080 7681 4082; do
    name="${SERVICE_PORTS[$port]}"
    if curl -sf --max-time 3 "http://localhost:${port}/" > /dev/null 2>&1; then
        ok "$name (port $port) is responding"
    else
        warn "$name (port $port) is NOT responding"
        WARNINGS+=("Service not responding: $name on port $port")
    fi
done

# ---------------------------------------------------------------------------
# 6. Summary
# ---------------------------------------------------------------------------
section "6/6  Summary"

echo "Configured:"
echo "  - Power: auto-restart on, sleep disabled, WOL on, TCP keepalive on"
echo "  - Auto-login: $(echo "$FV_STATUS" | grep -qi "on" && echo "SKIPPED (FileVault is on)" || echo "set for user 'shane'")"
echo ""

if [ ${#WARNINGS[@]} -eq 0 ]; then
    echo "All checks passed — no warnings."
else
    echo "Warnings (${#WARNINGS[@]}):"
    for w in "${WARNINGS[@]}"; do
        echo "  - $w"
    done
fi

if [ ${#MANUAL_STEPS[@]} -gt 0 ]; then
    echo ""
    echo "Manual steps remaining:"
    for step in "${MANUAL_STEPS[@]}"; do
        echo "  - $step"
    done
fi

echo ""
echo "Done."
