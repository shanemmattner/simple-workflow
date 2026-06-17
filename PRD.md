# PRD: simple-workflow

**Product Requirements Document — v1.0**
**Date: 2026-06-16**

---

## 1. Vision

simple-workflow transforms GitHub issues into tested, reviewed pull requests. It is an infrastructure layer that wraps around target repositories to maintain them autonomously.

The system is a display piece: clean architecture, easy to study, minimal code. Every design decision optimizes for observability, prompt quality, and solving classes of problems rather than one-offs.

### Core Tenets

1. **Observability** — Every LLM call logged to SQLite. That is how we improve. No silent failures, no lost context.
2. **Built-in evaluation** — Analysis of every tool call, every response. Evaluation is a natural part of execution, not bolted on after.
3. **Prompt quality** — Good prompts let you use smaller/cheaper models. Invest in prompts. Version them. Measure them.
4. **Solve classes of problems** — Workflows are templates for problem categories. `issue-to-pr` is the first workflow, not the only one.
5. **Repo infrastructure layer** — The pipeline wraps around target repositories. It reads their context, respects their conventions, and maintains them.
6. **Simple, minimal code** — Clean interfaces, replaceable modules, no framework dependencies. An engineer reads this codebase in an afternoon.

---

## 2. Architecture

```
workflows/           ← prompt templates, portable across engines
  issue-to-pr/
    prompts/
    workflow.yaml

engines/
  github_claude/     ← our first engine, self-contained
    README.md        ← what's here, how it works
    source.py        ← reads GitHub issues via gh CLI
    runtime.py       ← calls Claude CLI (subscription, --permission-mode auto --max-turns N)
    storage.py       ← per-run SQLite (.db per run)
    workspace.py     ← git worktree isolation
    destination.py   ← creates GitHub PR
    eval.py          ← LLM-as-judge, failure categorization
    orchestrator.py  ← thin phase sequencing
    agents/          ← agent definitions for this engine
    tools/           ← helper scripts
    runs/            ← output .db files (gitignored)
```

### Core Principles

1. **Each engine is a folder with everything it needs.** Open it, understand it.
2. **No abstract interfaces. No plugin system. No dynamic loading.** Just Python files that import each other.
3. **Want a new engine? Copy the folder, swap what's different.** Fork-friendly, not framework-friendly.
4. **Files stay modular so they CAN be reused across engines, but there's no machinery enforcing it.** If two engines share 80% of their storage code, that's fine — copy it. Extract a shared lib later if it earns its keep.
5. **Workflows (prompts) are separate and work with any engine.** The prompts are the valuable IP. Engines are plumbing.
6. **Keep it stupid simple. Copy-paste friendly.** An engineer reads one engine folder in an afternoon. No jumping between abstraction layers.
7. **README in each engine folder explains what's there and how it works.** Self-documenting at the folder level.

### Module Responsibilities

| File | What it does |
|------|-------------|
| `source.py` | Reads GitHub issues via `gh` CLI. Returns issue body, comments, metadata. Posts status comments back. |
| `runtime.py` | Calls Claude CLI (`claude "<prompt>" --output-format json --model <model> --permission-mode auto --max-turns N`). Parses response JSON. Tracks tokens/cost/timing. |
| `storage.py` | One SQLite `.db` file per run. Creates tables, logs phases/messages/tool_calls/events. Self-contained replay of entire run. |
| `workspace.py` | Creates git worktree on a branch. Provides isolated directory for agents. Reports diff when done. Cleans up. |
| `destination.py` | Pushes branch, creates GitHub PR with review findings in body. Updates issue labels. |
| `eval.py` | LLM-as-judge scoring of completed runs. Failure categorization. Pattern accumulation. Prompt edit proposals. |
| `orchestrator.py` | Reads `workflow.yaml`, sequences phases, calls runtime for each phase, logs to storage, checks gates between phases. Thin — most logic lives in the other modules. |

### Why Not Interfaces?

The previous design had Protocol classes so you could "swap any implementation without touching other modules." In practice:

- We have one engine (GitHub + Claude). We're not building a framework for hypothetical future engines.
- When we DO want a second engine, copying a folder and changing what's different is faster and clearer than implementing abstract interfaces.
- Interfaces add indirection. You read `source.py` and immediately see what it does. No hunting for "which implementation is active?"
- If shared code emerges naturally, extract it then. Don't pre-architect it.

