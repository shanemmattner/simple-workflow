# Mobile Workflow Research: Phone-to-Mac-Studio Agent Control

Research date: 2026-06-17
Context: Mac Studio on Tailscale (100.93.197.59:22), phone via Terminus SSH client.

---

## 1. What You Already Have

Before building anything new, here is the existing infrastructure worth leveraging:

### cc-queue system (`repos/cc-queue/`)
A fully built job queue with SQLite backend at `~/.cache/cc-queue/queue.db`. Includes:
- **`cc-queue submit`** -- submit jobs with priority, model, timeout, metadata
- **`cc-queue list`** -- see all jobs by status/priority
- **`cc-queue status <id>`** / **`cc-queue result <id>`** -- check job outcomes
- **`cc-queue logs <id> --follow`** -- tail live logs
- **`cc-queue dashboard`** -- CLI summary (running/pending/stalled/done counts + CPU/RAM)
- **`cc-queue metrics --last 8h`** -- CSV dump of system resource metrics (sampled every 30s)
- **`cc_dispatcher.py`** -- daemon that polls the queue, dispatches Claude Code sessions as subprocesses, monitors for stalls, auto-retries

### simple-workflow pipeline (`repos/simple-workflow/`)
- **`runs/runs.db`** -- 12 pipeline runs logged with per-phase cost, duration, tokens, review scores
- **`scripts/stats.sh`** -- 8 pre-built SQLite queries (cost-by-phase, avg-cost, run-details, etc.)
- **`scripts/run.sh`** -- one-liner to kick off issue-to-PR pipeline

### Existing launchd services
Already running on the Mac Studio:
- `com.shanemmattner.maestro-scheduler.plist` -- scheduled pipeline runs
- `com.shanemmattner.mlx-server.plist` / `mlx-server-2.plist` -- local LLM inference
- `com.shanemmattner.mlx-dispatcher.plist` -- MLX job dispatch
- `com.personal-assistant.claude-keepalive.plist` -- session keepalive
- `com.shanemmattner.proactive-monitor.plist` -- monitoring daemon
- `com.shanemmattner.morning-briefing.plist`

### Existing SQLite databases to surface
| Database | Location | Content |
|---|---|---|
| cc-queue | `~/.cache/cc-queue/queue.db` | Jobs, metrics, events |
| Pipeline runs | `repos/simple-workflow/runs/runs.db` | Runs, phases, reviews, costs |
| Per-run DBs | `repos/simple-workflow/engines/github_claude/runs/*.db` | Full replay per run |

---

## 2. The `cc` Quick-Launch Command

### Simplest approach: shell alias

Add to `~/.zshrc` on the Mac Studio:

```bash
# Quick-launch Claude Code in the right project
cc() {
  local project="${1:-simple-workflow}"
  local base="$HOME/Desktop/personal-assistant-clones/2/repos"
  local dir="$base/$project"
  if [ ! -d "$dir" ]; then
    echo "Project not found: $dir"
    echo "Available:" && ls "$base"
    return 1
  fi
  cd "$dir" && claude
}

# Quick-launch in tmux (survives SSH disconnect)
cct() {
  local project="${1:-simple-workflow}"
  local session="cc-${project}"
  local base="$HOME/Desktop/personal-assistant-clones/2/repos"
  if tmux has-session -t "$session" 2>/dev/null; then
    tmux attach -t "$session"
  else
    tmux new-session -d -s "$session" -c "$base/$project" "claude"
    tmux attach -t "$session"
  fi
}

# List active Claude Code tmux sessions
ccl() {
  tmux list-sessions 2>/dev/null | grep "^cc-" || echo "No active sessions"
}
```

**From the phone (Terminus):**
```
ssh mac-studio     # Tailscale hostname or 100.93.197.59
cct simple-workflow  # Opens Claude Code in tmux -- detach with Ctrl-B D
ccl                  # Check what's running
```

### Why tmux, not screen

tmux is the standard for this use case. It handles:
- Session persistence across SSH disconnects (critical for phone)
- Multiple panes (split view: code + logs)
- Session naming (`cc-simple-workflow`, `cc-assistant-bot`)
- Detach/reattach without losing state

### Claude Code Remote Control (alternative)

