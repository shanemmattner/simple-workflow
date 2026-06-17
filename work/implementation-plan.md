# Implementation Plan: Restructure to engines/github_claude/

Restructure the codebase from a flat `engine/` directory with abstract interfaces into the `engines/github_claude/` self-contained folder approach.

---

## Current State

```
engine/
  orchestrator.py    ← 710-line God module (CLI entry, phase dispatch, context assembly)
  agent.py           ← Claude CLI wrapper (subprocess calls, JSON parsing, token tracking)
  gates.py           ← validation gates, post-phase checks
  worktree.py        ← git worktree lifecycle
  waves.py           ← wave planner execution, parallel dispatch
db.py                ← SQLite schema + operations (shared runs.db)
schemas.py           ← Pydantic models for all phase I/O
workflows/issue-to-pr/
  workflow.yaml
  prompts/*.md
```

## Target State

```
workflows/issue-to-pr/     ← unchanged (prompts stay here)
  workflow.yaml
  prompts/

engines/github_claude/
  README.md
  source.py
  runtime.py
  storage.py
  workspace.py
  destination.py
  eval.py
  orchestrator.py
  agents/
  tools/
  runs/                    ← gitignored
```

---

## Wave 1: Extract Modules (all tasks run in parallel)

Each task is independent — they read from the old code and write new files. No task depends on another task in this wave.

---

### Task 1.1: Create folder structure

**What to do:**
- Create `engines/github_claude/` and subdirectories: `agents/`, `tools/`, `runs/`
- Add `engines/github_claude/runs/` to `.gitignore`
- Create empty `engines/github_claude/__init__.py`

**Files to read:**
- `.gitignore` (to understand current ignore patterns)

**Files to create:**
- `engines/github_claude/__init__.py`
- `engines/github_claude/agents/.gitkeep`
- `engines/github_claude/tools/.gitkeep`

**Acceptance criteria:**
- Directory structure exists
- `runs/` inside the engine folder is gitignored
- Python package is importable

---

### Task 1.2: Extract source.py

**What to do:**
- Extract GitHub issue reading logic from `engine/orchestrator.py` into `engines/github_claude/source.py`
- This includes: fetching issue body via `gh`, fetching comments, parsing metadata (labels, assignee), posting status comments back to the issue
- Look for `gh issue view` and `gh issue comment` subprocess calls in orchestrator.py

**Files to read:**
- `engine/orchestrator.py` — find all GitHub issue fetch/post logic
- `schemas.py` — find `WorkRequest` or equivalent model

**Files to create:**
- `engines/github_claude/source.py`

**Functions to expose:**
- `fetch_issue(repo: str, issue_number: int) -> dict` — returns issue body, title, comments, labels, metadata
- `post_comment(repo: str, issue_number: int, body: str) -> None` — posts status comment
- `update_labels(repo: str, issue_number: int, add: list[str], remove: list[str]) -> None`

**Acceptance criteria:**
- All `gh issue` subprocess calls live in source.py, nowhere else
- Function can be called standalone: `python -c "from engines.github_claude.source import fetch_issue"`
- No imports from old `engine/` package
- No abstract base classes or Protocol types

---

### Task 1.3: Extract runtime.py

**What to do:**
- Extract Claude CLI invocation logic from `engine/agent.py` into `engines/github_claude/runtime.py`
- This includes: building the `claude` subprocess command, parsing JSON output, extracting tokens/cost/timing, handling multi-turn conversations, timeout handling

**Files to read:**
- `engine/agent.py` — the entire file, this is the primary source
- `engine/orchestrator.py` — how it calls agent.py (what parameters it passes)
- `workflows/issue-to-pr/workflow.yaml` — model names, max_turns values

**Files to create:**
- `engines/github_claude/runtime.py`

**Functions to expose:**
- `call_claude(prompt: str, *, model: str, cwd: str, max_turns: int, timeout: int | None = None) -> dict` — returns parsed response with content, tokens, cost, timing, finish_reason
- `parse_claude_response(raw_json: str) -> dict` — extracts structured data from Claude CLI output

**Acceptance criteria:**
- All `subprocess.run(["claude", ...])` logic lives here
- Returns a plain dict (not a Pydantic model) — keep it simple
- Can be tested in isolation: call with a prompt, get back structured response
- Handles Claude CLI errors gracefully (non-zero exit, timeout, malformed JSON)
- No abstract base classes or Protocol types

