# cc-queue vs simple-workflow: Comparison Report

## 1. What cc-queue Does

cc-queue is a **local job queue and dispatcher daemon** for running Claude Code sessions on a single machine. It solves the problem of safely running multiple concurrent Claude Code sessions with resource management.

### Components

- **cc_queue.py** -- CLI for submitting, listing, cancelling, and monitoring jobs. Stores jobs in a SQLite database at `~/.cache/cc-queue/queue.db`.
- **cc_dispatcher.py** -- Long-running daemon (managed by launchd) that polls the queue every 5 seconds and spawns subprocesses. Runs monitor threads per job for stall detection.
- **cc_shared.py** -- Shared constants, DB schema, capacity checks (CPU load, RAM, max workers), and CCS (Claude Code Subscription) account rotation logic.
- **keepalive.sh** -- Cron-based keepalive that restarts the dispatcher if it dies.

### What it actually manages

cc-queue does NOT implement any pipeline logic. It dispatches to **external** executors:

- **`worker` jobs** -- calls `python3 -m workflows.pipe --issue N` (the maestro repo's pipe.py)
- **`pr-review` jobs** -- calls `python3 -m workflows.pr_review --pr N` (maestro's pr_review)
- **`mc` jobs** -- calls `mc.py` (maestro's multi-Claude orchestrator)
- **`pipe` jobs** -- calls `python3 -m workflows.pipe --task "..."` (maestro's pipe.py with freeform task)

### Key capabilities

- Priority queue (1-9, lower = higher priority)
- Concurrent worker limit (MAX_WORKERS=5) with CPU/RAM backpressure
- Stall detection (120s no-stdout timeout, 300s startup grace)
- Auto-retry of stalled jobs (up to 3 retries with priority boost)
- CCS account rotation (picks account closest to billing reset)
- Token refresh for expired OAuth credentials
- Orphan process cleanup (kills reparented claude/node processes)
- System metrics recording (RAM, CPU, swap, worktree count)
- Job logs, events, dashboard

---

## 2. What simple-workflow Does

simple-workflow is a **pipeline that transforms GitHub issues into tested, reviewed PRs**. It is the actual execution engine -- the thing that reads an issue, decomposes it into tasks, plans implementation, executes code changes, reviews the diff, and opens a PR.

### Architecture

- **engines/github_claude/orchestrator.py** -- Phase sequencer: triage -> plan -> test-plan -> wave-planner -> execute -> review -> PR creation
- **engines/github_claude/source.py** -- Fetches GitHub issues via `gh` CLI
- **engines/github_claude/runtime.py** -- Calls Claude CLI as subprocess, parses JSON response, tracks tokens/cost
- **engines/github_claude/storage.py** -- Per-run SQLite .db file with 5 tables (run, phase, message, tool_call, event)
- **engines/github_claude/workspace.py** -- Git worktree lifecycle
- **engines/github_claude/destination.py** -- Pushes branch, creates GitHub PR
- **engines/github_claude/gates.py** -- Validation gates between phases

### Key capabilities

- Multi-phase pipeline with configurable models per phase (workflow.yaml)
- Parallel execution within waves (ThreadPoolExecutor)
- Prose-first prompts with separate extraction calls for structured data
- Budget tracking with hard caps
- Gate validation between phases
- Prior run context injection (learns from previous failures on same issue)
- Per-run SQLite database as complete audit trail
- Red/green test gates for TDD enforcement

---

## 3. Do They Overlap?

**No. They solve completely different problems at different layers of the stack.**

| Concern | cc-queue | simple-workflow |
|---------|----------|-----------------|
| What to run next | Yes (priority queue, scheduling) | No |
| How to run it | Subprocess dispatch, stall monitoring | No (it IS the thing being run) |
| Resource management | CPU/RAM/swap backpressure, max workers | No |
| Account rotation | CCS credential management | No |
| Pipeline logic | None -- delegates to external executors | Yes (triage, plan, execute, review) |
| Issue decomposition | No | Yes (triage phase) |
| Code generation | No | Yes (execute phase) |
| PR creation | No | Yes (destination.py) |
| Run observability | Job-level logs and events | Phase/message/tool-call level SQLite |

cc-queue is an **operations layer** (what runs, when, with what resources). simple-workflow is a **pipeline layer** (how to turn an issue into a PR).

---

## 4. Do They Complement Each Other?

**Yes, perfectly.** cc-queue is the natural dispatch layer for simple-workflow runs.

Currently, cc-queue dispatches to maestro's `pipe.py` and `pr_review.py`. simple-workflow is a cleaner, more observable replacement for that pipeline logic. The integration path is straightforward:

1. Add a new job type to cc-queue (e.g., `sw` or `simple-workflow`)
2. In `cc_dispatcher.py:_build_command()`, add a branch that builds: `python -m engines.github_claude <repo> <issue> --budget N --model M`
3. cc-queue handles: when to run it, resource limits, stall detection, account rotation, retry
4. simple-workflow handles: how to decompose the issue, plan, execute, review, and create the PR

This is NOT redundant. cc-queue brings capabilities that simple-workflow does not have and should not have:

- **Concurrency control** -- simple-workflow is single-threaded (one pipeline run). cc-queue manages 5 concurrent runs with backpressure.
- **Account rotation** -- simple-workflow has no concept of CCS accounts or billing cycles.
- **Stall detection** -- cc-queue's monitor threads catch hung Claude sessions. simple-workflow's internal timeouts are per-phase, not per-process.
- **Machine-level resource management** -- RAM/CPU/swap checks before dispatching new work.

---

## 5. Should They Be Merged?

**No. Keep them separate.**

They operate at different layers and have different concerns:

- **cc-queue** is infrastructure that could dispatch ANY kind of Claude Code job. It already handles 4 job types (worker, pr-review, mc, pipe). Adding simple-workflow as a 5th type is trivial.
- **simple-workflow** is a pipeline definition that should remain self-contained and testable in isolation. Merging queue management into it would violate its own design principle of "simple, minimal code."

The only thing that should change: when simple-workflow matures enough to replace maestro's pipe.py for issue-to-PR work, cc-queue's `worker` job type should point to simple-workflow instead of maestro.

---

## 6. Recommendation

### Short-term (now)

1. **Add a `sw` job type to cc-queue** that dispatches to `python -m engines.github_claude`. This is a ~20-line change in `cc_dispatcher.py:_build_command()`.
2. **Test the integration** by submitting: `cc-queue submit '' --type sw --issue 42 --cwd /path/to/target-repo`
3. **Keep both repos separate.** cc-queue is infrastructure; simple-workflow is pipeline logic.

### Medium-term (when simple-workflow is battle-tested)

4. **Migrate cc-queue's `worker` type** from maestro's pipe.py to simple-workflow's orchestrator. This replaces the executor, not the queue.
5. **Use cc-queue's metrics** to compare simple-workflow's success rate and cost against maestro's pipe.py on the same issues.

### What NOT to do

- Do not merge the repos.
- Do not add queue/scheduling logic to simple-workflow.
- Do not add pipeline phases to cc-queue.
- Do not deprecate either one -- they serve different purposes.

### Architecture summary

```
cc-queue (operations layer)
    |
    |-- dispatches --> simple-workflow (pipeline layer)
    |-- dispatches --> maestro/pipe.py (legacy pipeline)
    |-- dispatches --> maestro/pr_review.py
    |-- dispatches --> maestro/mc.py
    |
    manages: concurrency, accounts, stalls, retries, resources
```

simple-workflow is the pipeline. cc-queue is the scheduler that runs it.
