# Repository Audit: simple-workflow

Audited 2026-06-16. Every source file read in full.

---

## 1. Current Structure

```
simple-workflow/
  CLAUDE.md                          # Developer guide
  pyproject.toml                     # Package config (pydantic, pyyaml deps)
  schemas.py                         # Pydantic I/O contracts for all phases
  db.py                              # SQLite schema + CRUD
  engine/
    __init__.py                      # Empty
    agent.py                         # Claude CLI wrapper (subprocess call, retry, JSON parse)
    orchestrator.py                  # CLI entry point, phase dispatch, context assembly, GitHub ops
    gates.py                         # Validation gates (file checks, DAG cycle, red/green test gates)
    waves.py                         # Wave execution engine (parallel dispatch, progress checkpointing)
    worktree.py                      # Git worktree lifecycle (create, cleanup, context manager)
  workflows/issue-to-pr/
    workflow.yaml                    # Phase definitions, model config, budget, gate declarations
    prompts/
      triage.md                      # Triage agent prompt template
      plan.md                        # Plan agent prompt template
      test-plan.md                   # Test plan agent prompt template
      execute.md                     # Execute agent prompt template
      wave-planner.md                # Wave planner prompt template
      review.md                      # Review agent prompt template
  scripts/
    run.sh                           # Shell entry point (5 lines)
    stats.sh                         # SQLite dashboard queries
  runs/                              # Gitignored runtime artifacts
    runs.db                          # Shared SQLite database
    pipeline.db                      # Appears unused/stale
    <uuid>/                          # Per-run prompt/response files + progress JSON
  work/                              # Research/notes (not runtime)
```

### How they connect

1. `scripts/run.sh` calls `python3 -m engine.orchestrator`
2. `orchestrator.py:main()` parses CLI args, calls `run_pipeline()`
3. `run_pipeline()` sequences: fetch issue -> create worktree -> triage -> plan+test-plan (parallel) -> wave-planner -> execute waves -> review -> push + PR
4. Each phase: `_run_phase()` loads prompt template from `workflows/*/prompts/`, calls `inject_context()` to fill placeholders, calls `agent.run_agent()` (subprocess to `claude` CLI), parses JSON from response
5. `waves.execute_waves()` handles parallel task dispatch within waves, calling `agent.run_agent()` directly
6. `gates.py` validates phase outputs and runs red/green test gates
7. `db.py` records everything to `runs/runs.db`
8. `worktree.py` manages git worktree lifecycle for isolated execution

---

## 2. Core Concerns -- Where Each Lives

### LLM Calling
- **Primary location**: `engine/agent.py` (lines 100-275)
- Wraps `claude` CLI via `subprocess.run()` with `--output-format json --permission-mode auto`
- Handles: retry logic (3 attempts, exponential backoff on 429/500/503), timeout, JSON event stream parsing, cost/token extraction
- Also: `_parse_json()` (lines 47-97) -- extracts first JSON object from prose responses

### Orchestration
- **Primary location**: `engine/orchestrator.py:run_pipeline()` (lines 248-586)
- One giant function (338 lines) that sequences all 6 phases
- Also contains: `_run_phase()` helper (lines 589-622), budget checking, error routing

### Prompt Management
- **Templates**: `workflows/issue-to-pr/prompts/*.md` (6 files)
- **Context injection**: `orchestrator.py:inject_context()` (lines 135-196)
- **Knowledge loading**: `orchestrator.py` lines 39-45 (`KNOWLEDGE_FILES` dict) + injection logic in `inject_context()`
- Placeholders: `{issue_number}`, `{issue_body}`, `{prior_phases}` -- simple string replacement

### State/Storage
- **Schema + CRUD**: `db.py` (150 lines)
- Three tables: `pipeline_runs`, `phase_logs`, `reviews`
- Single shared `runs/runs.db` file (not per-run as desired)
- File artifacts: prompts/responses saved to `runs/<uuid>/<phase>-prompt.txt` and `-response.txt`
- Wave progress: `runs/<uuid>/build-progress.json` (checkpointing in `waves.py`)

### Git Operations
- **Worktree lifecycle**: `engine/worktree.py` (148 lines)
- **Branch push + PR creation**: `orchestrator.py` lines 507-543
- **Commit checking**: `gates.py:check_commits_exist()` (lines 224-241)
- **Diff extraction**: `orchestrator.py` lines 469-473