---

### Task 1.4: Extract storage.py

**What to do:**
- Extract SQLite logic from `db.py` into `engines/github_claude/storage.py`
- Implement per-run .db files (one SQLite database per run, not shared)
- Implement the 5-table schema from PRD section 4: run, phase, message, tool_call, event
- Filename convention: `<repo>-<issue>-<YYYYMMDD-HHMM>.db` in `engines/github_claude/runs/`

**Files to read:**
- `db.py` — current SQLite implementation (3-table schema)
- `PRD.md` section 4 — target 5-table schema with all columns, indexes, pragmas

**Files to create:**
- `engines/github_claude/storage.py`

**Functions to expose:**
- `create_run_db(repo: str, issue_number: int) -> tuple[str, sqlite3.Connection]` — creates .db file with schema, returns path and connection
- `log_phase(conn, run_id: str, phase_data: dict) -> int` — inserts phase record, returns phase_id
- `log_message(conn, phase_id: int, message_data: dict) -> int` — inserts message record
- `log_tool_call(conn, message_id: int, phase_id: int, tool_data: dict) -> None`
- `log_event(conn, run_id: str, event_type: str, details: dict, phase_id: int | None = None) -> None`
- `finish_run(conn, run_id: str, outcome: str, totals: dict) -> None`
- `find_prior_runs(repo: str, issue_number: int) -> list[dict]` — scans runs/ directory for matching .db files

**Acceptance criteria:**
- Creates per-run .db files in `engines/github_claude/runs/`
- All 5 tables created with correct schema (match PRD exactly)
- Pragmas set: WAL mode, foreign keys ON, page_size 8192
- Indexes created
- `find_prior_runs` scans the runs/ directory and reads summary data from matching .db files
- No shared state between runs
- No abstract base classes or Protocol types

---

### Task 1.5: Extract workspace.py

**What to do:**
- Extract git worktree logic from `engine/worktree.py` into `engines/github_claude/workspace.py`
- Provides isolated working directory for each run via git worktree

**Files to read:**
- `engine/worktree.py` — the entire file, this is the primary source
- `engine/orchestrator.py` — how it creates/uses/cleans up worktrees

**Files to create:**
- `engines/github_claude/workspace.py`

**Functions to expose:**
- `create_worktree(repo_path: str, branch: str, base: str = "main") -> str` — creates worktree, returns path
- `get_diff(worktree_path: str, base: str = "main") -> str` — returns combined diff of all changes
- `cleanup_worktree(worktree_path: str) -> None` — removes worktree and branch

**Acceptance criteria:**
- All `git worktree` subprocess calls live here
- Creates branches with a consistent naming convention
- Cleanup removes both worktree directory and the branch
- Handles edge cases: worktree already exists, branch already exists, repo not found
- No abstract base classes or Protocol types

---

### Task 1.6: Extract destination.py

**What to do:**
- Extract GitHub PR creation logic from `engine/orchestrator.py` into `engines/github_claude/destination.py`
- This includes: pushing the branch, creating the PR via `gh pr create`, formatting PR body with review findings

**Files to read:**
- `engine/orchestrator.py` — find all `gh pr` subprocess calls and PR body formatting logic
- `schemas.py` — find review output schema (used in PR body)

**Files to create:**
- `engines/github_claude/destination.py`

**Functions to expose:**
- `push_branch(worktree_path: str, branch: str) -> None` — pushes branch to origin
- `create_pr(repo: str, branch: str, title: str, body: str, base: str = "main") -> dict` — creates PR, returns {number, url}
- `format_pr_body(review_findings: list[dict], run_metadata: dict) -> str` — formats the PR description

**Acceptance criteria:**
- All `gh pr` subprocess calls live here
- PR body includes review findings, run metadata, link to issue
- Returns PR number and URL after creation
- Handles errors: push fails, PR already exists for branch
- No abstract base classes or Protocol types

---

### Task 1.7: Create eval.py skeleton

**What to do:**
- Create a skeleton `engines/github_claude/eval.py` with function signatures and docstrings
- This module is not fully implemented yet — just the structure and TODO comments

**Files to read:**
- `PRD.md` section 7 — The Improvement Cycle (defines what eval does)

**Files to create:**
- `engines/github_claude/eval.py`

