# Web Terminal Fix: Voice Dictation + Mobile Controls

**Date**: 2026-06-17
**Status**: Deployed and running

---

## What Was Deployed

A voice wrapper (adapted from [buckle42/claude-code-remote](https://github.com/buckle42/claude-code-remote)) is now running on the Mac Studio alongside the existing ttyd terminal.

| Service | URL | Port |
|---------|-----|------|
| **Voice UI** (use this on iPhone) | `http://mac-studio:4083` | 4083 |
| Raw ttyd terminal | `http://mac-studio:4082` | 4082 |

Both services bind to the Tailscale IP (`100.93.197.59`) only.

## Problem 1: iOS Voice Dictation (FIXED)

**Root cause**: xterm.js uses a hidden `<textarea>` for input. iOS dictation fires `compositionEnd` events that replay already-committed text, producing duplicate words. This is a known unresolved xterm.js bug (#1101, #2403, #5377).

**Solution**: The voice wrapper serves a page with:
- The ttyd terminal embedded in an `<iframe>` (for visual output)
- A native HTML `<textarea>` at the bottom where iOS dictation works correctly
- On submit, text is sent to the `webterminal` tmux session via `tmux send-keys`

Dictation goes into the native textarea (where iOS handles it properly), then the composed text is injected into tmux. The xterm.js composition bug is completely bypassed.

## Problem 2: Backspace / Text Selection / Mobile Keys (FIXED)

**Root cause**: xterm.js has minimal mobile touch support. Backspace, arrow keys, and other control keys are hard or impossible to trigger from iPhone's on-screen keyboard.

**Solution**: On-screen quick-action button bar with:
- **Del** (backspace) -- sends `BSpace` to tmux
- **Left/Right arrows** -- cursor movement
- **Up/Down arrows** -- command history
- **Tab** -- autocomplete
- **Esc** -- escape key
- **^C** -- Ctrl+C (highlighted in red)
- **Enter** -- enter key
- **Clear** -- Ctrl+L (clear screen)
- **Home/End** -- Ctrl+A / Ctrl+E (jump to line start/end)
- **Kill** -- Ctrl+U (kill line)
- **Copy** -- captures full tmux scrollback into a selectable overlay (long-press to copy)
- **Camera** -- upload photos (compressed client-side, saved to `/tmp/claude-uploads/`)

All buttons send keys directly to tmux, bypassing xterm.js input handling entirely.

## Additional Features

- **Auto-reconnect**: When the phone wakes from sleep, the iframe auto-reloads
- **Photo upload**: Camera button lets you upload images to the Mac Studio (useful for Claude Code vision tasks)
- **Multi-line input**: Shift+Enter in the textarea adds a newline; Enter sends
- **Copy pane**: Full tmux scrollback captured into a native textarea overlay where iOS text selection works

## Files on Mac Studio

```
~/.local/bin/remote-cli/
  voice-wrapper.py       # FastAPI app (the voice UI)
  start.sh               # Starts ttyd + voice wrapper + caffeinate, with ttyd watchdog
  stop.sh                # Stops all services (preserves tmux session)
  logs/                  # ttyd.log, voice-wrapper.log, PID files

~/Library/LaunchAgents/com.user.remote-cli.plist   # Auto-start on boot
```

## How It Works

```
iPhone Safari
  -> http://mac-studio:4083  (voice wrapper, FastAPI)
       |
       +-- <iframe> loads http://mac-studio:4082 (ttyd, terminal display)
       |
       +-- <textarea> at bottom (native iOS dictation works here)
       |
       +-- Button bar (sends keys via POST /key -> tmux send-keys)
       |
       +-- "Send" button (POST /send -> tmux send-keys -l "text" + Enter)
```

## Operations

```bash
# Start (if not running)
ssh studio "~/.local/bin/remote-cli/start.sh"

# Stop
ssh studio "~/.local/bin/remote-cli/stop.sh"

# Check status
ssh studio "lsof -i :4082 -i :4083 | grep LISTEN"

# View logs
ssh studio "tail -20 ~/.local/bin/remote-cli/logs/voice-wrapper.log"
ssh studio "tail -20 ~/.local/bin/remote-cli/logs/ttyd.log"

# Reload launchd (after editing plist)
ssh studio "launchctl unload ~/Library/LaunchAgents/com.user.remote-cli.plist"
ssh studio "launchctl load ~/Library/LaunchAgents/com.user.remote-cli.plist"
```

## ttyd Settings Applied

The ttyd instance was restarted with mobile-optimized settings:
- `fontSize=16` (down from 18, better fit on iPhone)
- `lineHeight=1.2`
- `cursorBlink=true`, `cursorStyle=block`
- `scrollback=10000`
- Bound to Tailscale IP only (`-i 100.93.197.59`)

## What Did NOT Work / Alternatives Considered

- **ttyd mobile settings alone**: ttyd has no built-in mobile keyboard support. The `-W` (writable) flag enables input but doesn't fix iOS dictation or provide on-screen controls.
- **Other web terminals** (wetty, gotty, shellinabox): All use xterm.js or similar hidden-textarea input. Same dictation bug. No advantage.
- **code-server**: Uses xterm.js internally. 500MB+ memory for terminal-only use. Overkill.
- **Custom xterm.js + Bun server**: Would work but claude-code-remote already solved it. No need to rebuild.
