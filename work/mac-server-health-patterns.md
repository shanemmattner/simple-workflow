# Mac Server Health Patterns

Practical patterns for keeping services healthy on a headless Mac Studio used as a server.
Research date: 2026-06-17.

---

## 1. How launchd KeepAlive Actually Works

### What launchd checks — and what it does NOT check

launchd only knows if the process PID exists. It does NOT:
- Check if the process is responding to requests
- Ping an HTTP health endpoint
- Verify the process is doing useful work
- Detect zombie or hung states

If a process hangs (stuck in a select loop, deadlock, etc.), launchd thinks it's healthy. The PID is there. That's all launchd sees.

### KeepAlive modes

| Mode | Plist config | When it restarts |
|---|---|---|
| Always | `<key>KeepAlive</key><true/>` | Any time the process exits, for any reason |
| Crash-only | `KeepAlive → SuccessfulExit: false` | Only on non-zero exit (crash), not clean shutdown |
| Network-conditional | `KeepAlive → NetworkState: true` | Only when an interface has an IP address |

**Crash-only is the right default** for long-running services. It prevents respawn loops when you intentionally stop a service for maintenance.

```xml
<key>KeepAlive</key>
<dict>
    <key>SuccessfulExit</key>
    <false/>
</dict>
```

### ThrottleInterval — the hidden gotcha

launchd imposes a 10-second minimum between restarts by default. If a service crashes faster than this, launchd enters exponential backoff. This is silent — no log, no alert, the service just stays down longer and longer.

Set an explicit `ThrottleInterval` to control this:

```xml
<key>ThrottleInterval</key>
<integer>30</integer>
```

30 seconds is a reasonable default. It prevents tight crash loops from hammering the system while keeping restart times tolerable.

### launchd does NOT support HTTP health checks

There is no native mechanism to tell launchd "restart this if `curl localhost:8803/health` fails." This capability requires an external watchdog script.

---

## 2. Port / HTTP Health Monitoring

### The core pattern

launchd keeps the PID alive. A separate watchdog script checks if the port is actually responding, and kills the process if not (launchd then restarts it).

```bash
#!/bin/bash
# health-check.sh — check if a service responds, restart via launchd if not
SERVICE_LABEL="com.myservice.llm"
HEALTH_URL="http://localhost:8803/health"
TIMEOUT=10

HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "$HEALTH_URL" || echo "000")

if [[ "$HTTP_CODE" != "200" ]]; then
    logger -t health-check "FAIL: $SERVICE_LABEL returned $HTTP_CODE — restarting"
    launchctl kickstart -k "system/$SERVICE_LABEL"
fi
```

Run this via a separate launchd plist on a schedule:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.myservice.llm-healthcheck</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/health-check.sh</string>
    </array>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>/var/log/health-check.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/health-check.err</string>
</dict>
</plist>
```

### What interval to use

- **60 seconds** is the community consensus for production services. Fast enough to catch real outages, slow enough to not waste resources.
- **30 seconds** if the service is externally visible and downtime is expensive.
- **5 minutes** for non-critical background tasks.
- Never go below 30 seconds for HTTP checks — you'll generate noise on transient hiccups.

### curl flags that matter

```bash
# The full reliable incantation
curl -s \                    # silent (no progress meter)
     -o /dev/null \          # discard body
     -w '%{http_code}' \     # print status code
     --max-time 10 \         # timeout in 10s (not --connect-timeout alone)
     --retry 0 \             # no automatic retries — the watchdog controls this
     "$HEALTH_URL"
```

`--max-time` is the total time cap including connection + transfer. Without it, a hung service causes the health check to hang too.

### Should the watchdog restart, or just alert?

Both — with escalation:
1. First failure: alert only (transient blip)
2. Two consecutive failures: alert + restart
3. Five consecutive failures in 10 minutes: alert with high priority (something structural is wrong)

The glitchymagic/service-watchdog project on GitHub implements exactly this as a Python daemon with 5 escalation levels. It tracks consecutive failures in a state file and only escalates when warranted. This prevents the "restart loop where the restart doesn't fix anything" failure mode.

---

## 3. Headless Mac Server Patterns

### The M4-specific sleep nightmare

Apple Silicon (M4 in particular) added a regression in macOS Sequoia/Tahoe where display sleep and system sleep interact in ways that can leave the machine unreachable. The full fix requires ALL of these together:

```bash
# 1. Hardware: insert an HDMI dummy plug before removing monitor
# (without it: 800x600 VNC, partial framebuffer init, GPU issues)

