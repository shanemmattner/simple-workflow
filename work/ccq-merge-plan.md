# cc-queue Merge Plan: Absorb into simple-workflow

**Date: 2026-06-17**

The previous analysis (ccq-vs-simple-workflow.md) recommended keeping them separate. That recommendation assumed cc-queue would continue dispatching to multiple executors (maestro pipe.py, pr_review, mc.py). In practice, simple-workflow is replacing maestro as the sole pipeline, and maintaining cc-queue as a separate repo adds coordination cost for no benefit. One repo, one CLI, one daemon.

---

## 1. What to Bring Over

### Bring: job queue + SQLite schema (cc_shared.py)

The SQLite job queue with priority, status tracking, and metadata is the core value. The schema (jobs table + metrics table) is clean and works. Bring it as-is, adjusting paths from `~/.cache/cc-queue/` to `~/.cache/simple-workflow/`.

### Bring: dispatcher loop (cc_dispatcher.py)

The main poll-dispatch-monitor loop is battle-tested. Key pieces to keep:

- **Priority-ordered job pickup** -- `SELECT ... WHERE status='pending' ORDER BY priority ASC, submitted_at ASC`
- **Threaded job monitoring** -- one thread per running job, watching stdout for stalls
- **Stall detection** -- 120s post-token silence timeout, 300s startup grace, stall counter with auto-retry (up to 3)
- **Fast-fail backoff** -- 3 consecutive fast failures trigger 60s cooldown
- **Orphan process cleanup** -- kills reparented claude/node processes (ppid=1, age >5min)
- **Stale job recovery** -- on startup, re-queues anything stuck in 'running'
- **Metrics sampling** -- 30s interval, RAM/CPU/swap/worktree stats, 7-day pruning

### Bring: capacity checks (cc_shared.py)

- `is_at_capacity()` -- checks MAX_WORKERS, CPU load (70% threshold), and RAM floor (16GB)
- `_available_ram_gb()` -- macOS vm_stat parsing
- `_cpu_load()` -- os.getloadavg
- `_swap_used_mb()` -- sysctl parsing

### Bring: CLI commands (cc_queue.py)

All subcommands are useful:

- `submit` -- enqueue a job with priority, model, timeout, metadata
- `status` -- show job status, phase, stall count, PID
- `result` -- block until done or poll with --no-wait
- `logs` -- show/tail/path for job log files
- `list` -- table view of all jobs, sorted by status/priority
- `cancel` -- delete pending jobs
- `dashboard` -- summary counts + capacity display
- `metrics` -- dump CSV of system resource history

### Bring: keepalive script (keepalive.sh)

The keepalive pattern (PID file check, nohup restart) is needed for daemon reliability. Adapt paths.

### Bring: event logging

The `_write_event()` pattern (JSON files in events/ dir) provides a lightweight event bus for dispatch/stall/completion events. Useful for external monitoring.

---

## 2. What to Leave Behind

### Leave: CCS account rotation (cc_shared.py: select_ccs_account, _load_credentials, _refresh_token)

This is ~100 lines managing `~/.ccs/resets.json`, OAuth token expiry, and account selection by billing cycle proximity. It solves a real problem, but:

- It couples the dispatcher to a specific credential management scheme (CCS instances with per-account config dirs)
- simple-workflow's runtime.py just uses whatever `claude` is on PATH with no account concept
- If we need account rotation later, it should be a pluggable hook, not hardwired into the dispatcher

**Migration path:** For now, the dispatcher sets `CLAUDE_CONFIG_DIR` from environment or leaves it unset (uses default claude config). Add account rotation back as an optional plugin if/when needed.

### Leave: maestro-specific job types and paths

