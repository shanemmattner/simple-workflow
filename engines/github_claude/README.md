# github_claude engine

Reads a GitHub issue via `gh` CLI, creates an isolated git worktree, runs Claude Code CLI through a multi-phase pipeline (triage, plan, test-plan, wave-planner, execute, review), and opens a GitHub PR with the result. Every LLM call is logged to a per-run SQLite database for replay and evaluation.

## Files

| File | What it does |
|------|-------------|
| `source.py` | Fetches GitHub issues (title, body, comments, labels) via `gh`. Posts status comments and updates labels. |
| `runtime.py` | Runs `claude` CLI as a subprocess with `--output-format json`. Parses the response envelope, extracts tokens/cost/timing. Retries on rate limits. |
| `storage.py` | Creates one SQLite `.db` file per run. Five tables: `run`, `phase`, `message`, `tool_call`, `event`. Full replay from a single file. |
| `workspace.py` | Creates git worktrees under `<repo>/.sw-worktrees/<branch>` for isolation. Reports diffs. Cleans up on completion. |
| `destination.py` | Pushes the branch to origin and creates a GitHub PR via `gh pr create`. Formats the PR body with phase costs and review findings. |
| `eval.py` | LLM-as-judge scoring, failure categorization, cross-run pattern detection, prompt improvement proposals. (Stubs — not yet implemented.) |
| `__init__.py` | Package marker. |
| `agents/` | Agent definitions for this engine. (Empty — placeholder.) |
| `tools/` | Helper scripts for agent tool use. (Empty — placeholder.) |
| `runs/` | Output directory for `.db` files. Gitignored. |

## How to run

```bash
python -m engines.github_claude owner/repo#123
```

Or via the project wrapper:

```bash
./scripts/run.sh owner/repo#123
./scripts/run.sh owner/repo#123 --budget 2.00 --model opus
```

## Prerequisites

- **Python 3.11+**
- **`gh` CLI** — authenticated (`gh auth status` should show logged in)
- **`claude` CLI** — authenticated via subscription (no API key needed)
- **Git** — the target repo must be cloned locally

## Where output goes

Each run produces a single SQLite file in `engines/github_claude/runs/`:

```
runs/owner-repo-42-20260614-1430.db
runs/owner-repo-42-20260614-1505.db
```

Filename convention: `<owner>-<repo>-<issue_number>-<YYYYMMDD-HHMM>.db`

The `.db` file is self-contained — every phase outcome, every message, every tool call, every event. You can replay or analyze any run from its `.db` alone.

## How it works

1. **Fetch issue** — `source.fetch_issue()` pulls the issue title, body, comments, and metadata from GitHub.
2. **Create workspace** — `workspace.create_workspace()` makes a git worktree on a fresh branch (`issue-<number>-<id>`) off `main`.
3. **Run phases** — The orchestrator reads `workflow.yaml` and runs each phase in sequence: triage, plan, test-plan, wave-planner, execute, review. Each phase calls `runtime.call_agent()` with the phase's prompt, model, and max turns. Gates between phases validate the output before proceeding.
4. **Log everything** — `storage.log_phase()`, `log_message()`, `log_tool_call()`, and `log_event()` write to the run's `.db` file as each phase executes.
5. **Create PR** — `destination.push_branch()` pushes the branch, then `destination.create_pr()` opens a PR with review findings and cost breakdown in the body.
6. **Evaluate** — `eval.judge_run()` scores the run, categorizes failures, and proposes prompt edits for the next attempt.

Prior runs on the same issue are discovered via `storage.find_prior_runs()` and their review findings are injected into the next run's triage prompt, so the agent learns from past failures.

## Configuration

Phase sequencing, model assignments, max turns, and gates are all controlled by `workflows/issue-to-pr/workflow.yaml`:

```yaml
phases:
  - name: triage
    model: sonnet
    max_turns: 10
  - name: plan
    model: sonnet
    max_turns: 10
    parallel: per_task
  - name: execute
    model: sonnet
    max_turns: 30
    parallel: per_wave
  - name: review
    model: haiku
    max_turns: 5
```

Models are defined in the same file (`haiku`, `sonnet`, `opus` with token limits and per-token costs). Budget cap is `max_per_run_usd`.

## How to fork this into a new engine

```bash
cp -r engines/github_claude engines/my-new-engine
```

Then swap the modules that differ. Common forks:

- **Different source** (GitLab, local markdown) — rewrite `source.py`
- **Different runtime** (OpenAI Codex, local LLM) — rewrite `runtime.py`
- **Different destination** (GitLab MR, local patch file) — rewrite `destination.py`
- **Same storage/workspace** — keep them as-is, or copy and modify

No interfaces to implement. No registration. Just Python files that import each other.
