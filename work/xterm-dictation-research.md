# iOS Voice Dictation Duplicate Word Problem in xterm.js / ttyd

Research date: 2026-06-17

## Root Cause Analysis

The duplicate word problem has **two independent causes** that compound on iOS:

### Cause 1: WebKit Bug -- iOS Dictation Skips Composition Events

**WebKit Bug 261764** (filed 2023-09-19, status: NEW/unresolved, P2):
iOS/iPadOS dictation does NOT fire `compositionstart`, `compositionupdate`, or `compositionend` events. Only `beforeinput` and `input` events fire. This is a Safari/WebKit bug -- macOS Safari and Chrome/Firefox on all platforms fire composition events correctly during dictation.

This means xterm.js's `CompositionHelper` never activates during iOS dictation. The helper's `isComposing` flag stays false, so dictated text bypasses the composition pipeline entirely and gets processed through both the `input` event path AND the direct text-change detection path simultaneously, producing duplicates.

### Cause 2: xterm.js CompositionHelper Double-Commit Bug

Even when composition events DO fire (Android, CJK IME), xterm.js has a known double-commit bug. In `CompositionHelper.keydown()` (lines 115-139), when a non-composition keydown arrives mid-composition, the helper finalizes the composition synchronously AND lets the keydown fall through. The finalized composition contains the same character the keydown is about to emit, producing duplicates. This affects Android/GBoard heavily (issue #3600, open since 2022, still broken in xterm.js 6.0.0 as of 2026-05).

### The Compound Effect on iOS

iOS dictation inserts text via direct DOM manipulation (no composition events due to WebKit bug). xterm.js sees the textarea value change in `_handleAnyTextareaChanges()` and emits the text. But iOS Safari may also fire `input`/`beforeinput` events that xterm.js processes separately, causing double emission.

---

## Angle 1: Fix xterm.js Directly

### Status of Upstream Fixes

- **Issue #1101** (Support mobile platforms): Open since 2017. Maintainer closed #2403 as duplicate of this. No fix landed. Maintainer response: "very low on my priority list."
- **Issue #2403** (Accommodate predictive keyboard): Closed 2019 as duplicate of #1101. The `type='password'` workaround was proposed but rejected for accessibility reasons.
- **Issue #3600** (Android erratic text): Open since 2022. PR #4007 partially fixed single-char backspace but the core double-commit on composition finalization remains.
- **Issue #5377** (Limited touch support): Open since 2025. Proposes a `TouchHandlingService` but no implementation.

### No Working Upstream Patch Exists

The maintainer (Tyriar) has consistently deprioritized mobile/dictation issues. There is no merged fix for the composition double-commit, and the WebKit bug (no composition events during dictation) means even a perfect CompositionHelper wouldn't help iOS dictation.

### Proposed xterm.js-Level Fix

Intercept at the textarea level BEFORE xterm.js processes events. The key insight from the dolonet comment on #3600:

> `CompositionHelper.keydown` at L115-L139 finalizes the composition synchronously when a non-composition keydown arrives mid-composition, then lets the keydown fall through. On GBoard, the finalized composition contains the same character the keydown is about to emit, so you get the duplicate.

A monkey-patch approach that intercepts the textarea:

```javascript
// After terminal.open(), find the hidden textarea
const textarea = document.querySelector('.xterm-helper-textarea');
if (!textarea) return;

let lastInputValue = '';
let lastInputTime = 0;
const DEDUP_WINDOW_MS = 100;

// Intercept input events to deduplicate
textarea.addEventListener('input', (e) => {
  const now = Date.now();
  const currentValue = textarea.value;
  
  // If the same value was just processed within the dedup window, suppress
  if (currentValue === lastInputValue && (now - lastInputTime) < DEDUP_WINDOW_MS) {
    e.stopImmediatePropagation();
    textarea.value = '';
    return;
  }
  
  lastInputValue = currentValue;
  lastInputTime = now;
}, true); // capture phase -- fires before xterm.js handler

// For iOS dictation specifically: since no composition events fire,
// intercept beforeinput to track dictation insertions
textarea.addEventListener('beforeinput', (e) => {
  if (e.inputType === 'insertText' || e.inputType === 'insertFromDictation') {
    // Mark that we're handling dictation input
    textarea.dataset.dictationPending = e.data || '';
  }
}, true);
```

**Limitation**: This is fragile. xterm.js's internal textarea management can reset the textarea, and the event ordering varies by iOS version.

---

## Angle 2: ttyd Configuration

### Available Hooks

1. **`--index <path>`**: Serve a custom `index.html` that includes arbitrary JS. This is the primary injection point.
2. **`--client-option key=value`**: Pass xterm.js `ITerminalOptions`. Useful options:
   - `disableStdin=false` (default)
   - No option exists for composition handling or mobile input mode
3. **`window.term`**: ttyd exposes the Terminal instance as `window.term` globally. This means post-initialization JS can access the terminal object directly.

### Custom index.html Approach

ttyd's `--index` flag lets you serve a completely custom HTML page. The page must implement the ttyd WebSocket protocol, but you can wrap it with any JS you want.

**Practical approach**: Copy ttyd's built-in HTML (it's a single-page React/Preact app compiled to a bundle), add a `<script>` tag at the end that patches the textarea after the terminal opens.

