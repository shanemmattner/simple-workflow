# Mac Studio: Auto Power-On + Auto-Login After Power Outage

Researched 2026-06-17. Sources: Apple Support, Der Flounder, UMA Technology, Agileguy headless Mac guide, Astropad headless guide, HomeTechOps.

---

## 1. Auto Power-On After Power Loss

### The setting

Two distinct behaviors — both needed for a truly unattended server:

| Behavior | Setting name | When to use |
|---|---|---|
| Restart after kernel panic / OS crash | "Restart automatically if the computer freezes" | Any Mac |
| Power on when AC is restored after outage | "Start up automatically after a power failure" | Desktop Macs only |

### GUI path (Ventura / Sonoma / Tahoe)

System Settings → Energy Saver → **Start up automatically after a power failure** → ON

### Terminal commands

**Standard (all macOS, works on M4 Mac Studio):**
```bash
sudo pmset -a autorestart 1
```

Verify:
```bash
pmset -g | grep autorestart
# Expected output: autorestart    1
```

Disable:
```bash
sudo pmset -a autorestart 0
```

**New in macOS Tahoe 26.5 (Mac Studio 2025+, Mac mini 2024+, iMac 2024+):**

A second, finer-grained setting was added — auto power-on whenever AC is connected (not just after outage):

```bash
sudo pmset autorestartatconnect 1
```

Verify:
```bash
pmset -g | awk '/autorestartatconnect/ {print $2}' | sed '/^$/d'
# Expected: 1
```

This maps to "Start up when power is connected: Always" in Energy Saver (Tahoe only). The older `autorestart` toggle still exists alongside it.

### Apple Silicon / M4 gotchas

- `autorestart` works on M4 Mac Studio — confirmed in community reports and Apple docs.
- The `autorestartatconnect` option is confirmed available on **Mac Studio introduced 2025 or later** per Apple's KBase article.
- Neither setting is MDM-manageable as of Tahoe 26.5 — use `pmset` for scripted deployment.
- Some Apple Community threads report that `pmset` changes in Recovery Mode don't persist correctly (they reset to 1 even if set to 0). Always verify with `pmset -g` after setting.
- On MacBook Pros: the `autorestart` option may not appear or may not work because the battery handles brief power loss. Mac Studio has no battery, so it behaves like a proper desktop.

---

## 2. Auto-Login (No Password at Boot)

### The FileVault catch-22 (critical)

FileVault is enabled by default on all new Apple Silicon Macs. **FileVault and auto-login are mutually exclusive** — macOS grays out the auto-login option when FileVault is on. After a reboot with FileVault enabled, the Mac stops at a pre-boot decryption screen with no network access. A headless Mac Studio stuck here requires a keyboard physically plugged in.

**Decision:**
- **Disable FileVault** → auto-login works → fully unattended after power outage. Acceptable if the Mac is in a physically secure location (home, locked office, rack).
- **Keep FileVault** → every reboot/power outage requires physical keyboard input. Not viable for autonomous server use.

For a Mac Studio running as an always-on agent server in a secure location: **disable FileVault**.

### Disable FileVault

```
System Settings → Privacy & Security → FileVault → Turn Off
```

This initiates background decryption. Wait for it to complete before enabling auto-login (may take hours on a full disk). Check progress in the same pane.

### Enable auto-login via GUI (Ventura/Sonoma/Tahoe)

```
System Settings → Users & Groups → Automatically log in as → [select user]
```

Enter the account password when prompted.

Note: **The old `defaults write` method no longer works in Sequoia/Sonoma/Tahoe.** Use either the GUI or `sysadminctl` (see below).

### Enable auto-login via command line (Ventura+)

As of macOS Ventura 13.2.1, `sysadminctl` supports auto-login natively:

```bash
# Enable (prompts for password interactively — preferred)
sudo sysadminctl -autologin set -userName YOUR_USERNAME -password -

# Enable (non-interactive, password in plaintext — for scripted setups)
sudo sysadminctl -autologin set -userName YOUR_USERNAME -password YOUR_PASSWORD

# Check status
sysadminctl -autologin status

# Disable
sudo sysadminctl -autologin off
```

This replaces all older methods (kcpassword scripts, `defaults write`). Under the hood it still creates `/etc/kcpassword` (XOR-obfuscated password, not real encryption) and writes the `autoLoginUser` key to `/Library/Preferences/com.apple.loginwindow`.

### Side effects of enabling auto-login

Apple warns in a GUI prompt: **Touch ID is disabled and Apple Pay is removed** when auto-login is on. This is expected and acceptable for a server Mac.