Claude Code now has a built-in Remote Control feature:
1. Run `claude` on the Mac Studio (inside tmux)
2. Type `/remote` in the Claude Code session
3. Get a URL/QR code
4. Open it on your phone's browser (not Terminus -- actual browser)

This gives a web-based interface to the running session. The limitation: it still needs `claude` running in a terminal, so tmux is still required for persistence.

---

## 3. Dispatching Workloads from Phone

### Option A: Use cc-queue directly (already built)

```bash
# From phone SSH:
cc-queue submit 'Fix the login bug' --cwd ~/Desktop/personal-assistant-clones/2/repos/assistant-bot --priority 3

# Or for issue-to-PR pipeline:
cc-queue submit --type worker --issue 42 --cwd ~/Desktop/personal-assistant-clones/2/repos/simple-workflow

# Check status:
cc-queue list
cc-queue dashboard
```

Add a convenience wrapper:

```bash
# sw = simple-workflow shortcut
sw() {
  local cmd="${1:-status}"
  local base="$HOME/Desktop/personal-assistant-clones/2/repos/simple-workflow"
  case "$cmd" in
    run)
      shift
      "$base/scripts/run.sh" "$@"
      ;;
    stats)
      shift
      "$base/scripts/stats.sh" "$@"
      ;;
    status)
      cc-queue list
      ;;
    dash)
      cc-queue dashboard
      ;;
    *)
      echo "Usage: sw {run|stats|status|dash} [args]"
      ;;
  esac
}
```

### Option B: Webhook/curl trigger

If you want fire-and-forget from phone without SSH, a minimal webhook server:

```python
#!/usr/bin/env python3
"""Tiny webhook server for dispatching agent jobs. Port 8484."""
import subprocess, json
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        task = body.get('task', '')
        cwd = body.get('cwd', '.')
        result = subprocess.run(
            ['python3', 'cc_queue.py', 'submit', task, '--cwd', cwd],
            capture_output=True, text=True,
            cwd='/path/to/cc-queue'
        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(result.stdout.encode())

HTTPServer(('0.0.0.0', 8484), Handler).serve_forever()
```

Then from phone: `curl -X POST http://100.93.197.59:8484 -d '{"task":"Fix bug","cwd":"/path"}'`

### Option C: Telegram bot

For a richer mobile experience, a Telegram bot that wraps cc-queue. The OpenCode Telegram Bot project (github.com/grinev/opencode-telegram-bot) demonstrates this pattern. But this adds complexity -- only worth it if SSH feels too cumbersome.

### Option D: Claude Code Dispatch (Anthropic's built-in)

Anthropic's Dispatch feature lets you create new Claude Code tasks from the Claude mobile app or claude.ai. It is purpose-built for this use case but requires the desktop Claude Code session to be running. Combined with tmux persistence on the Mac Studio, this could work well for ad-hoc tasks. However, it does not integrate with your custom pipeline (cc-queue / simple-workflow), so it is best for freeform coding tasks rather than structured pipeline runs.

### Recommendation

**Use cc-queue from SSH (Option A).** It is already built and working. Add the `sw` wrapper for ergonomics. Skip Telegram bots and webhooks unless SSH becomes a bottleneck -- it will not with Terminus + Tailscale.

---

## 4. Monitoring Dashboards

### Tier 1: CLI dashboard (already exists)

```bash
cc-queue dashboard    # Queue status + capacity
sw stats cost-by-phase  # Pipeline costs
sw stats total-runs     # Run status counts
```

This is what you should use 90% of the time from the phone. Fast, works over SSH, no setup.

### Tier 2: Datasette (10 minutes to set up)