```bash
# Start ttyd with custom index.html
ttyd --index /path/to/custom-index.html tmux attach
```

The custom index.html would include the original ttyd bundle plus a composition fix script (see Angle 3).

### Client Options That Help (Marginally)

```bash
# These don't fix dictation but improve mobile experience
ttyd -t rendererType=dom \
     -t disableResizeOverlay=true \
     -t fontSize=16 \
     tmux attach
```

The `rendererType=dom` avoids WebGL/canvas issues on mobile Safari. Larger fontSize helps touch accuracy.

---

## Angle 3: JavaScript Interception (Most Promising for Quick Fix)

### Approach A: Post-Init Monkey Patch via Bookmarklet

Since `window.term` is available in ttyd, a bookmarklet can patch the terminal after page load:

```javascript
javascript:void(function(){
  /* iOS xterm.js dictation dedup patch */
  var ta = document.querySelector('.xterm-helper-textarea');
  if (!ta) { alert('No xterm textarea found'); return; }
  
  var seen = {};
  var WINDOW = 150; /* ms dedup window */
  
  /* Wrap the textarea's value setter to detect duplicate writes */
  var desc = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
  var origSet = desc.set;
  var origGet = desc.get;
  
  Object.defineProperty(ta, 'value', {
    get: function() { return origGet.call(this); },
    set: function(v) {
      var now = Date.now();
      var key = v.trim();
      if (key && seen[key] && (now - seen[key]) < WINDOW) {
        /* Duplicate within window -- skip */
        console.log('[dedup] suppressed:', JSON.stringify(v));
        return;
      }
      if (key) seen[key] = now;
      /* Clean old entries */
      for (var k in seen) {
        if (now - seen[k] > 1000) delete seen[k];
      }
      return origSet.call(this, v);
    }
  });
  
  alert('Dictation dedup patch active');
})();
```

**iOS Safari limitation**: Bookmarklets work on iOS Safari but Apple restricts their execution in some contexts. The bookmarklet must be saved as a bookmark, then the URL edited to contain the `javascript:` code.

### Approach B: Visible Input Field Overlay (Most Robust)

Replace the hidden textarea with a visible input field that the user dictates into, then forward completed text to the terminal:

