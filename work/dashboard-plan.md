# Dashboard Plan: simple-workflow Observability

Date: 2026-06-17

## Source Material

IndyDevDan's repos (GitHub user: `disler`, cloned to `~/services/pa/repos/`):
- `claude-code-hooks-multi-agent-observability` -- Vue 3 + Bun server + SQLite event store
- `claude-code-hooks-mastery` -- hook examples, status lines, output styles (reference only)

Our pipeline: `engines/github_claude/` with per-run `.db` files in `engines/github_claude/runs/`.

---

## 1. Architecture Comparison

### Their System

```
Claude Code hooks (PreToolUse, PostToolUse, etc.)
    |
    v  HTTP POST to localhost:4000/events
send_event.py  -------->  Bun server (apps/server/src/index.ts)
                              |
                              +-- SQLite: single events.db (flat event log)
                              +-- WebSocket broadcast to /stream
                              |
                          Vue 3 client (apps/client/, Vite + Tailwind)
                              |
                              +-- LivePulseChart (canvas-based activity chart)
                              +-- EventTimeline (scrolling event rows)
                              +-- AgentSwimLanes (per-agent activity comparison)
                              +-- FilterPanel (source_app, session_id, event_type)
                              +-- ThemeManager (full theming system -- overkill)
                              +-- HITL (human-in-the-loop question/permission UI)
```

**Their SQLite schema** (single table + theme tables):
```sql
events (id, source_app, session_id, hook_event_type, payload JSON,
        chat JSON, summary, timestamp INTEGER, humanInTheLoop JSON,
        humanInTheLoopStatus JSON, model_name)
```

Everything is a flat event with a JSON `payload` blob. No relational structure. No concept of "runs" or "phases."

### Our System

```
orchestrator.py
    |
    v  Per-run SQLite .db file
storage.py  -------->  engines/github_claude/runs/<repo>-<issue>-<timestamp>.db
                          |
                          +-- run (id, repo, issue_number, status, model, cost, tokens, verdict)
                          +-- phase (run_id, phase_name, status, model, cost, tokens, failure_category)
                          +-- message (phase_id, turn_number, role, content, tokens, cost)
                          +-- tool_call (message_id, phase_id, tool_name, input, result, duration_ms)
                          +-- event (run_id, phase_id, event_type, details JSON, timestamp)
```

Relational, per-run. Multiple .db files (one per pipeline invocation). Phases are: triage, plan-task-N, test-plan-task-N, wave-planner, execute-task-N, review.

---

## 2. What We Can Reuse vs. What We Build

### Reuse (with modification)

| Component | Their Path | What It Does | Adaptation |
|-----------|-----------|--------------|------------|
| WebSocket composable | `apps/client/src/composables/useWebSocket.ts` | Auto-reconnecting WS client, event buffering | Reuse directly -- change message types to match our schema |
| LivePulseChart | `apps/client/src/components/LivePulseChart.vue` | Canvas-based real-time activity bar chart | Repurpose: x-axis = phases, bars = events/tool-calls per phase |
| EventTimeline | `apps/client/src/components/EventTimeline.vue` | Scrolling event list with auto-scroll | Adapt: show phases as collapsible groups instead of flat events |
| AgentSwimLanes | `apps/client/src/components/AgentSwimLane*.vue` | Per-agent horizontal activity lanes | Map to per-phase swim lanes showing parallel execute-task-N |
| FilterPanel | `apps/client/src/components/FilterPanel.vue` | Dropdown filters | Adapt: filter by run, phase, status instead of source_app/session_id |
| useEventColors | `apps/client/src/composables/useEventColors.ts` | Deterministic color assignment per agent name | Reuse for phase coloring |
| Bun server pattern | `apps/server/src/index.ts` | HTTP + WS in one process, SQLite reads | Rewrite the data layer entirely |

### Drop Entirely

| Component | Why |
|-----------|-----|
| ThemeManager, ThemePreview, useThemes, theme.ts, themes DB tables | 400+ lines of theme marketplace code. Zero value for us. Use a single dark theme. |
| HITL system (humanInTheLoop, responseWebSocketUrl) | We don't need human-in-the-loop from the dashboard. Pipeline gates handle this. |
| ChatTranscript, ChatTranscriptModal | Their transcript is a flat JSONL dump. Our messages table is structured. Build our own. |
| send_event.py + hook wiring | Their data ingestion path. We already have storage.py writing to SQLite. |
| ToastNotification | Nice-to-have polish, not needed for v1. |

### Build New