- `_find_mc_py()` -- auto-detects maestro's mc.py location across clone directories
- `MC_PY`, `MAESTRO_DIR` -- hardcoded maestro paths
- `MAESTRO_LOG_DIR`, `MAESTRO_WORKTREE_DIR` -- maestro-specific cache directories
- The `worker`, `pr-review`, `mc` branches in `_build_command()` -- these build maestro-specific command lines
- `_cleanup_worktree()` -- maestro-specific worktree cleanup logic (looks for issue-N/pr-N directories in maestro's worktree dir)

All of this is maestro plumbing. simple-workflow has its own worktree management (workspace.py) and its own run storage.

### Leave: maestro metadata flags

The `submit` command's `--execute-model`, `--reviewer-model`, `--fixer-model`, `--max-rounds`, `--gh-repo` flags are maestro pipe.py/pr_review.py specific. Replace with simple-workflow's own flags (`--budget`, `--model`, `--repo-path`).

---

## 3. Where It Goes in simple-workflow

### File layout

```
simple-workflow/
  queue/                    <-- NEW: queue + dispatcher (top-level, engine-agnostic)
    __init__.py
    __main__.py             <-- CLI entry: `python -m queue submit ...`
    cli.py                  <-- argparse CLI (adapted from cc_queue.py)
    dispatcher.py           <-- daemon loop (adapted from cc_dispatcher.py)
    shared.py               <-- DB schema, capacity checks (adapted from cc_shared.py)
    keepalive.sh            <-- daemon keepalive (adapted from keepalive.sh)

  engines/
    github_claude/          <-- existing, untouched
      orchestrator.py
      source.py
      runtime.py
      storage.py
      workspace.py
      destination.py
      gates.py
      eval.py
      __main__.py

  scripts/
    run.sh                  <-- existing (direct run, no queue)
    stats.sh                <-- existing
    sw.sh                   <-- NEW: unified CLI wrapper
```

### Why top-level `queue/`, not inside `engines/github_claude/`

The queue dispatches runs. A run is an engine invocation. The queue sits above engines -- it could dispatch to `github_claude`, a future `local_claude` engine, or anything else. Putting it inside an engine would be wrong architecturally.

The queue imports nothing from `engines/`. The engine imports nothing from `queue/`. They communicate through subprocess invocation (the dispatcher spawns `python -m engines.github_claude`).

### CLI namespace

The unified CLI is `sw` (via `scripts/sw.sh` or a symlink):

```bash
# Queue operations
sw submit owner/repo#42 --priority 3 --budget 2.00 --model opus
sw list
sw list --status running
sw status abc12345
sw result abc12345
sw result abc12345 --no-wait
sw logs abc12345 --follow
sw cancel abc12345
sw dashboard
sw metrics --last 8h

# Direct run (no queue, existing behavior)
sw run owner/repo#123 --budget 2.00 --model opus

# Stats
sw stats cost-by-phase
sw stats run-details <run_id>
```

### Daemon management

```bash
# Start dispatcher (foreground, for testing)
python -m queue

# Start via launchd (production)
launchctl load ~/Library/LaunchAgents/com.simple-workflow.dispatcher.plist

# Keepalive (cron fallback)
*/1 * * * * /path/to/simple-workflow/queue/keepalive.sh
```

---

## 4. Implementation Plan

### Task 1: Create queue/shared.py

**Source:** cc_shared.py
**Changes:**
- Rename paths: `~/.cache/cc-queue/` -> `~/.cache/simple-workflow/`
- Remove `MAESTRO_LOG_DIR`, `MAESTRO_WORKTREE_DIR`
- Remove `select_ccs_account()`, `_load_credentials()`, `_refresh_token()`, `_parse_reset_schedule()` (all CCS account logic)
- Keep: `SCHEMA` (jobs + metrics tables), `get_db()`, `is_at_capacity()`, all resource check functions, all constants
- Add `REPO_ROOT = Path(__file__).resolve().parent.parent` for relative path resolution

### Task 2: Create queue/cli.py

**Source:** cc_queue.py
**Changes:**
- Update imports from `cc_shared` to `queue.shared`
- Update `cmd_submit()`:
  - Replace job types with: `issue` (the primary type, replaces worker/pipe), `review` (future), `custom` (freeform task)
  - Replace maestro-specific metadata flags with simple-workflow flags: `--budget`, `--model`, `--repo-path`
  - Keep: `--priority`, `--timeout`, `--submitted-by`
  - The `task` field for `issue` type stores `owner/repo#NNN` (the simple-workflow issue ref format)
- Keep all other commands (status, result, logs, list, cancel, dashboard, metrics) largely unchanged
- Add `cmd_run()` that calls `engines.github_claude` directly (no queue, for backward compatibility with `scripts/run.sh`)

### Task 3: Create queue/dispatcher.py

**Source:** cc_dispatcher.py
**Changes:**
- Update imports from `cc_shared` to `queue.shared`
- Replace `_build_command()` entirely:
  - Single command builder: `["python3", "-m", "engines.github_claude", task, "--budget", str(budget), "--model", model]`
  - The `task` field is already in `owner/repo#NNN` format
  - Set `cwd` to simple-workflow's repo root
  - For `custom` type, fall back to `["claude", task, "--output-format", "json", "--model", model]`
- Remove `select_ccs_account()` call from `dispatch_job()`:
  - Instead: use `CLAUDE_CONFIG_DIR` from environment if set, otherwise omit (use default config)
  - Remove `account` column usage (or keep as optional, set from env var)
- Remove `_find_mc_py()`, `MC_PY`, `MAESTRO_DIR`
- Remove `_cleanup_worktree()` (simple-workflow's workspace.py handles its own cleanup)
- Keep everything else: monitor loop, stall detection, orphan cleanup, recovery, metrics, fast-fail backoff, signal handling, event writing

### Task 4: Create queue/__main__.py

Entry point for `python -m queue` (starts dispatcher) and for `python -m queue.cli` (CLI).

```python
"""Queue dispatcher entry point.

    python -m queue              # start dispatcher daemon
    python -m queue.cli submit   # CLI operations
"""
from queue.dispatcher import main
main()
```

### Task 5: Create queue/__init__.py

Empty or minimal, just makes it a package.

### Task 6: Create scripts/sw.sh

Unified CLI wrapper:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

case "${1:-}" in
    run)
        shift
        python3 engines/github_claude/__main__.py "$@"
        ;;
    stats)
        shift
        ./scripts/stats.sh "$@"
        ;;
    *)
        python3 -m queue.cli "$@"
        ;;
