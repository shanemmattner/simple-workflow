# simple-workflow

Pipeline that transforms GitHub issues into tested, reviewed PRs using `claude -p` as the execution engine.

## Run

```bash
python3 workflows/shftty-web/run.py /path/to/repos/shftty main --issue 896 --budget 2.00 --model opus
python3 workflows/tunedvoice/run.py /path/to/repos/tunedvoice main --issue 42
```

Arguments: `<repo_path> <git_ref> [--issue N] [--budget 10.0] [--model sonnet]`

## Phases

1. **triage** -- assess the issue, decide scope (can emit SKIP or ESCALATE to halt)
2. **plan** (optional) -- decompose into `### Step N` / `### Task N` blocks. Skipped if `prompts/plan.md` doesn't exist; triage output used as the task list instead.
3. **execute** -- one `claude -p` sub-agent per task, sequential, each commits when done
4. **review** -- reviews the combined diff, emits PASS/WARN/FAIL verdict
5. **improve** (optional) -- fix review findings. Skipped if `prompts/improve.md` doesn't exist.

After review (or improve), the branch is pushed to origin.

## Create a new workflow

1. Copy any existing workflow folder (e.g. `cp -r workflows/shftty-web workflows/my-project`)
2. Edit the prompts in `prompts/` for your domain
3. Run it -- `run.py` is generic. Workflow name and branch prefix are auto-derived from the directory name.

## Key files

- `workflows/<name>/run.py` -- 242-line self-contained orchestrator (identical across workflows)
- `workflows/<name>/prompts/` -- frozen prompt templates per phase (triage, plan, execute, review, improve)
- `scripts/run.sh` -- shell wrapper with run-lock and wall-clock timeout
- `scripts/stats.sh` -- SQLite dashboard queries
- `scripts/status.sh` -- check running pipelines

## Active workflows

| Workflow | Prompts | Notes |
|---|---|---|
| shftty-web | triage, plan, execute, review, improve | |
| shftty-android | triage, plan, execute, review, improve | |
| shftty-ios | triage, plan, execute, review, improve | |
| tunedvoice | triage, plan, execute, review, improve | |
| cody-business | triage, execute, review, improve | No plan phase |
| family-caregiving | triage, execute, review, improve | No plan phase |
| steadion-deal | triage, execute, review | No plan or improve |
| issue-to-pr | (prompt archive only) | No run.py -- kept as reference |

## Stall protection

Three layers protect against hung `claude -p` processes:
1. **Env vars**: `CLAUDE_STREAM_IDLE_TIMEOUT_MS=600000`, `API_TIMEOUT_MS=1200000` (set automatically)
2. **Wall-clock cap**: `gtimeout` wrapper in `scripts/run.sh` (default 60 min, override via `CLAUDE_WALL_TIMEOUT_S`)
3. **Run lock**: `scripts/lock_exec.py` prevents concurrent runs against the same issue/ref

## Important

- Prompts are frozen templates. Change them deliberately. Each prompt can have YAML frontmatter for `model:` and `max_turns:` overrides.
- Target repos provide context via `.workflows/` (context.md, testing.md, knowledge/).
- Phase outputs are written to `.workflow-outputs/` inside the worktree (gitignored).
- Budget tracking is per-run. Pipeline exits immediately when spend exceeds `--budget`.

## Test

```bash
python3 -m pytest tests/
```