---

## 3. Data Flow

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                     ORCHESTRATOR                         │
                    │                                                         │
  source.fetch() ──▶│  WorkRequest ──▶ inject context ──▶ runtime.call() ──▶ gate │
                    │       │                    │                 │            │ │
                    │       ▼                    ▼                 ▼            ▼ │
                    │   WorkRequest        FullPrompt        AgentResult     Pass │
                    │                                                       /Fail │
                    └─────────────┬────────────────────────────────────┬──────────┘
                                  │                                    │
                                  ▼                                    ▼
                            storage.log_*()                  destination.deliver()
                                  │                          (push + create PR)
                                  ▼
                          runs/<repo>-<issue>-<timestamp>.db
```

### Phase-by-phase data flow:

| Phase | Input | Output | Passes to next |
|-------|-------|--------|----------------|
| **triage** | issue body, repo context, prior run reviews | tasks[], proof_type, escalate flag | task list for plan/test-plan |
| **plan** (per task) | task description, triage output, repo context | steps[] with writes/reads/deps | wave-planner, execute |
| **test-plan** (per task) | task description, triage output | test_file, test_command, assertions | execute (red/green gates) |
| **wave-planner** | all plans, dependency graph | waves[] (ordered groups of tasks) | execute |
| **execute** (per wave) | plan, test-plan, worktree | commits[], test_passed, gate_results | review |
| **review** | combined diff, all phase outputs | verdict, score, findings[] | PR body, next-run context |

---

## 4. SQLite Schema

One `.db` file per run. Filename: `<repo>-<issue>-<YYYYMMDD-HHMM>.db`
Location: `runs/` directory (gitignored).
Self-contained: full replay of entire run from this file alone.

### Table: `run`

```sql
CREATE TABLE run (
    id                  TEXT PRIMARY KEY,    -- UUID
    repo                TEXT NOT NULL,       -- owner/repo
    issue_number        INTEGER NOT NULL,
    issue_title         TEXT,
    workflow            TEXT NOT NULL,       -- e.g. "issue-to-pr"
    model               TEXT NOT NULL,       -- default model for run
    git_branch          TEXT,
    git_base_commit     TEXT,               -- commit SHA at start
    started_at          TEXT NOT NULL,       -- ISO-8601
    finished_at         TEXT,
    outcome             TEXT,               -- success | failure | partial | budget_exceeded
    total_cost_usd      REAL DEFAULT 0,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_duration_ms   INTEGER,
    budget_usd          REAL,
    pr_number           INTEGER,
    pr_url              TEXT,
    prior_run_id        TEXT,               -- if this is a retry, link to previous run
    error_summary       TEXT,
    config              JSON                -- workflow.yaml snapshot + runtime overrides
);
```

### Table: `phase`

```sql
CREATE TABLE phase (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES run(id),
    name                    TEXT NOT NULL,       -- triage | plan | test-plan | wave-planner | execute | review
    label                   TEXT NOT NULL,       -- e.g. "plan-task-2" for per-task phases
    seq                     INTEGER NOT NULL,    -- execution order
    model                   TEXT NOT NULL,
    system_prompt_hash      TEXT,               -- SHA-256 of rendered prompt (detect drift)
    started_at              TEXT,
    finished_at             TEXT,
    duration_ms             INTEGER,
    outcome                 TEXT,               -- success | failure | skipped | gate_failed
    turn_count              INTEGER DEFAULT 0,
    cost_usd                REAL DEFAULT 0,
    input_tokens            INTEGER DEFAULT 0,
    output_tokens           INTEGER DEFAULT 0,
    cache_read_tokens       INTEGER DEFAULT 0,
    cache_creation_tokens   INTEGER DEFAULT 0,
    error_message           TEXT,
    gate_errors             JSON               -- list of gate failure strings, if any
);
```

### Table: `message`

```sql
CREATE TABLE message (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_id                INTEGER NOT NULL REFERENCES phase(id),
    seq                     INTEGER NOT NULL,    -- order within phase
    role                    TEXT NOT NULL,       -- user | assistant | system
    content                 TEXT NOT NULL,       -- full text (prose response or rendered prompt)
    content_blocks          JSON,               -- raw API content blocks (tool_use, thinking, etc.)
    input_tokens            INTEGER,
    output_tokens           INTEGER,
    cache_read_tokens       INTEGER,
    cache_creation_tokens   INTEGER,
    cost_usd                REAL,
    model                   TEXT,
    finish_reason           TEXT,               -- stop | max_tokens | tool_use
    duration_ms             INTEGER,
    created_at              TEXT NOT NULL
);
```

### Table: `tool_call`

```sql
CREATE TABLE tool_call (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      INTEGER NOT NULL REFERENCES message(id),
    phase_id        INTEGER NOT NULL REFERENCES phase(id),
    tool_name       TEXT NOT NULL,           -- Read, Write, Edit, Bash, etc.
    input_preview   TEXT,                    -- first 500 chars (for browsing without loading full JSON)
    input           JSON,                    -- full arguments
    output_preview  TEXT,                    -- first 500 chars
    output          JSON,                    -- full result
    duration_ms     INTEGER,
    success         INTEGER NOT NULL,        -- 0 or 1
    error           TEXT
);
```

### Table: `event`

```sql
CREATE TABLE event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES run(id),
    phase_id    INTEGER REFERENCES phase(id),   -- NULL for run-level events
    type        TEXT NOT NULL,                   -- rate_limit | retry | budget_check | gate_pass | gate_fail | error | checkpoint
    timestamp   TEXT NOT NULL,
    details     JSON
);
```

### Indexes

```sql
CREATE INDEX idx_phase_run ON phase(run_id);
CREATE INDEX idx_phase_name ON phase(name);
CREATE INDEX idx_message_phase ON message(phase_id, seq);
CREATE INDEX idx_tool_call_phase ON tool_call(phase_id);
CREATE INDEX idx_tool_call_name ON tool_call(tool_name);
CREATE INDEX idx_event_type ON event(type);
CREATE INDEX idx_event_run ON event(run_id);
```

### Pragmas (set on connection open)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA page_size = 8192;
```

