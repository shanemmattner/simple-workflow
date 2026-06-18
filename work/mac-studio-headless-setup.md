# Mac Studio Headless Server Setup

## Goal

Mac Studio auto-restarts after power loss, logs in automatically, starts all services — but if someone attaches a monitor they see a lock screen requiring a password.

## Steps

### 1. Disable FileVault

- System Settings → Privacy & Security → FileVault → Turn Off
- This takes hours to decrypt — let it finish before proceeding
- Why: FileVault requires password at boot, blocking auto-login

### 2. Enable auto-login

- Run: `sudo sysadminctl -autologin set -userName shane -password -`
- Will prompt for password
- Verify: restart and confirm it logs in without intervention

### 3. Lock screen immediately after login

Create a launchd agent that locks the screen right after auto-login:

File: `~/Library/LaunchAgents/com.pa.screen-lock.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.pa.screen-lock</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/osascript</string>
        <string>-e</string>
        <string>delay 5</string>
        <string>-e</string>
        <string>tell application "System Events" to keystroke "q" using {command down, control down}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
```

The 5-second delay ensures login completes before locking.

### 4. Require password immediately on wake/screen saver

- System Settings → Lock Screen → "Require password after screen saver begins or display is turned off" → Immediately
- Or: `sysadminctl -screenLock immediate -password -`

### 5. Run the server setup script

- `~/bin/mac-studio-server-setup.sh`
- This configures pmset, verifies services, etc.

## Result

Power outage → Mac boots → auto-login → services start via launchd → screen locks. SSH/Tailscale/web ports all work. Physical monitor shows lock screen.

## Note

Touch ID and Apple Pay are disabled with auto-login — irrelevant for a server.