### Validation/Gates
- **Primary location**: `engine/gates.py` (241 lines)
- Triage: file existence check, task count max 5
- Plan: DAG cycle detection (Kahn's algorithm), file path plausibility
- Test plan: field presence checks
- Wave planner: all tasks assigned, no duplicates, wave size limit
- Execute: red gate (tests must fail), green gate (tests must pass), commit exists
- Test command allowlist (lines 16-28)

### GitHub/GitLab Integration
- **Issue fetching**: `orchestrator.py:fetch_issue()` (lines 94-126) -- `gh issue view` / `glab issue view`
- **PR creation**: `orchestrator.py` lines 521-543 -- `gh pr create` / `glab mr create`
- **Platform detection**: `orchestrator.py:detect_platform()` (lines 56-68)

### Observability
- **Cost tracking**: `db.py:update_spent()`, `orchestrator.py` accumulates `spent_usd`
- **Phase logging**: `db.py:log_phase()` with duration, tokens, cost, stop_reason, prompt/output hashes
- **Stats dashboard**: `scripts/stats.sh` (SQLite queries)
- **Error capture**: `agent.py:save_error()` writes `error.json`
- **Console output**: `print()` statements scattered through `orchestrator.py`
- **Python logging**: `log = logging.getLogger(__name__)` in agent.py, gates.py, waves.py -- but never configured

---

## 3. What's Tangled

### orchestrator.py is a God module (710 lines, 8+ concerns)

`engine/orchestrator.py` mixes:
1. **CLI argument parsing** (lines 658-706) -- `argparse` setup and `main()`
2. **Phase sequencing** (lines 248-586) -- the `run_pipeline()` function
3. **Prompt context assembly** (lines 135-196) -- `inject_context()` with knowledge file loading
4. **GitHub/GitLab issue fetching** (lines 94-126) -- subprocess calls to `gh`/`glab`
5. **PR creation** (lines 507-543) -- subprocess calls to `gh pr create`/`glab mr create`
6. **Target repo discovery** (lines 625-649) -- filesystem scanning for the repo
7. **Platform detection** (lines 56-92) -- git remote URL parsing
8. **DB logging orchestration** (lines 199-222) -- `_log_agent_result()` wrapper

If you wanted to swap in a different git hosting provider (e.g., Gitea, Bitbucket) you'd have to edit the orchestrator's core flow. Same for changing how prompts are assembled or how the DB is written.

### waves.py re-opens DB connections

`engine/waves.py:execute_task()` (lines 71-91, 125-145) imports and re-initializes the DB module in a thread-unsafe pattern:
```python
import db as db_mod
from db import log_phase
thread_conn = db_mod.init_db(db_path)
```
This duplicates the logging concern that `orchestrator.py` handles elsewhere, and the import-inside-function pattern makes dependencies invisible.

### agent.py handles prompt/response file I/O

`engine/agent.py` (lines 113-117, 302-309) saves prompt and response files to disk. This is a storage/observability concern mixed into the LLM caller. The caller should return results; the orchestrator or a storage layer should decide what to persist.

### orchestrator.py hardcodes repo discovery heuristics

`_find_target_repo()` (lines 625-649) contains filesystem-specific logic (checking `~/services/`, `~/repos/`, `~/Desktop/personal-assistant-clones/`) that couples the orchestrator to the developer's machine layout.

### Prompt templates demand JSON but prompts say "prose-first"

The execute and review prompts say "output a summary" / "state your verdict", but `agent.py:_parse_json()` tries to extract JSON from responses. The `orchestrator.py:_run_phase()` function (line 620) falls back to `{}` if no JSON found. This ambiguity means phases sometimes produce parseable JSON and sometimes don't, with silent degradation.

---

## 4. Modularization Suggestions

### Target architecture: 7 replaceable modules

```
engine/
  llm.py           # LLM caller interface (Protocol class + Claude CLI implementation)
  orchestrator.py  # Pure sequencing logic -- no I/O, no subprocess calls
  prompts.py       # Template loading, context injection, knowledge assembly
  store.py         # Storage interface (Protocol) + SQLite implementation
  git.py           # All git operations: worktree, diff, push, branch
  gates.py         # Unchanged -- already clean
  waves.py         # Wave execution (takes an LLM caller, not subprocess directly)
  github.py        # GitHub/GitLab integration (issue fetch, PR create, comments)
  cli.py           # CLI entry point only -- argparse, wire modules, call orchestrator
```

### Specific boundaries

**LLM Caller interface** (replace Claude CLI with API, or another LLM):
```python
class LLMCaller(Protocol):
    def call(self, prompt: str, *, model: str, cwd: str, max_turns: int) -> AgentResult: ...
```
- Current `agent.py:run_agent()` becomes `ClaudeCLICaller.call()`
- File I/O (saving prompts/responses) moves to `store.py`
- JSON parsing moves to a utility or stays in the caller (it's LLM-response-specific)

**Storage interface** (replace SQLite with per-run .db, or Postgres, or plain files):
```python
class RunStore(Protocol):
    def create_run(self, ...) -> None: ...
    def log_phase(self, ...) -> None: ...
    def update_spent(self, ...) -> None: ...
    def finish_run(self, ...) -> None: ...
    def save_artifact(self, run_id: str, name: str, content: str) -> None: ...
```
- `db.py` becomes `SQLiteRunStore(RunStore)`
- `waves.py` stops managing its own DB connections -- receives a store instance

**GitHub interface** (replace gh CLI with API client, or support Gitea):
```python
class IssueTracker(Protocol):
    def fetch_issue(self, owner: str, repo: str, number: int) -> str: ...
    def create_pr(self, ...) -> str: ...  # returns PR URL
    def post_comment(self, ...) -> None: ...
```

**Orchestrator** becomes a pure coordinator:
```python
def run_pipeline(
    issue_tracker: IssueTracker,
    llm: LLMCaller,
    store: RunStore,
    git: GitOps,
    prompts: PromptManager,
    config: WorkflowConfig,
    issue_ref: str,
) -> PipelineResult:
```
- No subprocess calls
- No file I/O
- No argparse
- Just sequences phases, checks gates, tracks budget

### Migration path (incremental, not rewrite)

1. Extract `github.py` (issue fetch + PR creation) -- low risk, immediate cleanup
2. Extract `prompts.py` (inject_context + knowledge loading) -- medium effort
3. Extract `cli.py` (argparse + main) -- trivial
4. Add Protocol classes as interfaces -- no behavior change, just type annotations
5. Give `waves.py` a store instance instead of `db_path` -- thread-safety fix
6. Move file I/O out of `agent.py` -- clean separation

---

## 5. What's Missing

### Referenced but not implemented

- **`max_turns` from workflow.yaml is ignored**: `workflow.yaml` defines `max_turns: 10` per phase (lines 24-42), but `orchestrator.py:_run_phase()` never passes `max_turns` to `run_agent()`. The agent always gets the default `max_turns=30` from `agent.py:run_agent()` signature.

- **`models` section in workflow.yaml is decorative**: The `models:` block (lines 10-21) defines cost rates and max_tokens, but nothing reads those values. Cost comes from the Claude CLI response (`total_cost_usd`). Max tokens are never sent to Claude.

- **`gates:` section in workflow.yaml is declarative only**: The `gates:` block (lines 52-68) lists gate names but the orchestrator doesn't read this config. Gates are hardcoded into `run_pipeline()`. Adding a new gate requires code changes, not config changes.

- **`parallel:` field in workflow.yaml phases is unused**: `parallel: per_task` and `parallel: per_wave` (lines 30, 33, 39) are never read by the orchestrator. The parallel behavior is hardcoded.

- **`hit_turn_limit` and `hit_timeout` columns**: `phase_logs` schema (lines 36-37) has these columns but they're never written to (always default 0). The agent detects timeouts but never sets `hit_timeout=1`.

- **`review.level: per_run` config**: `workflow.yaml` line 49 declares `level: per_run` but there's no per-task review option implemented.

### Per-run DB migration not done

CLAUDE.md and the `work/` research files discuss moving to one `.db` per run, but the code still uses a single shared `runs/runs.db`. The stale `runs/pipeline.db` file suggests an earlier attempt that was abandoned.

### No `__main__.py`

`pyproject.toml` defines `sw = "engine.orchestrator:main"` but there's no `engine/__main__.py`, so `python3 -m engine` doesn't work -- only `python3 -m engine.orchestrator` does.

### No tests

`CLAUDE.md` says `python3 -m pytest tests/` but there's no `tests/` directory. Zero test coverage for the pipeline itself.

### Logging never configured

All modules use `logging.getLogger(__name__)` but nothing calls `logging.basicConfig()` or configures handlers. All `log.info()` / `log.warning()` calls are silent unless the caller sets up logging externally.

### No error recovery / resume

`waves.py` has progress checkpointing (`build-progress.json`) but the orchestrator has no `--resume <run_id>` flag to restart from a checkpoint. If a run fails at the review phase, you re-run the entire pipeline from scratch.

---

## 6. Simplification Opportunities

### Dead / over-engineered code

1. **`_parse_json()` brace-matching parser** (agent.py lines 59-86): 28 lines of hand-rolled JSON extraction with depth tracking, escape handling. In practice, the Claude CLI with `--output-format json` returns structured event JSON. The only time this parser runs is when extracting JSON from the agent's prose `result` field. A simpler approach: if the response starts with `{`, try `json.loads()`. If not, try regex for ```json fences. The brace-counting parser handles edge cases that likely never occur with LLM output.

2. **`MODEL_MAP`** (agent.py lines 19-23): Maps "haiku" to "haiku", "sonnet" to "sonnet", "opus" to "opus". This is a no-op identity map. It exists to support future aliases but currently adds indirection for zero value.

3. **`workflow.yaml` models/gates/parallel sections**: As noted in section 5, these config blocks are never consumed by code. They document intent but create false expectations that the pipeline is config-driven when it's actually code-driven.

4. **`@contextmanager worktree()`** (worktree.py lines 141-148): Defined but never used -- the orchestrator calls `create_worktree()` and `cleanup_worktree()` manually in a try/finally instead.

5. **`cleanup_all_worktrees()`** (worktree.py lines 114-138): Exported but never called from anywhere in the codebase.

6. **`PhaseContext` dataclass** (schemas.py lines 12-20): Defined but never instantiated anywhere in the codebase. The orchestrator passes around loose dicts and kwargs instead.

7. **`runs/pipeline.db`**: Stale file, not referenced by any code.

### Opportunities to simplify

1. **Remove `_find_target_repo()` heuristics** -- take the target repo path as a CLI argument or environment variable instead of guessing filesystem locations. This removes 25 lines of fragile path scanning.

2. **Use the `worktree()` context manager** instead of manual create/cleanup in try/finally. Saves 6 lines and eliminates the risk of forgetting cleanup.

3. **Make `workflow.yaml` either authoritative or remove it**: Either the orchestrator reads phase config (max_turns, gates, parallel strategy) from YAML, or the YAML is deleted in favor of hardcoded pipeline logic. The current state where YAML exists but is mostly ignored is confusing.

4. **Consolidate print + logging**: The orchestrator uses `print()` for user-facing output and `log.*()` for debug output, but logging is never configured. Pick one: either configure logging with a console handler, or use print exclusively and remove the logging imports.

5. **`waves.py` DB access pattern**: Instead of importing db_mod inside a thread and creating a new connection, pass a thread-safe store interface. This removes 20 lines of duplicated boilerplate in `execute_task()`.

---

## Summary of Key Issues (prioritized)

| Priority | Issue | Location | Impact |
|----------|-------|----------|--------|
| 1 | orchestrator.py is a 710-line God module | `engine/orchestrator.py` | Blocks any modular replacement |
| 2 | `max_turns` from config never passed to agent | `orchestrator.py:612`, `agent.py:105` | Agents get 30 turns regardless of phase config |
| 3 | Shared runs.db instead of per-run DB | `orchestrator.py:269` | Conflicts with stated design goal |
| 4 | No tests for the pipeline itself | nowhere | Regression risk on every change |
| 5 | GitHub/GitLab ops embedded in orchestrator | `orchestrator.py:94-126, 507-543` | Can't swap hosting providers |
| 6 | waves.py thread-unsafe DB pattern | `engine/waves.py:71-91` | Potential corruption under concurrency |
| 7 | Logging never configured | all modules | Silent failures, no observability |
| 8 | workflow.yaml mostly decorative | `workflows/issue-to-pr/workflow.yaml` | False expectations of config-driven behavior |
| 9 | `PhaseContext` defined but unused | `schemas.py:12-20` | Dead code confusion |
| 10 | File I/O mixed into agent.py | `engine/agent.py:113-117, 302-309` | LLM caller can't be used without filesystem |