**Functions to expose:**
- `judge_run(db_path: str) -> dict` — scores a completed run (TODO: implement LLM-as-judge)
- `categorize_failure(db_path: str) -> str` — returns failure category (TODO: implement)
- `find_patterns(db_paths: list[str]) -> list[dict]` — finds recurring failure patterns across runs (TODO)
- `propose_prompt_edit(pattern: dict) -> dict | None` — suggests a prompt diff (TODO)

**Acceptance criteria:**
- File exists with correct function signatures
- Each function has a docstring explaining what it should do
- Each function body is `raise NotImplementedError("TODO: implement")` or returns a placeholder
- No abstract base classes or Protocol types

---

## Wave 2: Wire Together (depends on Wave 1)

All Wave 1 modules must exist before these tasks start. Wave 2 tasks can run in parallel with each other.

---

### Task 2.1: Rewrite orchestrator.py as thin sequencer

**What to do:**
- Create `engines/github_claude/orchestrator.py` as a thin sequencer that imports from all Wave 1 modules
- Reads `workflows/issue-to-pr/workflow.yaml` for phase definitions
- Sequences phases in order, calls runtime for each, logs to storage, checks gates between phases
- This replaces the 710-line God module with a ~150-200 line coordinator

**Files to read:**
- `engine/orchestrator.py` — understand the current flow (what phases run in what order, how context is assembled between phases)
- `engine/gates.py` — understand gate checking logic (may inline simple gates or import gates.py)
- `engine/waves.py` — understand parallel dispatch (may simplify or keep)
- `workflows/issue-to-pr/workflow.yaml` — the phase config it reads
- All Wave 1 output files: `engines/github_claude/source.py`, `runtime.py`, `storage.py`, `workspace.py`, `destination.py`

**Files to create:**
- `engines/github_claude/orchestrator.py`

**Key responsibilities:**
1. Parse CLI args (repo, issue number, budget, model override)
2. Call `source.fetch_issue()` to get work request
3. Call `storage.create_run_db()` to create per-run database
4. Call `workspace.create_worktree()` to get isolated directory
5. For each phase in workflow.yaml:
   - Render prompt template (fill placeholders with context)
   - Call `runtime.call_claude()` with rendered prompt
   - Log phase/messages/tool_calls to storage
   - Run gate checks (can inline or import from a gates module)
   - Pass output to next phase as context
6. On success: call `destination.create_pr()`
7. On failure: call `source.post_comment()` with error
8. Always: call `workspace.cleanup_worktree()`, finalize storage

**Acceptance criteria:**
- Under 250 lines
- Reads workflow.yaml for phase config (not hardcoded)
- Imports from the other modules in the same folder
- Has a `main()` function that can be called from CLI
- Error handling: catches exceptions, logs to storage, posts failure comment, cleans up worktree
- No abstract base classes, no Plugin system, no dynamic loading

---

### Task 2.2: Write engines/github_claude/README.md

**What to do:**
- Write a README explaining what's in the engine folder and how it works
- Include: what each file does, how to run it, dependencies, configuration

**Files to read:**
- All files in `engines/github_claude/` (from Wave 1 + Task 2.1)
- `workflows/issue-to-pr/workflow.yaml`
- `PRD.md` section 2 (architecture overview)

**Files to create:**
- `engines/github_claude/README.md`

**Content should cover:**
- One-paragraph description of this engine
- File-by-file listing with one-line descriptions
- How to run: CLI command, required env vars, prerequisites (gh auth, claude auth)
- Where output goes (runs/ directory)
- How to add a new workflow
- How to fork this into a new engine

**Acceptance criteria:**
- An engineer unfamiliar with the project can read this README and understand the engine in 5 minutes
- No references to abstract interfaces or Protocol classes
- Includes concrete examples (actual CLI commands)

---

### Task 2.3: Migrate gates logic

**What to do:**
- Decide where gates live. Options:
  - Inline simple gate checks in orchestrator.py (preferred if gates are simple validation)
  - Create `engines/github_claude/gates.py` if gate logic is substantial
- Port gate logic from `engine/gates.py`

**Files to read:**
- `engine/gates.py` — full file, understand complexity
- `engine/orchestrator.py` — how gates are called
- `workflows/issue-to-pr/workflow.yaml` — gate declarations per phase

**Files to create:**
- `engines/github_claude/gates.py` (if needed) OR inline in orchestrator.py