esac
```

### Task 7: Adapt keepalive.sh

**Source:** keepalive.sh
**Changes:**
- Update `PIDFILE` to `~/.cache/simple-workflow/dispatcher.pid`
- Update `LOGFILE` to `~/.cache/simple-workflow/dispatcher.log`
- Update `SCRIPT` path to point to simple-workflow's queue module
- Command: `python3 -m queue` (run from simple-workflow repo root)

### Task 8: Create launchd plist

New file: `queue/com.simple-workflow.dispatcher.plist`

Based on the existing cc-dispatcher pattern, targeting `python3 -m queue` with `WorkingDirectory` set to the simple-workflow repo.

### Task 9: Update CLAUDE.md

Add queue section:

- `queue/cli.py` -- job submission and monitoring CLI
- `queue/dispatcher.py` -- daemon that polls queue and dispatches pipeline runs
- `queue/shared.py` -- SQLite schema, capacity checks
- `scripts/sw.sh` -- unified CLI entry point
- How to start the dispatcher
- How to submit jobs

### Task 10: Verify and test

- Start dispatcher in foreground: `python -m queue`
- Submit a test job: `python -m queue.cli submit owner/repo#1 --priority 5`
- Check it appears: `python -m queue.cli list`
- Watch dispatcher pick it up and spawn `python -m engines.github_claude`
- Verify stall detection works (kill the spawned process, watch re-queue)
- Verify dashboard shows correct counts
- Verify keepalive.sh restarts the dispatcher after kill

---

## 5. What Changes in the Workflow

### Before (cc-queue as separate repo)