| Component | Purpose |
|-----------|---------|
| **Run poller** (server) | Scans `runs/` directory for .db files, reads run/phase status, serves aggregated state |
| **Phase timeline** (client) | Gantt-style view: phases on y-axis, time on x-axis, color = status |
| **Run list** (client) | Table of all runs with repo, issue, status, cost, duration, verdict |
| **Phase detail** (client) | Expand a phase to see messages, tool calls, cost breakdown |
| **Compatibility adapter** (server) | HTTP endpoint that accepts events from non-Claude runtimes (Codex, OpenRouter) |

---

## 3. Architecture Recommendation

**Keep their stack: Vue 3 + Bun + SQLite. Do not switch.**

Rationale:
- Bun's built-in SQLite (`bun:sqlite`) and WebSocket server are the entire backend in ~100 lines of actual logic (their index.ts minus theme crud). No framework, no ORM, no deps beyond what Bun ships.
- Vue 3 Composition API with `<script setup>` is clean and the existing composables are well-structured.
- The client is Vite-based and builds to static files. Serve from Bun or any static host.
- Mac Studio has Bun installed. No new runtime needed.
- Total dependency footprint: Vue 3, Tailwind, Vite (dev only). Minimal.

**Change: ditch the monorepo structure.** Their `apps/server/` and `apps/client/` are separate npm packages with separate `bun.lock` files. For us, flatten to:

```
dashboard/
  server/
    index.ts          -- Bun HTTP+WS server
    db-reader.ts      -- reads our per-run .db files (NOT their events.db)
    run-poller.ts     -- watches runs/ dir, polls active .db files
    compat.ts         -- compatibility layer for non-Claude runtimes
  client/
    index.html
    src/
      App.vue
      components/
        RunList.vue
        PhaseTimeline.vue
        PhaseDetail.vue
        LivePulseChart.vue   (adapted from theirs)
        EventTimeline.vue    (adapted from theirs)
        FilterPanel.vue      (adapted from theirs)
      composables/
        useWebSocket.ts      (from theirs, unchanged)
        useEventColors.ts    (from theirs, unchanged)
        useRunData.ts        (new -- fetches/subscribes to run state)
      types.ts
    tailwind.config.js
    vite.config.ts
  package.json         -- single package.json
  start.sh
```

---

## 4. Server Design: Reading Our SQLite Files

Their server writes to a single `events.db`. Ours reads from multiple per-run `.db` files. Fundamentally different data access pattern.

### `db-reader.ts` -- Core Data Access

```typescript
import { Database } from 'bun:sqlite';

interface RunSummary {
  id: string;
  db_path: string;
  repo: string;
  issue_number: number;
  status: string;  // 'running' | 'passed' | 'failed' | ...
  model: string;
  started_at: string;
  finished_at: string | null;
  total_cost: number;
  total_tokens_in: number;
  total_tokens_out: number;
  review_verdict: string | null;
  phases: PhaseSummary[];
}

interface PhaseSummary {
  id: number;
  phase_name: string;
  status: string;
  model: string | null;
  started_at: string;
  finished_at: string | null;
  cost: number;
  tokens_in: number;
  tokens_out: number;
  failure_category: string | null;
}

function readRunDb(dbPath: string): RunSummary {
  const db = new Database(dbPath, { readonly: true });
  const run = db.prepare('SELECT * FROM run LIMIT 1').get() as any;
  const phases = db.prepare(
    'SELECT * FROM phase ORDER BY started_at'
  ).all() as PhaseSummary[];
  db.close();
  return { ...run, db_path: dbPath, phases };
}
```

### `run-poller.ts` -- Live Status via Directory Watching

```typescript
import { watch } from 'fs';
import { glob } from 'glob';

const RUNS_DIR = '../engines/github_claude/runs';

// Poll active runs (status='running') every 2 seconds
// Full directory scan every 30 seconds for new .db files
// On change, broadcast updated state via WebSocket
```

Key insight: their system gets events pushed to it (hooks POST to server). Ours must **pull** -- poll the .db files. For active runs, the .db has WAL mode enabled, so we can read while the orchestrator writes.

### API Endpoints

```
GET  /api/runs                    -- list all runs (scans runs/ directory)
GET  /api/runs/:id                -- full run detail (phases, events)
GET  /api/runs/:id/phases/:pid    -- phase messages + tool calls
GET  /api/runs/:id/phases/:pid/messages -- paginated messages
WS   /stream                      -- real-time updates for active runs
POST /api/events                  -- compatibility endpoint (non-Claude runtimes)
```