**Acceptance criteria:**
- All gates from workflow.yaml are implemented
- Gates receive phase output and return pass/fail with error messages
- Gate failures are logged to storage (event table, type='gate_fail')
- No abstract base classes or Protocol types

---

### Task 2.4: Create CLI entry point

**What to do:**
- Create a script or entry point to run the engine from command line
- Update `scripts/run.sh` to point at the new location

**Files to read:**
- `scripts/run.sh` — current entry point
- `engines/github_claude/orchestrator.py` — the main() function to call

**Files to create/modify:**
- `scripts/run.sh` (modify to call new location)
- Optionally: `engines/github_claude/__main__.py` for `python -m engines.github_claude`

**Acceptance criteria:**
- `./scripts/run.sh owner/repo#123` still works
- `python -m engines.github_claude owner/repo#123` works
- Budget and model CLI overrides still work

---

## Wave 3: Validate and Clean Up (depends on Wave 2)

---

### Task 3.1: End-to-end test run

**What to do:**
- Run the restructured engine against a real issue (or a test issue in a sandbox repo)
- Verify the full flow: fetch issue -> create worktree -> run phases -> create PR
- Fix any import errors, missing context, broken subprocess calls

**Files to read:**
- All files in `engines/github_claude/`
- `workflows/issue-to-pr/` (prompts and config)
- Run output .db file (to verify logging works)

**Acceptance criteria:**
- Pipeline runs end-to-end without crashing
- Per-run .db file is created with correct schema and populated data
- PR is created (or would be created in dry-run mode)
- Worktree is cleaned up after run completes
- All 5 tables have data in the .db file

---

### Task 3.2: Remove old engine/ directory

**What to do:**
- Delete the old `engine/` directory
- Delete `db.py` and `schemas.py` from root (if schemas are inlined or moved)
- Update any remaining references in scripts, tests, CLAUDE.md

**Files to read:**
- All files in `engine/` (confirm nothing is missed)
- `CLAUDE.md` — update Key Files section
- `tests/` — update imports
- `scripts/` — update any references

**Files to delete:**
- `engine/orchestrator.py`
- `engine/agent.py`
- `engine/gates.py`
- `engine/worktree.py`
- `engine/waves.py`
- `engine/__init__.py`
- `db.py`

**Acceptance criteria:**
- Old `engine/` directory is gone
- No remaining imports from `engine.*` or `db` anywhere in the codebase
- Tests still pass (or are updated to test new location)
- CLAUDE.md reflects new file locations

---

### Task 3.3: Update CLAUDE.md and top-level docs

**What to do:**
- Rewrite CLAUDE.md to reflect the new architecture
- Update Key Files to point at `engines/github_claude/` modules
- Update Run commands if they changed
- Remove references to old file locations

**Files to read:**
- `CLAUDE.md` — current content
- `engines/github_claude/README.md` — align with engine docs
- `PRD.md` — align with architecture section

**Files to create/modify:**
- `CLAUDE.md` (rewrite)

**Acceptance criteria:**
- Key Files section lists all `engines/github_claude/*.py` files
- Run instructions work
- No references to old `engine/` or root-level `db.py`
- A new contributor can read CLAUDE.md and navigate the codebase

---

## Dependency Graph

```
Wave 1 (all parallel):
  1.1 folder structure
  1.2 source.py
  1.3 runtime.py
  1.4 storage.py
  1.5 workspace.py
  1.6 destination.py
  1.7 eval.py skeleton
       │
       ▼
Wave 2 (all parallel, after Wave 1 completes):
  2.1 orchestrator.py (thin sequencer)
  2.2 README.md
  2.3 gates logic
  2.4 CLI entry point
       │
       ▼
Wave 3 (sequential):
  3.1 end-to-end test run
  3.2 remove old engine/ directory  ← after 3.1 confirms it works
  3.3 update CLAUDE.md              ← after 3.2 removes old files
```

## Notes

- **schemas.py**: The Pydantic models in `schemas.py` can either be inlined into the modules that use them, or kept as a shared file at root level (since workflows reference them). Decision: keep `schemas.py` at root for now — it defines workflow-level contracts that any engine would use. Move it later if it causes problems.
- **waves.py**: The parallel dispatch logic may be absorbed into orchestrator.py or kept as a helper. Depends on complexity — read `engine/waves.py` to decide.
- **Testing**: Existing tests in `tests/` will break during the transition. Update imports as part of Wave 3. Don't block Wave 1-2 on test updates.