# 2. pmset — apply as a set, not one at a time
sudo pmset -a sleep 0          # Idle sleep timer: never
sudo pmset -a disablesleep 1   # Hard kill switch (sets SleepDisabled=1)
sudo pmset -a standby 0        # No standby mode
sudo pmset -a hibernatemode 0  # No hibernation
sudo pmset -a disksleep 0      # No disk sleep
sudo pmset -a displaysleep 0   # No display sleep
sudo pmset -a womp 1           # Wake on LAN
sudo pmset -a tcpkeepalive 1   # Keep TCP connections alive
sudo pmset -a powernap 0       # Disable Power Nap (causes unpredictable wakes)

# Note: autopoweroff and autorestart are DEPRECATED on Apple Silicon + Tahoe
# The commands accept them without error but they silently do nothing.
```

`sleep 0` and `disablesleep 1` do different things. `sleep 0` sets the idle timer to "never" but macOS can still initiate sleep under other conditions. `disablesleep 1` (via `pmset -a disablesleep 1`) sets a hard `SleepDisabled` flag. Set both.

Verify with:
```bash
pmset -g                        # All current settings
pmset -g assertions             # What's holding wake locks
pmset -g log | grep -E "Sleep|Wake"  # Sleep/wake history
```

### Auto-login is mandatory for headless

FileVault must be OFF for auto-login to work. If FileVault is on, a reboot leaves the machine at the pre-boot unlock screen with no keyboard to type on.

Set auto-login: System Settings > Users & Groups > Automatic Login.

The `defaults write` method for auto-login no longer works in macOS Sequoia/Tahoe. Use System Settings.

### Screen Sharing for headless operation

Enable Screen Sharing (System Settings > General > Sharing > Screen Sharing). The `screensharingd` process holds a wake assertion — this is intentional and keeps the machine responsive.

### HDMI dummy plug — required, not optional

Without a display connected, macOS limits GPU initialization. VNC drops to 800x600. Some GPU-dependent services don't initialize correctly. A $10–15 HDMI 4K dummy plug (search "HDMI display emulator") from Amazon solves this permanently. Plug it in before disconnecting the real monitor.

### caffeinate as a safety net

Add `caffeinate -dimsu &` to startup (login item or launchd) as a belt-and-suspenders layer:
```bash
# -d: prevent display sleep
# -i: prevent idle sleep
# -m: prevent disk sleep
# -s: prevent system sleep
# -u: declare user activity
caffeinate -dimsu &
```

This is a userspace assertion. It's weaker than pmset (kernel-level) but acts as a fallback.

### Services to disable for a dedicated server

These consume CPU/I/O with no benefit on a headless server:
- Spotlight: `sudo launchctl unload -w /System/Library/LaunchDaemons/com.apple.metadata.mds.plist`
- Screen saver: `defaults write com.apple.screensaver idleTime -int 0`
- Time Machine: `sudo tmutil disable` (if you have an independent backup strategy)
- Automatic updates: manage these manually to avoid surprise reboots
- Power Nap: included in the pmset block above

### SSH hardening for a headless server

```bash
# /etc/ssh/sshd_config.d/headless.conf (don't edit main sshd_config)
PermitRootLogin no
AllowUsers yourusername
PasswordAuthentication no
```

After editing: `sudo launchctl kickstart -k system/com.openssh.sshd`

Keep your existing session open while testing from a second terminal.

### Keychain auto-unlock for SSH sessions

CLI tools using Keychain (gh, gcloud, etc.) fail silently in SSH sessions because the login keychain isn't unlocked. Workaround:

```bash
# ~/.bashrc — only runs on SSH connections
if [ -n "$SSH_CONNECTION" ]; then
    security unlock-keychain -p "$(cat ~/.keychain_secret)" ~/Library/Keychains/login.keychain-db 2>/dev/null
fi
```

Create `~/.keychain_secret` with `chmod 600`. Only acceptable with SSH key-only auth locked down.

---

## 4. Notification on Failure

### ntfy.sh — the right tool for this

ntfy.sh is an HTTP pub/sub push notification service. Send from any curl command, receive on iOS/Android. No account required for ntfy.sh (the public server). Self-hostable for private topics.

```bash
# One-liner alert
curl -d "LLM server down on studio" \
     -H "Title: Service Alert" \
     -H "Priority: urgent" \
     -H "Tags: warning" \
     ntfy.sh/YOUR_PRIVATE_TOPIC_NAME
```

Use a random UUID as your topic name for privacy (public topics are readable by anyone who knows the name).

### Integrating ntfy into the health-check watchdog

```bash
#!/bin/bash
TOPIC="ntfy.sh/abc123randomtopic"
HEALTH_URL="http://localhost:8803/health"
STATE_FILE="/tmp/healthcheck_failures.txt"
SERVICE_LABEL="com.myservice.llm"

HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$HEALTH_URL" || echo "000")