---

## 5. Workflow Definition Format

Workflows live in `workflows/<name>/` directories. A workflow is a directory containing:

```
workflows/issue-to-pr/
  workflow.yaml           # Phase definitions, model assignments, gates, budget
  prompts/
    triage.md             # Prompt template for triage phase
    plan.md               # Prompt template for plan phase
    test-plan.md          # ...
    wave-planner.md
    execute.md
    review.md
```

### workflow.yaml schema

```yaml
name: issue-to-pr
description: Transform a GitHub issue into a tested, reviewed PR

budget:
  max_per_run_usd: 1.00

max_parallel_workers: 5

phases:
  - name: triage
    model: sonnet
    max_turns: 10
    gates: [file_existence_check, task_count_max_5]
  - name: plan
    model: sonnet
    max_turns: 10
    parallel: per_task
    gates: [dag_acyclic, file_path_plausible]
  - name: test-plan
    model: sonnet
    max_turns: 10
    parallel: per_task
    gates: [test_file_specified, test_command_specified]
  - name: wave-planner
    model: sonnet
    max_turns: 5
    gates: [all_tasks_assigned, no_duplicate_tasks, wave_size_within_limit]
  - name: execute
    model: sonnet
    max_turns: 30
    parallel: per_wave
    gates: [red_gate, green_gate, commit_exists]
  - name: review
    model: haiku
    max_turns: 5
```

The orchestrator reads `workflow.yaml` and uses it as the authoritative source for phase sequencing, model selection, max turns, parallelism strategy, and gate declarations. No hardcoded phase logic in orchestrator code.

---

## 6. Prompt Management

### Principles

- Prompts are prose-first. No JSON schema demands inside agent prompts. Agents respond naturally.
- Extraction of structured data happens in a separate, cheap call (sonnet/haiku) AFTER the agent's prose response. The orchestrator needs structure; the agent does not.
- Prompts are the most valuable IP. They are portable across backends.

### Versioning

- Prompts live in `workflows/*/prompts/*.md` and are version-controlled via git.
- Every phase log stores `system_prompt_hash` (SHA-256 of the rendered prompt after context injection).
- `git log --follow workflows/issue-to-pr/prompts/triage.md` shows prompt evolution.
- Prompt hash in the DB links any run to the exact prompt version that produced it.

### Template Placeholders

```
{issue_number}     — The GitHub issue number
{issue_body}       — Full issue body text
{prior_phases}     — JSON of all prior phase outputs
{repo_context}     — Contents of target repo's .workflows/context.md
{prior_review}     — Review findings from prior run on same issue (if any)
```