```bash
# Submit a job (from cc-queue repo, dispatches to maestro)
cc-queue submit '' --type worker --issue 42 --cwd /path/to/repo

# Direct run (from simple-workflow repo)
./scripts/run.sh owner/repo#42
```

Two repos, two CLIs, two mental models. The queue dispatches to maestro, not to simple-workflow.

### After (merged)

```bash
# Submit a job to the queue (dispatches to simple-workflow pipeline)
sw submit owner/repo#42 --priority 3 --budget 2.00

# Same thing, explicit script path
./scripts/sw.sh submit owner/repo#42

# Direct run (bypass queue, for testing/debugging)
sw run owner/repo#42 --budget 2.00

# Monitor
sw dashboard
sw list
sw logs abc123 --follow
```

One repo. One CLI. The queue is built in. `sw submit` queues it; the dispatcher picks it up and runs the pipeline. `sw run` skips the queue for direct execution.

### What the dispatcher does on each job

1. Picks highest-priority pending job
2. Checks capacity (CPU, RAM, concurrent workers)
3. Builds command: `python3 -m engines.github_claude owner/repo#42 --budget 2.00 --model sonnet`
4. Spawns subprocess with stdout piped
5. Starts monitor thread watching for stalls
6. On completion: marks done/failed in queue DB
7. On stall: marks stalled, increments counter, auto-retries up to 3x with priority boost

### What stays the same

- The engine (`engines/github_claude/`) is completely untouched
- `scripts/run.sh` still works for direct runs
- `scripts/stats.sh` still works for per-run SQLite analysis
- The pipeline phases (triage, plan, test-plan, wave-planner, execute, review) are unchanged
- Per-run .db files still live in `engines/github_claude/runs/`

### Two databases, two purposes

- **Queue DB** (`~/.cache/simple-workflow/queue.db`): operational -- which jobs are pending/running/done, dispatcher metrics
- **Run DBs** (`engines/github_claude/runs/*.db`): analytical -- full replay of every phase, message, tool call in a pipeline run

They serve different purposes and should stay separate. The queue DB tracks "did the job complete?" The run DB tracks "what did the pipeline actually do?"

---

## 6. Naming: `queue/` Package Name Conflict

Python has a built-in `queue` module. Naming our package `queue/` will shadow it. Options:

1. **`dispatcher/`** -- clear, no conflict, but loses the "queue" concept in the name
2. **`jobqueue/`** -- no conflict, keeps the queue concept
3. **`scheduler/`** -- accurate but overloaded term
4. **`runner/`** -- too generic

**Recommendation: `dispatcher/`**. The package does two things: queue management (CLI) and dispatching (daemon). "Dispatcher" is more descriptive of its primary function. The CLI subcommands (`submit`, `list`, `cancel`) make the queue aspect obvious without the package needing "queue" in its name.

Updated layout:

```
simple-workflow/
  dispatcher/
    __init__.py
    __main__.py         # starts daemon
    cli.py              # sw submit/list/status/cancel/dashboard
    daemon.py           # poll-dispatch-monitor loop
    shared.py           # DB schema, capacity checks
    keepalive.sh
```

---

## 7. Task Execution Order

Tasks are independent enough for an agent to execute sequentially:

1. **Task 1** (shared.py) -- no dependencies, foundation for everything else
2. **Task 5** (__init__.py) -- trivial, needed for imports
3. **Task 2** (cli.py) -- depends on shared.py
4. **Task 3** (daemon.py) -- depends on shared.py
5. **Task 4** (__main__.py) -- depends on daemon.py
6. **Task 6** (sw.sh) -- depends on cli.py existing
7. **Task 7** (keepalive.sh) -- standalone
8. **Task 8** (launchd plist) -- standalone
9. **Task 9** (CLAUDE.md update) -- after all code exists
10. **Task 10** (testing) -- after everything is in place

Estimated effort: 2-3 hours for an agent, most of it in Tasks 2-3 (the substantial code adaptation).