if [[ "$HTTP_CODE" == "200" ]]; then
    # Clear failure counter on success
    rm -f "$STATE_FILE"
    exit 0
fi

# Increment failure count
FAILURES=$(cat "$STATE_FILE" 2>/dev/null || echo "0")
FAILURES=$((FAILURES + 1))
echo "$FAILURES" > "$STATE_FILE"

logger -t health-check "FAIL #$FAILURES: $SERVICE_LABEL returned $HTTP_CODE"

if [[ "$FAILURES" -eq 1 ]]; then
    # First failure: alert only
    curl -s -d "$(hostname): $SERVICE_LABEL returned $HTTP_CODE (failure #1 — watching)" \
         -H "Title: Service Hiccup" \
         -H "Priority: low" \
         "$TOPIC"
elif [[ "$FAILURES" -ge 2 ]]; then
    # Second+ failure: alert and restart
    curl -s -d "$(hostname): $SERVICE_LABEL down $FAILURES checks — restarting" \
         -H "Title: Service Restarted" \
         -H "Priority: high" \
         -H "Tags: warning" \
         "$TOPIC"
    launchctl kickstart -k "system/$SERVICE_LABEL" || \
    launchctl kickstart -k "gui/$(id -u)/$SERVICE_LABEL"
fi
```

### Other notification options

- **macOS native notifications** via `osascript -e 'display notification "..." with title "..."'` — appears on desktop but not on phone
- **Email via curl/sendmail** — works but requires SMTP setup; harder to do quietly
- **Discord/Slack webhook** — same pattern as ntfy, curl POST to webhook URL
- **Pushover** — paid ($5 one-time) but more polished iOS/Android app than ntfy

ntfy.sh wins for simplicity: no account, no API key, works from any curl.

### launchd cannot send notifications

launchd has no built-in notification mechanism. There is no hook for "call this script when service X restarts." The watchdog script pattern above is the only way.

---

## 5. Complete Recommended Setup

### LaunchDaemon plist for a service (e.g., LLM server)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.studio.llm-server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/llm-server</string>
        <string>--port</string>
        <string>8803</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>/var/log/llm-server/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/llm-server/stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>/var/lib/llm-server</string>
</dict>
</plist>
```

### LaunchDaemon plist for the health-check watchdog

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.studio.llm-healthcheck</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/llm-healthcheck.sh</string>
    </array>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>/var/log/llm-server/healthcheck.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/llm-server/healthcheck.err</string>
</dict>
</plist>
```

### Quick-reference: essential management commands

```bash
# Load a daemon (system-level)
sudo launchctl bootstrap system /Library/LaunchDaemons/com.studio.llm-server.plist

# Restart a service
sudo launchctl kickstart -k system/com.studio.llm-server

# Check status
sudo launchctl list | grep studio

# View unified logs
log show --predicate 'subsystem == "com.apple.launchd"' --last 1h
log stream --predicate 'process == "llm-server"'

# Verify pmset settings stuck
pmset -g | grep -E "sleep|standby|hibernate|disablesleep"

# Check what's preventing sleep (or asserting wake)
pmset -g assertions
```

### After every macOS upgrade

pmset settings are sometimes silently reset after OS upgrades. Re-run the full pmset block after every upgrade. Green Mini host (a Mac server hosting company) specifically calls this out in their docs.

### Mac Studio vs Mac Mini — lid close doesn't apply

Mac Studio is a desktop. There is no lid. The lid-close sleep concern is a MacBook/MacBook Pro issue only. Mac Studio will not sleep from lid close.

---

## Sources

- `https://deepwiki.com/tjluoma/launchd-keepalive/2-keepalive-fundamentals` — launchd KeepAlive internals
- `https://gist.github.com/jrd404/a2164581c0e7454e4e0167620c6cf069` — MacBook Air M4 headless server gist (Tahoe)
- `https://www.agileguy.ca/content/files/2026/03/headless-mac-guide.html` — Mac Mini M4 headless guide
- `https://famstack.dev/guides/prepare-your-mac-as-a-home-server/` — Mac Studio home server guide
- `https://github.com/quanhua92/headless-mac-server` — scripted headless Mac setup (scripts 00–15)
- `https://github.com/glitchymagic/service-watchdog` — macOS hierarchical service watchdog (Python, 5-level escalation)
- `https://docs.ntfy.sh/examples/` — ntfy.sh curl patterns
- `https://linkconfig.com/blog/ntfy-self-hosted-push-notifications/` — ntfy self-hosted setup
- `https://salty.vip/articles/en/overview-of-macos-process-automation-methods/` — launchd vs supervisord vs PM2 comparison
- `https://pocketcmds.com/recipes/bash/bash-health-checker` — curl health check script patterns