### Context Injection Order

1. Target repo's `.workflows/context.md` (domain glossary, conventions)
2. Target repo's `.workflows/knowledge/*.md` (architecture, pitfalls) — progressive disclosure with token cap
3. Prior run review findings (if this is a retry)
4. Rendered prompt template with placeholders filled

### Extraction Pattern

```
Agent (multi-turn, prose-first, full tool access)
    │
    ▼ prose response
Extraction call (single-turn, sonnet, cheap)
    │
    ▼ structured JSON matching Pydantic schema
Orchestrator continues
```

This separation means agents never see JSON schema constraints. They think freely. Structure is imposed after the fact, where it's cheap to retry if parsing fails.

---

## 7. The Improvement Cycle

The eval module implements a closed-loop improvement process:

```
Run completes
    │
    ▼
Judge (eval.py)
    ├── Score: 0-10 on correctness, completeness, efficiency
    ├── Categorize failure mode (if any):
    │     prompt_unclear | context_missing | model_limitation |
    │     gate_too_strict | gate_too_loose | tool_failure | budget_exceeded
    ├── Identify which phase failed and why
    └── Propose specific prompt edit (diff) if prompt-related
         │
         ▼
    Human reviews proposed edit
         │
         ▼
    Prompt updated in git (new commit = new version)
         │
         ▼
    Next run uses new prompt
         │
         ▼
    Compare: did the score improve for this failure category?
```

### What gets evaluated

| Signal | Source | Question |
|--------|--------|----------|
| Gate pass/fail | gates.py | Did the agent produce valid output? |
| Review score | review phase | How good was the implementation? |
| Cost efficiency | storage | Cost per successful PR? Per phase? |
| Turn count | storage | Is the agent spinning? (high turns = confused) |
| Tool call patterns | tool_call table | Excessive reads? Failed edits? |
| Prior run delta | compare .db files | Did the same issue improve between runs? |

### Cross-run analysis queries

```sql
-- Prompt versions that consistently fail
SELECT system_prompt_hash, COUNT(*) as runs, AVG(cost_usd) as avg_cost,
       SUM(CASE WHEN outcome = 'failure' THEN 1 ELSE 0 END) as failures
FROM phase GROUP BY system_prompt_hash;

-- Most expensive tool calls
SELECT tool_name, AVG(duration_ms), COUNT(*) FROM tool_call
GROUP BY tool_name ORDER BY AVG(duration_ms) DESC;

-- Phase failure rates
SELECT name, COUNT(*) as total,
       SUM(CASE WHEN outcome = 'failure' THEN 1 ELSE 0 END) as failures
FROM phase GROUP BY name;
```

---

## 8. GitHub Integration

### What gets posted and when

| Event | Action | Where |
|-------|--------|-------|
| Run starts | Comment: "Pipeline run started (run_id)" | Issue |
| Triage produces `escalate: true` | Comment: "This issue needs human clarification" + questions | Issue |
| Run succeeds | PR created with review findings in body | PR |
| Run fails | Comment: "Pipeline failed at {phase}: {error}" | Issue |
| Review has findings | Findings listed in PR description | PR |
| Retry run starts | Comment: "Retry run started, addressing: {prior_review_findings}" | Issue |

### How the next run reads prior context

1. Orchestrator calls `storage.find_prior_runs(repo, issue_number)` to find existing .db files for this issue.
2. From the most recent prior run, extracts: review findings, failure category, gate errors.
3. This context is injected into the triage prompt via `{prior_review}` placeholder.
4. The triage agent sees what went wrong before and can adjust its approach.

### Label State Machine

```
needs-triage  →  ready-for-agent  →  in-progress  →  in-review  →  done
                       ↓                    ↓
                  needs-info           failed (with comment explaining why)
```

Labels are the human-visible coordination mechanism. The pipeline reads `ready-for-agent` as the trigger signal and updates labels as it progresses.

---

## 9. Retry/Continuation Model

### Same issue, new run, prior context injected

Each run for the same issue produces a new .db file:
```
runs/myrepo-42-20260614-1430.db    ← first attempt (failed at review)
runs/myrepo-42-20260614-1505.db    ← retry (reads prior review findings)
runs/myrepo-42-20260615-0900.db    ← second retry (reads both prior runs)
```

### What gets injected from prior runs