```javascript
// Inject after ttyd loads
(function() {
  const term = window.term;
  if (!term) return;
  
  // Create visible input bar at bottom of screen
  const inputBar = document.createElement('div');
  inputBar.style.cssText = `
    position: fixed; bottom: 0; left: 0; right: 0;
    background: #1a1a2e; padding: 8px; z-index: 9999;
    display: flex; gap: 8px;
  `;
  
  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = 'Dictate or type here...';
  input.autocomplete = 'off';
  input.autocorrect = 'on';  // let iOS correct, just don't duplicate
  input.spellcheck = false;
  input.style.cssText = `
    flex: 1; padding: 12px; font-size: 16px;
    background: #16213e; color: #e0e0e0; border: 1px solid #0f3460;
    border-radius: 6px; font-family: monospace;
  `;
  
  const sendBtn = document.createElement('button');
  sendBtn.textContent = 'Send';
  sendBtn.style.cssText = `
    padding: 12px 20px; font-size: 16px;
    background: #0f3460; color: white; border: none;
    border-radius: 6px; cursor: pointer;
  `;
  
  // Send on button click or Enter key
  function sendInput() {
    const text = input.value;
    if (!text) return;
    // Write directly to terminal data handler (bypasses composition entirely)
    term.input(text + '\r', true);
    input.value = '';
    input.focus();
  }
  
  sendBtn.addEventListener('click', sendInput);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      sendInput();
    }
    // Forward special keys
    if (e.key === 'Tab') {
      e.preventDefault();
      term.input('\t', true);
    }
  });
  
  inputBar.appendChild(input);
  inputBar.appendChild(sendBtn);
  document.body.appendChild(inputBar);
  
  // Adjust terminal to not overlap with input bar
  const termContainer = document.querySelector('.terminal');
  if (termContainer) {
    termContainer.style.paddingBottom = '60px';
    term.fit();
  }
  
  // Prevent the hidden textarea from stealing focus on iOS
  const hiddenTextarea = document.querySelector('.xterm-helper-textarea');
  if (hiddenTextarea) {
    hiddenTextarea.addEventListener('focus', (e) => {
      // Only redirect if our input bar is being used
      if (document.activeElement === input) {
        e.preventDefault();
      }
    });
  }
})();
```

**Why this works**: iOS dictation into a standard `<input type="text">` element works correctly -- no composition event bugs, no duplicates. The dictated text goes into the input field cleanly, and we forward it to the terminal via `term.input()` (xterm.js API) which writes directly to the data handler, completely bypassing the composition pipeline.

### Approach C: Composition Event Interceptor (Surgical)

Intercept and normalize composition events before xterm.js sees them:

```javascript
(function() {
  const ta = document.querySelector('.xterm-helper-textarea');
  if (!ta) return;
  
  let composing = false;
  let composedText = '';
  let lastSentText = '';
  let lastSentTime = 0;
  
  // Capture phase -- fires before xterm.js handlers
  ta.addEventListener('compositionstart', () => {
    composing = true;
    composedText = '';
  }, true);
  
  ta.addEventListener('compositionupdate', (e) => {
    composedText = e.data || '';
  }, true);
  
  ta.addEventListener('compositionend', (e) => {
    composing = false;
    composedText = e.data || '';
    lastSentText = composedText;
    lastSentTime = Date.now();
  }, true);
  
  // Intercept input events to prevent double-processing
  ta.addEventListener('input', (e) => {
    const now = Date.now();
    // If we just finished composition and the input event carries the same text,
    // this is the duplicate -- suppress it
    if (!composing && lastSentText && 
        e.data === lastSentText && 
        (now - lastSentTime) < 100) {
      e.stopImmediatePropagation();
      e.preventDefault();
      lastSentText = '';
      return false;
    }
  }, true);
  
  // For iOS dictation where NO composition events fire:
  // Use beforeinput to detect dictation-specific input types
  ta.addEventListener('beforeinput', (e) => {
    if (e.inputType === 'insertFromDictation' || 
        e.inputType === 'insertReplacementText') {
      // iOS dictation detected -- mark it so we can dedup
      ta.dataset.dictationInsert = e.data || '';
    }
  }, true);
})();
```

**Note on iOS dictation detection**: The `insertFromDictation` inputType is not consistently implemented across iOS versions. Some versions use `insertText` for dictation too, making it indistinguishable from keyboard input at the event level.