### Post-login screen lock

After auto-login completes, the Mac will be logged in but may still require a password after the screensaver or display sleep. For a headless server, disable this:

```
System Settings → Lock Screen → "Require password after screen saver begins or display is turned off" → Never
```

Or via Terminal:
```bash
# Disable screen saver idle timeout
defaults -currentHost write com.apple.screensaver idleTime 0
```

---

## 3. Standard Practice for Headless Mac Servers

From HomeTechOps, Astropad, Agileguy, and community forums (MacRumors, TidBITS Talk, Reddit), the consensus setup for a Mac Studio/Mini as an always-on headless server is:

### The canonical checklist (do in order, with monitor attached)

```bash
# 1. Enable SSH
sudo systemsetup -setremotelogin on

# 2. Configure power management — never sleep
sudo pmset -a \
  sleep 0 \
  displaysleep 0 \
  disksleep 0 \
  womp 1 \
  tcpkeepalive 1 \
  powernap 0

# 3. Enable auto-restart after power loss
sudo pmset -a autorestart 1

# For Mac Studio 2025 / macOS Tahoe 26.5+, also:
sudo pmset autorestartatconnect 1

# 4. Verify pmset settings
pmset -g

# 5. Disable FileVault (GUI — then wait for decryption to complete)
# System Settings → Privacy & Security → FileVault → Turn Off

# 6. Enable auto-login (GUI — after FileVault is fully decrypted)
# System Settings → Users & Groups → Automatically log in as → [user]

# OR via command line (Ventura+):
sudo sysadminctl -autologin set -userName YOUR_USERNAME -password -

# 7. Disable screen lock
defaults -currentHost write com.apple.screensaver idleTime 0

# 8. Set hostname
sudo scutil --set ComputerName "mac-studio"
sudo scutil --set LocalHostName "mac-studio"
sudo scutil --set HostName "mac-studio"
```

### Services: LaunchDaemons, not Login Items

The consensus is: **never use "Open at Login" for server processes.** Login Items only run after a user session is established and are not reliably restarted on crash.

Use `launchd` instead:
- `/Library/LaunchDaemons/` — runs as root at boot, before login. Use for system services (SSH servers, databases, network services).
- `~/Library/LaunchAgents/` or `/Library/LaunchAgents/` — runs after user login. Use for GUI apps or agents that need a user session (Claude Code, anything using WindowServer).

Minimum plist structure:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.example.myservice</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/myservice</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key>
  <string>/var/log/myservice/stdout.log</string>
  <key>StandardErrorPath</key>
  <string>/var/log/myservice/stderr.log</string>
</dict>
</plist>
```

Load it:
```bash
sudo chown root:wheel /Library/LaunchDaemons/com.example.myservice.plist
sudo chmod 644 /Library/LaunchDaemons/com.example.myservice.plist
sudo launchctl load -w /Library/LaunchDaemons/com.example.myservice.plist
```

### HDMI dummy plug

Apple Silicon Mac Minis and Mac Studios handle headless operation better than Intel Macs did, but without a display they default to 1920x1080 1x (non-Retina). For CLI-only SSH use this doesn't matter. For remote desktop (Screen Sharing, VNC, RDP), insert a cheap HDMI dummy plug ($8–15) or use BetterDisplay to create a virtual Retina display.

### UPS recommendation

Multiple sources recommend pairing `autorestart` with a UPS. The UPS bridges short outages without rebooting at all; `autorestart` handles longer outages where the UPS is exhausted. Together they cover the full range of power events.

---

## Summary: Exact commands to run on Mac Studio (M4 Max)

```bash
# Run these in order (with monitor attached first)

# SSH access
sudo systemsetup -setremotelogin on

# Never sleep
sudo pmset -a sleep 0 displaysleep 0 disksleep 0 womp 1 tcpkeepalive 1 powernap 0

# Auto power-on after outage (standard)
sudo pmset -a autorestart 1

# Auto power-on when AC reconnected (Mac Studio 2025+ / Tahoe 26.5+)
sudo pmset autorestartatconnect 1

# Verify
pmset -g | grep -E "autorestart|sleep|displaysleep"

# Disable FileVault via GUI, wait for completion, then:
sudo sysadminctl -autologin set -userName YOUR_USERNAME -password -

# Verify auto-login
sysadminctl -autologin status

# Disable screen lock after idle
defaults -currentHost write com.apple.screensaver idleTime 0
```

After completing this, power-cycle the Mac Studio without a keyboard/mouse and confirm it boots to the desktop and services are running within ~2 minutes.