- Review findings (severity, category, description, suggestion)
- Failure phase and error message
- Gate errors that blocked progress
- Cost of prior attempt (budget awareness)

### Continuation vs fresh start

The pipeline always starts fresh (new worktree, new branch). It does NOT resume from a checkpoint. Prior context informs the new attempt but does not constrain it. The agent may take a completely different approach based on what failed before.

---

## 10. Patterns Stolen and Where They Fit

| Pattern | Source | Where it fits |
|---------|--------|---------------|
| Prose-first prompts, no JSON demands | mattpocock/skills | All prompt templates |
| CONTEXT.md domain glossary | mattpocock/skills | Target repo's `.workflows/context.md` injected into every phase |
| Label state machine | mattpocock/skills | GitHub integration (section 8) |
| Agent briefs (behavioral, not procedural) | mattpocock/skills | Triage output format — describes what, not how |
| Progressive disclosure via file links | mattpocock/skills | Knowledge files loaded on demand with token cap |
| Circuit breakers for batch processing | claude-pipeline | Budget checks between phases, max retry limits |
| Self-improving feedback loops | metaswarm | The improvement cycle (section 7) |
| Specialist agent lifecycle | metaswarm | Each phase is a specialist (triage, plan, execute, review) |
| Git worktree isolation | ccswarm | `engines/github_claude/workspace.py` — each run gets an isolated worktree |
| NDJSON/SQLite audit trails | ccswarm | Per-run .db file as complete audit trail |
| Tool abstraction layer | SWE-agent | `engines/github_claude/runtime.py` — isolated runtime, copy folder for new backend |
| Eval harness, LLM-as-judge | promptfoo | `engines/github_claude/eval.py` — judge runs, compare prompt versions |
| Git-native edit format | aider | Execute phase agents use search-replace tool |
| Session handoff protocol | pro-workflow | Prior run context injection (section 9) |
| Red/green test gates | metaswarm | `gates.py` — tests must fail before implementation, pass after |

### Future engines (copy the folder, swap what's different)

| Engine | Source | Runtime | Destination |
|--------|--------|---------|-------------|
| `github_claude` (current) | GitHub Issues via `gh` | Claude CLI (subscription) | GitHub PR |
| `gitlab-claude` (future) | GitLab issues | Claude CLI | GitLab MR |
| `github-openai` (future) | GitHub Issues | OpenAI Codex CLI | GitHub PR |
| `local-claude` (future) | Markdown file | Claude CLI | Local branch + patch |

Want a new engine? `cp -r engines/github_claude engines/my-new-engine`. Change the files that are different. Done.

---

## 11. Implementation Priorities

### Phase 1: Restructure into engines/github_claude/

1. **Create folder structure** — `engines/github_claude/` with all module files.
2. **Extract modules** — Split orchestrator.py God module into `source.py`, `runtime.py`, `storage.py`, `workspace.py`, `destination.py`.
3. **Per-run .db files** — Implement filename convention `<repo>-<issue>-<YYYYMMDD-HHMM>.db` in `engines/github_claude/runs/`.
4. **New 5-table schema** — Replace current 3-table schema with the schema in section 4.
5. **Thin orchestrator** — Rewrite orchestrator.py as sequencer that imports from the other modules. Reads workflow.yaml for phase config.

### Phase 2: Observability

6. **Message-level logging** — Log every message in multi-turn conversations, not just phase-level aggregates.
7. **Tool call extraction** — Populate tool_call table from agent responses.
8. **Prior run discovery** — `find_prior_runs()` implementation, inject prior review into triage.

### Phase 3: Evaluation

9. **eval.py** — Judge module that scores completed runs and categorizes failures.
10. **Cross-run comparison** — Dashboard queries comparing prompt versions.
11. **Prompt edit proposals** — eval suggests specific diffs to prompt templates.

### Phase 4: GitHub Integration

12. **Issue comments** — Post status updates as the run progresses.
13. **Label management** — Update labels through the state machine.
14. **Label-triggered runs** — (Optional) GitHub Action that triggers pipeline on `ready-for-agent` label.

---

## Non-Goals

- No web UI. SQLite queries and CLI are sufficient.
- No multi-repo orchestration in a single run. One issue, one repo, one PR.
- No automatic prompt modification. Eval proposes; human approves.
- No cloud deployment. This runs on a developer machine.
- No framework dependencies (langchain, langgraph, etc.). Plain Python + subprocess.
