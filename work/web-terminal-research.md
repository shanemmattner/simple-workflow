# Web Terminal on Mac Studio for iPhone Access via Tailscale

**Goal**: browser-based terminal at `http://<tailscale-ip>:4082` where iOS voice dictation works in the input field, capable of running `claude` interactively.

**Date**: 2026-06-17

---

## The Critical Finding: iOS Dictation + xterm.js

**iOS voice dictation does NOT work correctly in raw xterm.js terminals.** All tools below (ttyd, wetty, gotty, shellinabox) use xterm.js or similar canvas-based rendering with a hidden textarea for input. iOS dictation into this hidden textarea produces **duplicate words** due to an xterm.js IME/composition event handling bug (the textarea fires compositionEnd events that replay text already committed).

This is a known, unresolved issue:
- [xterm.js #1101 - Support mobile platforms](https://github.com/xtermjs/xterm.js/issues/1101)
- [xterm.js #2403 - Accommodate predictive keyboard on mobile](https://github.com/xtermjs/xterm.js/issues/2403)
- [xterm.js #5377 - Limited touch support on mobile devices](https://github.com/xtermjs/xterm.js/issues/5377)

**The workaround**: Use a native HTML `<input>` or `<textarea>` field above the terminal where dictation works correctly, then send the composed text to the terminal/tmux session programmatically. This is exactly what [buckle42/claude-code-remote](https://github.com/buckle42/claude-code-remote) does.

---

## Recommendation: ttyd + claude-code-remote voice wrapper

### Why This Wins

The [claude-code-remote](https://github.com/buckle42/claude-code-remote) project solves exactly this problem. It combines:
- **ttyd** for the terminal rendering
- **tmux** for session persistence
- **FastAPI voice wrapper** that adds a native text input field above the terminal where iOS dictation works, then sends text to tmux
- **Quick-action buttons** for common keys (Escape, Tab, Enter, arrow keys)
- **Auto-reconnect** when the phone wakes from sleep
- **Tailscale binding** -- everything binds exclusively to the Tailscale IP

### Setup

```bash
# Install prerequisites
brew install ttyd tmux

# Clone the project
git clone https://github.com/buckle42/claude-code-remote.git
cd claude-code-remote

# Follow the README for configuration
# Key: set TAILSCALE_IP to 100.93.197.59 and PORT to 4082
```

The voice wrapper is a small FastAPI app (Python) that:
1. Embeds ttyd's terminal view in an iframe
2. Adds a native `<input>` field at the top where iOS dictation works perfectly
3. On submit, sends the text to the tmux session via `tmux send-keys`
4. Adds mobile-friendly quick-action buttons

### Alternative: DIY Minimal Version

If claude-code-remote is too heavy, the core pattern is simple:

```bash
# 1. Install ttyd
brew install ttyd

# 2. Start ttyd with tmux (writable, on Tailscale IP only)
ttyd -W -p 4082 -i 100.93.197.59 tmux new-session -A -s claude

# 3. Inside tmux, run claude
```

Then add a minimal HTML page served on a different port that has a real `<input>` field and sends text to tmux via a tiny API. This is ~50 lines of code.

---

## Option-by-Option Analysis

### 1. ttyd (RECOMMENDED as base layer)

| Attribute | Details |
|-----------|---------|
| **Install** | `brew install ttyd` -- confirmed in Homebrew, ARM64 native bottle |
| **macOS ARM64** | Yes, works natively. [ttyd-launchd](https://github.com/bhrutledge/ttyd-launchd) project specifically targets macOS launchd |
| **Launch command** | `ttyd -W -p 4082 -i 100.93.197.59 tmux new-session -A -s claude` |
| **Interactive CLI** | Yes, runs claude interactively when wrapping tmux or bash |
| **Colors/cursor** | Full xterm emulation via xterm.js |
| **iOS dictation** | Broken in raw terminal (duplicate words). Needs voice wrapper |
| **Config lines** | 1 command to launch |
| **Auth** | `-c user:pass` available but not needed with Tailscale |

**Flags**:
- `-W` = writable (default is read-only)
- `-p 4082` = port
- `-i 100.93.197.59` = bind to Tailscale IP only (security)
- Wrap with tmux so sessions persist across browser reconnects

**launchd service**: The [ttyd-launchd](https://github.com/bhrutledge/ttyd-launchd) project provides a macOS launchd plist for auto-start on boot.

### 2. wetty

| Attribute | Details |
|-----------|---------|
| **Install** | `npm install -g wetty` (last published 3+ years ago, v2.7.0) |
| **macOS ARM64** | Should work (pure Node.js + node-pty native addon) |
| **Launch command** | `wetty --port 4082 --host 100.93.197.59 --base /` |
| **Interactive CLI** | Yes |
| **Colors/cursor** | Full xterm.js emulation |
| **iOS dictation** | Same xterm.js issue -- broken without wrapper |
| **Config lines** | ~3-5 lines |
| **Auth** | SSH-based by default (connects to local sshd), can bypass |

**Problems**: Unmaintained (3+ years stale). Requires SSH to be running. Heavier dependency chain (Node.js + native build tools for node-pty). No advantage over ttyd for this use case.

### 3. gotty

| Attribute | Details |
|-----------|---------|
| **Install** | Binary download from [releases](https://github.com/sorenisanerd/gotty/releases) -- `gotty_v1.6.0_darwin_arm64.tar.gz` available. Also `brew install yudai/gotty/gotty` (but this may be the older fork) |
| **macOS ARM64** | Yes, darwin_arm64 binary available |
| **Launch command** | `gotty -w -p 4082 -a 100.93.197.59 tmux new-session -A -s claude` |
| **Interactive CLI** | Yes with `-w` (write) flag |
| **Colors/cursor** | Uses hterm (not xterm.js) |
| **iOS dictation** | Likely same class of problem (hterm also uses hidden textarea approach) |
| **Config lines** | 1 command |
| **Auth** | Basic auth available via flags |

**Problems**: Uses hterm instead of xterm.js -- potentially different (possibly worse) mobile input behavior. Less actively maintained than ttyd. The original yudai/gotty is abandoned; sorenisanerd fork is the active one.

### 4. xterm.js + custom Bun server

| Attribute | Details |
|-----------|---------|
| **Install** | `bun add @xterm/xterm @xterm/addon-fit node-pty` |
| **macOS ARM64** | node-pty has native compilation -- works on ARM64 with Xcode CLI tools |
| **Launch command** | Custom server script |
| **Interactive CLI** | Yes |
| **Colors/cursor** | Full xterm.js |
| **iOS dictation** | Same xterm.js issue UNLESS you build a custom input field (which is the whole point) |
| **Config lines** | ~100-150 lines for a minimal server + client |
| **Auth** | Roll your own |

**This is what claude-code-remote essentially does** (but with FastAPI/Python instead of Bun). If you want a Bun-native version, the architecture is:
- Server: Bun WebSocket server + node-pty spawning tmux
- Client: xterm.js canvas terminal + a visible `<input>` field for dictation
- The `<input>` field captures dictated text, then sends it to the PTY via WebSocket

This is the cleanest path if you want full control, but claude-code-remote already solved it.

### 5. shellinabox

| Attribute | Details |
|-----------|---------|
| **Install** | `brew install shellinabox` (available but ancient) |
| **macOS ARM64** | Questionable -- has [known build issues on macOS](https://github.com/shellinabox/shellinabox/issues/47), unmaintained |
| **Launch command** | `shellinaboxd -p 4082 -s /:LOGIN` |
| **Interactive CLI** | Yes |
| **Colors/cursor** | Custom AJAX terminal, not xterm.js |
| **iOS dictation** | Uses its own rendering -- untested, likely problematic |
| **Config lines** | ~2-3 lines |
| **Auth** | PAM-based (heavier than needed) |

**Verdict**: Dead project. Skip.

### 6. code-server (VS Code web)

| Attribute | Details |
|-----------|---------|
| **Install** | `brew install code-server` |
| **macOS ARM64** | Yes, Homebrew ARM64 bottle available |
| **Launch command** | `code-server --bind-addr 100.93.197.59:4082 --auth none` |
| **Interactive CLI** | Yes, via integrated terminal |
| **Colors/cursor** | Full VS Code terminal emulation |
| **iOS dictation** | VS Code terminal uses xterm.js internally -- same problem. BUT the VS Code command palette search bar and editor text areas DO support dictation |
| **Config lines** | 1 command + config.yaml |
| **Auth** | Password by default, `--auth none` for Tailscale |

**Problems**: Heavy (pulls full VS Code). Overkill for terminal-only use. The terminal pane itself has the same xterm.js dictation issue. Memory footprint is 500MB+ vs ttyd's ~5MB.

### 7. Bonus: lhymes/claude-web-terminal

| Attribute | Details |
|-----------|---------|
| **Install** | `git clone` + `./install-local.sh` |
| **macOS ARM64** | Yes, explicitly supports macOS |
| **Architecture** | ttyd + tmux + Tailscale, multi-instance with color themes |
| **iOS dictation** | Same raw xterm.js -- no voice wrapper |
| **Config lines** | One install script |

Nice multi-instance management (color-coded sessions, auto-restart via launchd, healthcheck every 30s) but does NOT solve the dictation problem.

### 8. Bonus: shell-now (STRRL)

| Attribute | Details |
|-----------|---------|
| **Install** | `brew tap strrl/tap && brew install shell-now` |
| **macOS ARM64** | Yes |
| **Purpose** | Exposes terminal to public internet (ngrok-style) |
| **iOS dictation** | Same xterm.js issue |
| **Caveat** | Safari compatibility issues noted. Public exposure is wrong model for Tailscale |

Not appropriate -- designed for public sharing, not private Tailscale access.

---

## Concrete Setup Plan

### Phase 1: Get a working terminal (5 minutes)

```bash
# On Mac Studio
brew install ttyd tmux

# Start it (bind to Tailscale IP only)
ttyd -W -p 4082 -i 100.93.197.59 tmux new-session -A -s main
```

Open `http://100.93.197.59:4082` on iPhone. Terminal works. Typing works. Colors work. `claude` runs interactively.

**Dictation will produce duplicate words at this stage.** This is usable for quick commands but not for dictation-heavy use.

### Phase 2: Add voice wrapper for dictation (15 minutes)

```bash
# Clone the solution
git clone https://github.com/buckle42/claude-code-remote.git
cd claude-code-remote

# Follow setup -- key pieces:
# 1. ttyd serves terminal on an internal port (e.g., 4083)
# 2. FastAPI wrapper serves on 4082, embeds ttyd iframe + native input field
# 3. Native input field -> tmux send-keys
```

The voice wrapper gives you:
- A real `<input>` field at top of page where iOS dictation works perfectly
- Press Enter or tap Send to inject text into the tmux/terminal session
- Quick-action buttons for Escape, Tab, Ctrl+C, arrow keys
- Auto-reconnect on phone wake

### Phase 3: Persistence via launchd

Create a launchd plist so ttyd + wrapper auto-start on boot and restart on crash. The [ttyd-launchd](https://github.com/bhrutledge/ttyd-launchd) project has a template.

---

## Summary

| Option | Install Effort | iOS Dictation | Recommendation |
|--------|---------------|---------------|----------------|
| **ttyd (raw)** | 1 command | Broken (duplicates) | Good base, needs wrapper |
| **ttyd + claude-code-remote** | 10 min | Works via native input | **Best option** |
| **wetty** | npm install | Broken | Skip (stale, no advantage) |
| **gotty** | Binary download | Likely broken | Skip (less maintained than ttyd) |
| **Custom Bun + xterm.js** | ~150 LOC | Works if you build input field | Only if you want full control |
| **shellinabox** | brew | Unknown, likely broken | Skip (dead project) |
| **code-server** | brew | Broken in terminal pane | Overkill for this use case |
| **claude-web-terminal** | install script | Broken | Nice multi-instance, no dictation fix |

**Winner**: `ttyd` + the [claude-code-remote](https://github.com/buckle42/claude-code-remote) voice wrapper. It was built specifically for running Claude Code from an iPhone over Tailscale with working iOS dictation. The voice wrapper pattern (native input field above xterm.js terminal, sending text via tmux) is the only proven solution to the iOS dictation + web terminal problem.