WebSocket message types:
```typescript
type WsMessage =
  | { type: 'runs_update'; data: RunSummary[] }        // periodic full state
  | { type: 'phase_update'; data: { run_id: string; phase: PhaseSummary } }
  | { type: 'event'; data: { run_id: string; event: any } }  // compat events
```

---

## 5. Live Workflow Status (Not Just Historical Data)

The critical difference between "browse SQLite data" and "see status of running workflows."

### Detection of Active Runs

1. On startup, scan `runs/` for all `.db` files
2. Open each, read `run.status` -- if `'running'`, mark as active
3. For active runs, keep the Database handle open (readonly) and poll every 2s:
   - `SELECT * FROM phase ORDER BY started_at` -- detect new phases
   - `SELECT * FROM event WHERE id > ?` -- incremental event fetch
   - `SELECT status FROM run` -- detect completion
4. Use `fs.watch()` on `runs/` directory to detect new `.db` files appearing

### Client-Side Status Indicators

Adapt from their connection status pattern (`isConnected` reactive ref):

- **Run status badges**: green pulse (running), checkmark (passed), red X (failed)
- **Phase progress bar**: horizontal bar showing completed/active/pending phases
- **Active phase highlight**: the currently-executing phase gets a pulsing indicator
- **Cost ticker**: live-updating cost from the active run's phase rows
- **Duration timer**: wall-clock elapsed since `run.started_at`

### Phase Timeline (New Component)

```
triage     [====]
plan-1     [====]
plan-2     [====]
test-plan  [====]
wave-plan  [====]
exec-1     [========>        ]  <-- currently running
exec-2     [    ]  (pending)
review     [    ]  (pending)
```

This replaces their LivePulseChart as the primary visualization. Their pulse chart shows events-per-second; ours shows phase progression.

---

## 6. Phone Over Tailscale (Mac Studio at 100.93.197.59)

### Serving