---

## Angle 4: Local LLM / Server-Side Deduplication

### Concept: tmux Input Pipe Dedup Filter

Instead of fixing the browser, accept the doubled input and deduplicate server-side before it reaches the shell:

```bash
#!/bin/bash
# dedup-filter.sh -- sits between tmux input and the shell
# Usage: tmux pipe-pane -I "bash /path/to/dedup-filter.sh"

declare -A seen
WINDOW=0.3  # seconds

while IFS= read -r -n1 char; do
  now=$(date +%s.%N)
  key="$char"
  
  if [[ -n "${seen[$key]}" ]]; then
    elapsed=$(echo "$now - ${seen[$key]}" | bc)
    if (( $(echo "$elapsed < $WINDOW" | bc -l) )); then
      continue  # skip duplicate
    fi
  fi
  
  seen[$key]="$now"
  printf '%s' "$char"
done
```

**Why this doesn't work well**: The duplication happens at the word level, not character level. iOS dictation sends "hello" and then xterm.js sends "hello" again. A character-level filter can't distinguish "hello" typed intentionally twice from "hello" duplicated by the bug. Also, tmux's `pipe-pane -I` is for input, but the doubled text is already committed to the PTY by the time tmux sees it.

### Better Server-Side Approach: readline Wrapper

A more practical server-side approach would wrap the shell with a readline proxy:

```python
#!/usr/bin/env python3
"""
dedup_readline.py -- Wrap a shell, deduplicating rapid identical inputs.
Run: python3 dedup_readline.py bash
Use as: ttyd python3 dedup_readline.py bash
"""
import sys, os, time, pty, select, re

DEDUP_WINDOW = 0.2  # seconds
last_line = ''
last_time = 0

def dedup(data: bytes) -> bytes:
    global last_line, last_time
    now = time.time()
    text = data.decode('utf-8', errors='replace')
    
    # Check for word-level duplication: "hello hello" -> "hello"
    words = text.split()
    if len(words) >= 2:
        deduped = []
        prev = None
        for w in words:
            if w != prev or (now - last_time) > DEDUP_WINDOW:
                deduped.append(w)
            prev = w
        text = ' '.join(deduped)
    
    # Check for line-level duplication
    if text.strip() == last_line.strip() and (now - last_time) < DEDUP_WINDOW:
        return b''
    
    last_line = text
    last_time = now
    return text.encode('utf-8')

def main():
    shell = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('SHELL', '/bin/bash')
    pid, fd = pty.fork()
    
    if pid == 0:
        os.execvp(shell, [shell])
    
    try:
        while True:
            r, _, _ = select.select([sys.stdin.buffer, fd], [], [])
            if sys.stdin.buffer in r:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    break
                data = dedup(data)
                if data:
                    os.write(fd, data)
            if fd in r:
                data = os.read(fd, 4096)
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
    except OSError:
        pass

if __name__ == '__main__':
    main()
```

**Verdict**: Server-side dedup is inherently fragile because it can't distinguish intentional repetition from bug-induced duplication. It would suppress legitimate repeated commands ("ls" then "ls" again quickly). Not recommended as a primary solution.

---

## Angle 5: Alternative Web Terminals

### Native iOS Apps (Best Solution for Daily Use)

| App | Dictation Support | How It Works |
|-----|-------------------|--------------|
| **Moshi** | Native Parakeet/Whisper transcription | Purpose-built for AI coding agents (Claude Code). On-device voice with technical term recognition. Dictated text goes through native iOS input, not web textarea. $4.99/mo. |
| **La Terminal** | Native iOS dictation | Fully native terminal (not web-based). Supports iOS dictation and international keyboards natively. No xterm.js involved. |
| **Blink Shell** | Native iOS dictation | Native terminal with mosh support. Dictation works through standard iOS text input. |

**Moshi is purpose-built for exactly this use case** -- running Claude Code from an iPhone over mosh+tmux, with dictation that handles code/paths/technical terms. It bypasses the entire xterm.js problem by being a native app.