[Datasette](https://datasette.io/) is the single best tool for browsing SQLite databases via a web UI. Zero config, instant results.

```bash
pip install datasette datasette-dashboards

# Serve all your databases at once:
datasette serve \
  ~/.cache/cc-queue/queue.db \
  ~/Desktop/personal-assistant-clones/2/repos/simple-workflow/runs/runs.db \
  --host 0.0.0.0 \
  --port 8001 \
  --setting sql_time_limit_ms 5000
```

What you get:
- Browse all tables with filtering, sorting, faceting
- Run arbitrary SQL queries from the browser
- JSON API for every query (useful for automation)
- Mobile-friendly responsive UI
- Read-only by default (safe)

**datasette-dashboards plugin** lets you define custom dashboards in YAML:

```yaml
# metadata.yml
plugins:
  datasette-dashboards:
    agent-runs:
      title: Agent Pipeline Runs
      charts:
        - title: Runs by Status
          db: runs
          query: SELECT status, count(*) as count FROM pipeline_runs GROUP BY status
          library: vega-lite
          display:
            mark: bar
        - title: Cost by Phase (Last 10 Runs)
          db: runs
          query: >
            SELECT phase, SUM(cost_usd) as total_cost
            FROM phase_logs
            WHERE run_id IN (SELECT run_id FROM pipeline_runs ORDER BY started_at DESC LIMIT 10)
            GROUP BY phase
          library: vega-lite
          display:
            mark: bar
```

### Tier 3: sqlite-web (alternative to Datasette)

```bash
pip install sqlite-web
sqlite_web ~/.cache/cc-queue/queue.db --host 0.0.0.0 --port 8080
```

Simpler than Datasette. Single-DB browser with table editing. Less polished but works.

### Tier 4: Single-file HTML dashboard

For a bookmarkable status page, a single HTML file served by Python's built-in server. It reads the SQLite databases and renders status, costs, and metrics as a mobile-friendly page.

```python
#!/usr/bin/env python3
"""Single-file agent dashboard. Run: python3 dashboard.py (port 8001)"""
import json, sqlite3, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

QUEUE_DB = Path.home() / '.cache/cc-queue/queue.db'
RUNS_DB = Path.home() / 'Desktop/personal-assistant-clones/2/repos/simple-workflow/runs/runs.db'

def query(db_path, sql, params=()):
    if not db_path.exists(): return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows

HTML = '''<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Dashboard</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; margin: 1em; background: #0d1117; color: #c9d1d9; }
  h1, h2 { color: #58a6ff; }
  table { width: 100%%; border-collapse: collapse; margin: 0.5em 0 1.5em 0; }
  th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid #21262d; font-size: 14px; }
  th { color: #8b949e; }
  .ok { color: #3fb950; } .error { color: #f85149; } .running { color: #d29922; }
  .metric { display: inline-block; background: #161b22; padding: 12px 20px; margin: 4px; border-radius: 8px; }
  .metric .value { font-size: 24px; font-weight: bold; }
  .metric .label { font-size: 12px; color: #8b949e; }
  @media (max-width: 600px) { .metric { display: block; margin: 4px 0; } }
</style>
</head><body>
<h1>Agent Dashboard</h1>
<div id="metrics">%METRICS%</div>
<h2>Queue Jobs</h2>
<table>%QUEUE%</table>
<h2>Pipeline Runs</h2>
<table>%RUNS%</table>
<h2>Cost by Phase (Last 10 Runs)</h2>
<table>%COSTS%</table>
<script>setTimeout(()=>location.reload(), 30000)</script>
</body></html>'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/status':
            data = {
                'queue': query(QUEUE_DB, "SELECT status, count(*) as n FROM jobs GROUP BY status"),
                'runs': query(RUNS_DB, "SELECT status, count(*) as n FROM pipeline_runs GROUP BY status"),
            }
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
            return

        # Build HTML
        jobs = query(QUEUE_DB,
            "SELECT id, job_type, status, priority, last_phase, submitted_at FROM jobs ORDER BY "
            "CASE status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, submitted_at DESC LIMIT 20")
        runs = query(RUNS_DB,
            "SELECT run_id, issue, status, model, spent_usd, started_at FROM pipeline_runs ORDER BY started_at DESC LIMIT 15")
        costs = query(RUNS_DB,
            "SELECT phase, SUM(cost_usd) as total, AVG(cost_usd) as avg FROM phase_logs "
            "WHERE run_id IN (SELECT run_id FROM pipeline_runs ORDER BY started_at DESC LIMIT 10) GROUP BY phase")
        q_counts = query(QUEUE_DB, "SELECT status, count(*) as n FROM jobs GROUP BY status")
        r_counts = query(RUNS_DB, "SELECT status, count(*) as n FROM pipeline_runs GROUP BY status")

        metrics_html = ''
        for row in q_counts:
            metrics_html += f'<div class="metric"><div class="value {row["status"]}">{row["n"]}</div><div class="label">Queue: {row["status"]}</div></div>'
        for row in r_counts:
            metrics_html += f'<div class="metric"><div class="value {row["status"]}">{row["n"]}</div><div class="label">Runs: {row["status"]}</div></div>'

        def table(rows, cols):
            if not rows: return '<tr><td>No data</td></tr>'
            h = '<tr>' + ''.join(f'<th>{c}</th>' for c in cols) + '</tr>'
            body = ''
            for r in rows:
                cells = ''.join(f'<td class="{r.get("status","")}">{r.get(c,"")}</td>' for c in cols)
                body += f'<tr>{cells}</tr>'
            return h + body

        page = HTML.replace('%METRICS%', metrics_html)
        page = page.replace('%QUEUE%', table(jobs, ['id','job_type','status','priority','last_phase','submitted_at']))
        page = page.replace('%RUNS%', table(runs, ['run_id','issue','status','model','spent_usd','started_at']))
        page = page.replace('%COSTS%', table(costs, ['phase','total','avg']))

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(page.encode())

    def log_message(self, *a): pass

print("Dashboard at http://0.0.0.0:8001")
HTTPServer(('0.0.0.0', 8001), Handler).serve_forever()
```

### Tier 5: Mission Control (if you outgrow the above)

[builderz-labs/mission-control](https://github.com/builderz-labs/mission-control) is a self-hosted agent orchestration dashboard. SQLite-backed, single `pnpm start`, no external dependencies. Features: task dispatch, agent fleet management, cost tracking, webhooks, cron, alerts. Overkill for now, but worth knowing about if the system grows.

### Tier 6: Observability platforms (for later)

If you ever need tracing/eval:
- [Langfuse](https://github.com/langfuse/langfuse) -- open source LLM observability, self-hosted
- [Opik](https://github.com/comet-ml/opik) -- tracing + eval dashboards
- [Helicone](https://www.helicone.ai/) -- LLM proxy with cost/latency dashboards, self-hostable via Docker

These are heavy. Not recommended until you have 50+ runs/day and need tracing.

---

## 5. Port Conventions and Network Access

### Recommended port assignments

| Service | Port | Purpose |
|---|---|---|
| Datasette | 8001 | SQLite browser / main dashboard |
| Custom dashboard | 8002 | Single-file HTML dashboard (if not using Datasette) |
| sqlite-web | 8080 | DB editor (if needed) |
| Webhook dispatch | 8484 | Job submission endpoint |
| MLX inference | 8081, 8082 | Already running (mlx-server, mlx-server-2) |

### Tailscale access (already configured)

Since the Mac Studio is on Tailscale (100.93.197.59), any port you bind to `0.0.0.0` is accessible from your phone when connected to Tailscale. No firewall changes needed. From your phone browser:

```
http://100.93.197.59:8001    # Datasette or dashboard
```

Bookmark this in your phone browser for one-tap access.

### Tailscale Serve (optional, nicer URLs)

```bash
# Makes the dashboard available at https://mac-studio.tailnet-name.ts.net
tailscale serve --bg 8001
```

This gives you HTTPS with a valid cert and a memorable hostname. No Funnel needed -- Serve keeps it private to your tailnet.

**Tailscale Funnel** (public internet access) is only needed if you want to share the dashboard outside your tailnet. Funnel only supports ports 443, 8443, and 10000. For personal use, Serve is better.

---

## 6. Concrete Recommendations (Priority Order)

### Do now (5 minutes)

1. **Add shell aliases to `~/.zshrc`** on the Mac Studio:
   ```bash
   # cc = Claude Code in project
   cc() { cd ~/Desktop/personal-assistant-clones/2/repos/${1:-simple-workflow} && claude; }
   
   # cct = Claude Code in tmux (persistent)
   cct() {
     local p="${1:-simple-workflow}" s="cc-$p"
     tmux has-session -t "$s" 2>/dev/null && tmux attach -t "$s" || \
       tmux new-session -s "$s" -c "$HOME/Desktop/personal-assistant-clones/2/repos/$p" "claude"
   }
   
   # ccl = list active sessions
   ccl() { tmux list-sessions 2>/dev/null | grep "^cc-" || echo "None"; }
   ```

2. **Add cc-queue to PATH** (if not already):
   ```bash
   export PATH="$HOME/Desktop/personal-assistant-clones/2/repos/cc-queue:$PATH"
   alias q='cc-queue'
   alias ql='cc-queue list'
   alias qd='cc-queue dashboard'
   ```

### Do soon (10 minutes)

3. **Install and launch Datasette**:
   ```bash
   pip install datasette datasette-dashboards
   
   # One-liner to serve both databases:
   datasette serve \
     ~/.cache/cc-queue/queue.db:queue \
     ~/Desktop/personal-assistant-clones/2/repos/simple-workflow/runs/runs.db:runs \
     --host 0.0.0.0 --port 8001
   ```

4. **Create a launchd plist** to keep Datasette running:
   ```xml
   <!-- ~/Library/LaunchAgents/com.shanemmattner.datasette-dashboard.plist -->
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
   <dict>
     <key>Label</key>
     <string>com.shanemmattner.datasette-dashboard</string>
     <key>ProgramArguments</key>
     <array>
       <string>/opt/homebrew/bin/datasette</string>
       <string>serve</string>
       <string>--host</string><string>0.0.0.0</string>
       <string>--port</string><string>8001</string>
       <string>--immutable</string>
       <!-- Add your DB paths here -->
     </array>
     <key>RunAtLoad</key><true/>
     <key>KeepAlive</key><true/>
     <key>StandardOutPath</key>
     <string>/tmp/datasette.log</string>
     <key>StandardErrorPath</key>
     <string>/tmp/datasette.err</string>
   </dict>
   </plist>
   ```

### Do if needed (30 minutes)

5. **Custom HTML dashboard** (the single-file Python script from Tier 4 above) -- only if Datasette's UI feels too generic and you want a purpose-built status page.

6. **Webhook endpoint** -- only if you find yourself wanting to dispatch jobs without opening Terminus.

### Skip for now

- Telegram bots (adds dependency, auth complexity)
- Mission Control (overkill for current scale)
- Langfuse/Opik (need tracing integration first)
- Tailscale Funnel (Serve is sufficient for personal use)

---

## 7. The Complete Phone Workflow

Once set up, here is the day-to-day flow:

**Quick interactive coding session:**
```
[Phone] Open Terminus
[Phone] ssh mac-studio
[Phone] cct assistant-bot          # Claude Code in tmux
[Phone] ... work ...
[Phone] Ctrl-B D                   # Detach (keeps running)
[Phone] Close Terminus              # Safe, session persists
[Phone] Later: ssh mac-studio && tmux attach -t cc-assistant-bot
```

**Dispatch a pipeline run:**
```
[Phone] ssh mac-studio
[Phone] cc-queue submit --type worker --issue 15 --cwd ~/Desktop/personal-assistant-clones/2/repos/assistant-bot
[Phone] ql                          # Check queue
```

**Monitor from browser:**
```
[Phone] Open Safari
[Phone] http://100.93.197.59:8001   # Datasette dashboard (bookmarked)
[Phone] Browse tables, run SQL, check costs
```

**Quick status check:**
```
[Phone] ssh mac-studio
[Phone] qd                          # cc-queue dashboard (one command)
```

---

## Sources

- [Datasette](https://datasette.io/) -- SQLite web browser
- [datasette-dashboards plugin](https://datasette.io/plugins/datasette-dashboards)
- [sqlite-web](https://github.com/coleifer/sqlite-web) -- simple SQLite web browser
- [Mission Control](https://github.com/builderz-labs/mission-control) -- self-hosted agent orchestration dashboard
- [Langfuse](https://github.com/langfuse/langfuse) -- open source LLM observability
- [Opik](https://github.com/comet-ml/opik) -- LLM tracing and eval
- [Claude Code Remote Control guide](https://www.datacamp.com/tutorial/claude-code-remote-control)
- [Claude Code on VPS with tmux](https://medium.com/@0xmega/claude-code-on-a-vps-the-complete-setup-security-tmux-mobile-access-2d214f5a0b3b)
- [Claude Code headless mode](https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-headless)
- [Claude Code Dispatch](https://www.mindstudio.ai/blog/what-is-claude-code-dispatch)
- [Tailscale Serve docs](https://tailscale.com/kb/1242/tailscale-serve)
- [Tailscale Funnel docs](https://tailscale.com/docs/features/tailscale-funnel)
- [OpenCode Telegram Bot](https://agentskill.work/en/skills/grinev/opencode-telegram-bot)