Bun server binds to `0.0.0.0:4080` (pick a port that won't conflict with existing services). Vite builds static client to `dist/`, Bun serves it:

```typescript
// In production, serve static files from dist/
if (url.pathname === '/' || !url.pathname.startsWith('/api')) {
  const filePath = `./client/dist${url.pathname === '/' ? '/index.html' : url.pathname}`;
  const file = Bun.file(filePath);
  if (await file.exists()) {
    return new Response(file);
  }
  // SPA fallback
  return new Response(Bun.file('./client/dist/index.html'));
}
```

Single port serves both API and static files. WebSocket upgrades on `/stream` at the same port.

### Mobile-Specific Adaptations

Their client already has mobile breakpoints (`mobile:` prefix throughout Tailwind classes). Reuse this pattern:

```css
/* In tailwind.config.js (from their repo) */
screens: {
  'mobile': {'max': '640px'},
  'short': {'raw': '(max-height: 500px)'},
}
```

For phone access:
- URL: `http://100.93.197.59:4080`
- No auth needed (Tailscale is the auth layer)
- Touch-friendly: their buttons already have `mobile:p-1` sizing
- Add: pull-to-refresh for run list
- Add: swipe to expand phase detail

### launchd Service

```xml
<!-- ~/Library/LaunchAgents/com.pa.workflow-dashboard.plist -->
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.pa.workflow-dashboard</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/shane/.bun/bin/bun</string>
    <string>run</string>
    <string>start</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/shane/services/pa/repos/simple-workflow/dashboard/server</string>
  <key>KeepAlive</key>
  <true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>RUNS_DIR</key>
    <string>/Users/shane/services/pa/repos/simple-workflow/engines/github_claude/runs</string>
    <key>PORT</key>
    <string>4080</string>
  </dict>
</dict>
</plist>
```

---

## 7. Compatibility Layer for Non-Claude Code Runtimes

Our pipeline uses `runtime.py` which calls `claude -p` (Claude CLI). But the dashboard should also work when:
- Codex (OpenAI) is the runtime
- OpenRouter API calls are the runtime
- Local models via LiteLLM are the runtime

### Approach: Storage-Layer Abstraction

The dashboard reads `.db` files. The compatibility question is: **who writes the .db files?**

Currently, `storage.py` is called by `orchestrator.py`. The orchestrator doesn't care about the model provider -- it calls `runtime.call_agent()` which returns a standardized dict:

```python
# runtime.py already returns a provider-agnostic dict:
{
  "content": str,
  "tokens_in": int,
  "tokens_out": int,
  "cost": float,
  "duration_s": float
}
```

So the compatibility layer is in `runtime.py`, NOT in the dashboard. The dashboard just reads the standard schema.

### `compat.ts` -- External Event Ingestion

For agents that don't use our orchestrator (e.g., standalone Codex agents), provide an HTTP endpoint that accepts events and writes them to a .db:

```typescript
// POST /api/events
// Body: { source: "codex", run_id?: string, phase?: string, event_type: string, details: any }

// This creates/appends to a .db file in runs/ using the same schema.
// The dashboard's run poller picks it up automatically.
```

### Runtime Adapter Pattern (for orchestrator.py)

```python
# engines/github_claude/runtime.py -- add provider dispatch

def call_agent(prompt, *, model, cwd, max_turns, ...) -> dict:
    provider = _resolve_provider(model)
    if provider == "claude-cli":
        return _call_claude_cli(prompt, model=model, cwd=cwd, ...)
    elif provider == "openrouter":
        return _call_openrouter(prompt, model=model, ...)
    elif provider == "codex":
        return _call_codex(prompt, model=model, cwd=cwd, ...)
    elif provider == "litellm":
        return _call_litellm(prompt, model=model, ...)
```

The `.db` schema stays identical regardless of provider. The `model` column in `phase` records which model ran. The dashboard renders it all the same way.

---

## 8. Implementation Phases

### Phase 1: Read-Only Dashboard (3-4 hours)

1. Create `dashboard/` directory structure
2. Port server: Bun + SQLite reader for our schema (no writes)
3. Port client: RunList + PhaseTimeline + basic detail view
4. Copy useWebSocket, useEventColors composables verbatim
5. Wire run polling with WebSocket broadcast
6. Test: start a pipeline run, see it appear live in browser

### Phase 2: Mobile + Tailscale (1-2 hours)

1. Build client to static files
2. Add static file serving to Bun server
3. Set up launchd plist on Mac Studio
4. Test from phone over Tailscale
5. Tune mobile breakpoints (their patterns are a good start)

### Phase 3: Rich Detail Views (2-3 hours)

1. Phase detail: messages and tool calls in collapsible tree
2. Cost breakdown charts (per-phase, per-model)
3. Token usage visualization
4. Gate results display (from event table)
5. Review verdict display

### Phase 4: Compatibility Layer (2-3 hours)

1. Add POST /api/events endpoint to dashboard server
2. Add runtime adapters to orchestrator's runtime.py
3. Test with OpenRouter (DeepSeek V4 via our existing LiteLLM proxy)
4. Record model provider in phase metadata

---

## 9. Key Code Patterns From Their Repos

### Pattern: Bun Server with WebSocket (apps/server/src/index.ts:104-447)

Their server is a single `Bun.serve()` call with both `fetch` (HTTP) and `websocket` handlers. No Express, no framework. This pattern works well:

```typescript
const server = Bun.serve({
  port: parseInt(process.env.PORT || '4080'),
  async fetch(req) {
    // Route matching via url.pathname string checks
    // CORS headers on every response
    // WebSocket upgrade on /stream
  },
  websocket: {
    open(ws) { clients.add(ws); /* send initial state */ },
    close(ws) { clients.delete(ws); },
  }
});
```

### Pattern: Vue Composable for WebSocket (apps/client/src/composables/useWebSocket.ts)

Auto-reconnect with 3s backoff. Event buffer with sliding window. Clean setup/teardown in `onMounted`/`onUnmounted`. Copy this directly.

### Pattern: Canvas Chart Rendering (apps/client/src/utils/chartRenderer.ts)

They render the pulse chart with raw Canvas 2D API -- no chart library dependency. We can adapt this for our phase timeline if we want the Gantt chart to be performant on mobile.

### Pattern: Color Assignment (apps/client/src/composables/useEventColors.ts)

Deterministic hash-based color assignment per string key. Use for phase names so colors are consistent across sessions.

---

## 10. Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| WAL-mode read contention between dashboard and orchestrator | Bun's `bun:sqlite` with `{ readonly: true }` handles WAL reads safely. Tested in their repo. |
| Stale .db files accumulating (each run = new file) | Add a cleanup endpoint/cron that archives runs older than 30 days. Show a count in the dashboard. |
| Bun version differences between MacBook and Mac Studio | Pin Bun version in `package.json` engines field. Both machines have Bun. |
| Vue/Vite build complexity | Their client has zero custom Vite config beyond the Vue plugin. Keep it minimal. |
| Too many .db files to scan on startup | Index the `runs/` directory lazily. Load run metadata only, defer phase/message reads to on-demand. |