### Alternative Web Terminal Libraries

| Library | Mobile/Dictation Status |
|---------|------------------------|
| **DomTerm** | Uses DOM-based rendering (contenteditable). Can optionally use xterm.js. In DOM mode, standard text input works, but the project is not actively maintained for mobile. |
| **sshx (xterm.js fork)** | Fork by ekzhang for sshx. No mobile-specific fixes found. |
| **Hterm** (Chromium) | Chrome-only terminal. Better Android support but no special iOS handling. Keyboard doesn't show on iOS. |
| **Terminal.js** | Minimal web terminal. No composition handling at all -- same problems. |

### Web-Based Workaround: Wetty / gotty with Custom Frontend

Projects like **wetty** (Web + tty) and **gotty** use xterm.js under the hood, so they have the same bug. However, gotty is simpler and easier to patch since it serves a single HTML file.

---

## Recommended Solution (Ranked by Practicality)

### 1. Use Moshi (immediate, zero effort)

If the primary use case is running Claude Code from iPhone, Moshi solves this completely. Native app, native dictation, mosh+tmux, built for this exact workflow.

### 2. Visible Input Field Overlay (Approach B from Angle 3)

For ttyd specifically, serve a custom `index.html` via `--index` that adds a visible input bar at the bottom. Dictation goes into a standard `<input>` element (which iOS handles correctly), and completed text is forwarded to the terminal via `window.term.input()`. This completely bypasses the composition event pipeline.

Implementation:
1. Copy ttyd's default HTML to a file
2. Append the input bar script from Approach B above
3. Run: `ttyd --index /path/to/custom-index.html tmux attach`

### 3. Bookmarklet Dedup Patch (Approach A from Angle 3)

Quick-and-dirty: save the bookmarklet JavaScript as a Safari bookmark, tap it after loading ttyd. Works for occasional use but must be re-applied on each page load.

### 4. Fork xterm.js CompositionHelper

For a permanent fix, fork xterm.js and modify `CompositionHelper.ts`:
- Add `beforeinput` event listener to detect `insertFromDictation` input type
- When dictation is detected, suppress duplicate processing in `_handleAnyTextareaChanges()`
- Add a configurable dedup window (default 150ms) to suppress rapid identical inputs

This is the most work but would fix it upstream. The xterm.js maintainer has shown no interest in prioritizing mobile fixes, so a maintained fork may be necessary.

---

## Key References

- [WebKit Bug 261764: iOS dictation doesn't trigger composition events](https://bugs.webkit.org/show_bug.cgi?id=261764)
- [xterm.js #1101: Support mobile platforms](https://github.com/xtermjs/xterm.js/issues/1101) (open since 2017)
- [xterm.js #2403: Accommodate predictive keyboard on mobile](https://github.com/xtermjs/xterm.js/issues/2403)
- [xterm.js #3600: Erratic text on Chrome Android](https://github.com/xtermjs/xterm.js/issues/3600) (double-commit bug analysis by dolonet)
- [xterm.js #5377: Limited touch support](https://github.com/xtermjs/xterm.js/issues/5377)
- [xterm.js CompositionHelper.ts source](https://github.com/xtermjs/xterm.js/blob/master/src/browser/input/CompositionHelper.ts)
- [CodeMirror composition handling (reference implementation)](https://github.com/codemirror/view/blob/main/src/input.ts)
- [ttyd Client Options Wiki](https://github.com/tsl0922/ttyd/wiki/Client-Options)
- [ttyd --index custom HTML](https://github.com/tsl0922/ttyd/issues/194)
- [Moshi terminal app](https://getmoshi.app/) -- native iOS terminal with dictation for AI coding agents
- [La Terminal](https://la-terminal.net/) -- native iOS SSH/Mosh client with dictation support
- [Square: Understanding Composition Browser Events](https://developer.squareup.com/blog/understanding-composition-browser-events/)
