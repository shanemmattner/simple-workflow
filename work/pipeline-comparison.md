# Pipeline Comparison: simple-workflow vs shftty

Date: 2026-06-16

## 1. Phase Structure (Side by Side)

| # | simple-workflow | shftty |
|---|----------------|--------|
| 1 | **Triage** -- decompose issue into 1-5 tasks (JSON-only output, no tools) | **Triage** -- classify into 1-5 tasks or escalate (prose allowed, haiku, neutral temp dir) |
| 2 | **Plan** -- build steps per task (parallel fan-out, no tool access) | **Plan** -- extract claims, verify against codebase with tools, size-gate, build plan (all in one session with full codebase access) |
| 3 | **Test Plan** -- design a failing test spec (parallel with Plan, no tool access) | **Test** -- write AND run red tests (agent has tool access, commits test files, confirms red) |
| 4 | **Wave Planner** -- LLM schedules tasks into waves | *(deterministic `_build_waves()` in Python -- not a phase)* |
| 5 | **Execute** -- implement per-wave | **Implement** -- per-wave, sequential waves with per-wave gates and push checkpoints |
| 6 | **Review** -- check combined diff | **Review** -- fresh session, no prior context, runs verify-affected gate |

Key structural differences:
- simple-workflow uses an **LLM call to schedule waves** (wave-planner phase). shftty replaces this with 100 lines of deterministic Python (topological sort + write-set collision detection). The LLM wave planner is an unnecessary cost and hallucination risk.
- simple-workflow fans out plan+test-plan **per task in parallel**, then fans in for wave planning. shftty runs phases sequentially on a single task (first task from triage).
- shftty **pushes after every passing wave** (durable checkpoint). simple-workflow only pushes once at the end.
- simple-workflow has **6 phases** (one being the LLM wave planner). shftty has **5 phases** but gets more done per phase because agents have tool access.

---

## 2. How Agents Are Called

### simple-workflow (`engine/agent.py`)

```python
cmd = ["claude", prompt, "--output-format", "json", "--model", model_name, "--dangerously-skip-permissions"]
```

- Passes the **full prompt as a CLI positional argument** -- will break on large prompts (shell arg limits)
- Uses `--dangerously-skip-permissions` -- no `--permission-mode`, no `--max-turns`
- `cwd` = worktree_path for ALL phases (leaks CLAUDE.md context into triage/plan)
- Single-turn only -- `num_turns` always returns 1, even for the execute phase
- Uses `subprocess.run()` with a flat timeout

### shftty (`workflows/src/agent.py`)

```python
prompt_file = Path(worktree) / f".pipeline-{phase}-prompt.md"
prompt_file.write_text(prompt, encoding="utf-8")
cmd = [
    "claude",
    f"Read {prompt_file} and execute every instruction in it.",
    "--output-format", "json",
    "--model", model,
    "--max-turns", str(max_turns),
    "--permission-mode", "auto",
]
```

- Writes prompt to a **temp file**, passes a short "Read and execute" instruction (avoids arg limits)
- Uses `--permission-mode auto` -- agents can read files, run commands, commit
- Explicit `--max-turns` configurable per phase (10 for triage, 30 for plan/test/review, 60 for implement)
- Triage runs in `tempfile.gettempdir()` -- no CLAUDE.md pollution
- Uses `Popen` with `communicate(timeout=...)` and `proc.kill()` on timeout
- Cleans up prompt file in `finally` block

---

## 3. Output Handling

### simple-workflow

- Prompts demand: "Output JSON only. No prose, no markdown fences."
- `_parse_json()` tries: whole-string parse, brace-depth scan for first `{...}`, markdown fence extraction
- **On parse failure: returns `None`, caller gets `{}`, `ValidationKill` is raised**
- No fallback extraction step -- if the model emits prose + JSON mixed and brace scanning fails, the run dies

### shftty

- Prompts say: "Write your analysis in natural language. Be thorough and specific." -- allows prose output
- `parse_json()` uses the same brace-depth scanner
- **On parse failure: calls `extract_structured()`** -- a second cheap haiku call that takes raw prose + schema description and extracts JSON:

```python
# phases/triage.py
triage_json = agent.parse_json(raw_text)
if not triage_json or "tasks" not in triage_json:
    triage_json = agent.extract_structured(raw_text, schema_description)
if not triage_json or "tasks" not in triage_json:
    triage_json = _fallback_task(issue_number, issue_body)
```

Three levels of degradation: direct parse -> haiku extraction -> deterministic fallback. The pipeline never crashes on unparseable output.

---

## 4. Validation

### simple-workflow

- **Pydantic models in `schemas.py`** for every phase output: `TriageOutput`, `PlanOutput`, `TestPlanOutput`, `WavePlannerOutput`, `ExecuteOutput`, `ReviewOutput`
- Validation via `model_validate()` -- on failure, raises `ValidationKill` which halts the entire pipeline
- Additional semantic gates in `engine/gates.py`:
  - Triage: file existence check (50% threshold with fuzzy matching)
  - Plan: DAG cycle detection via Kahn's algorithm
  - Test plan: non-empty test_file and test_command
  - Wave planner: all tasks assigned, no duplicates, max_parallel respected
  - Red/green gates: test command allowlist, subprocess execution

**Verdict: KILL on any validation error. No retry, no degrade, no fallback.** A missing optional field like `escalate_reason` in triage output kills a run that may have already spent $0.50 on prior phases.

### shftty

- **No Pydantic.** All validation is dict access with `.get()` defaults
- Phase runners check for required keys ("tasks" in triage, "steps" in plan) and fallback gracefully
- Gates are subprocess-based (`pnpm typecheck && pnpm lint`, `bash scripts/verify-affected.sh`) -- they check the CODE, not the LLM output shape
- Implement phase **continues on gate failure**: `"[WARN] Gate failed after wave N -- continuing to next wave"`
- Triage has `_fallback_task()` that wraps the entire issue in one task if parsing fails

**Verdict: DEGRADE gracefully at every failure point. Never kills the run on a parse failure.**

---

## 5. What simple-workflow Does WORSE

### 5.1. Prompt passed as CLI argument (will explode)

```python
# engine/agent.py line 118
cmd = ["claude", prompt, "--output-format", "json", "--model", model_name, "--dangerously-skip-permissions"]
```

The `prompt` variable is the FULL rendered prompt -- issue body + repo context + knowledge files + prior phases JSON. For a real issue with 5 tasks, multi-step plans, and repo context, this can easily exceed 100KB. macOS has a 256KB `ARG_MAX` but subprocess argument limits are lower in practice. This is a ticking time bomb.

### 5.2. Execute phase is single-turn -- the TDD loop is a fiction

The execute prompt says: "Write the test, run it, confirm red, implement, run again, confirm green, commit." But:
- No `--max-turns` flag means agent gets default (1?) turns
- `--dangerously-skip-permissions` is not `--permission-mode auto`
- `num_turns` is hardcoded to 1 in the result
- The agent cannot actually run tests, read files, or make commits

The execute phase generates text describing what it would do. It does not do it.

### 5.3. No recovery on parse failure

```python
# engine/orchestrator.py line 373
except ValidationError as exc:
    raise ValidationKill(phase_label, [str(exc)])
```

A single missing optional field kills the entire run and wastes all prior spending. No retry, no extraction fallback, no degraded mode. Every dollar spent on prior phases is thrown away.

### 5.4. LLM-based wave planning is fragile and expensive

The wave planner is an entire LLM call that does what 100 lines of Python can do deterministically. It can hallucinate task IDs, produce invalid schedules, or fail to parse -- and any of those kills the pipeline. shftty's `_build_waves()` is a pure function: topological sort + write-set collision detection + wave merging. Zero tokens, zero cost, zero hallucination risk, milliseconds instead of 30+ seconds.

### 5.5. Red gate runs AFTER the execute agent

```python
# engine/waves.py lines 63-100
agent_result = run_agent(prompt, ...)  # agent "writes tests + implements"
if test_command:
    red_ok, red_msg = gates.run_red_gate(test_command, worktree_path)
```

The red gate checks whether tests fail AFTER the agent has already run. But since the agent is single-turn and can't actually modify files, the gate is checking the pre-existing worktree state -- which has nothing to do with the agent's output.

In shftty, the test phase is a separate agent session that writes tests, runs them, and confirms red BEFORE the implement phase starts. The red gate is meaningful because the agent actually creates test files.

### 5.6. cwd pollution -- CLAUDE.md leaks into all phases

```python
# engine/agent.py line 129
cwd=worktree_path or None,
```

Every phase runs in the target repo's worktree. The target repo's CLAUDE.md gets loaded into context for EVERY phase -- including triage, which is supposed to be a pure text-in/text-out classification. The lessons file (`lessons.md`) explicitly documents this as a known failure mode: "Headless `claude` calls inherit CLAUDE.md from their cwd."

shftty's triage runs in `tempfile.gettempdir()` -- zero CLAUDE.md contamination.

### 5.7. No resume capability

If simple-workflow crashes at wave 3 of 5, you restart from scratch. shftty has `--resume RUN_DIR_NAME` that:
- Walks the resume chain (`resume.json` pointing to parent runs)
- Loads all completed phase response artifacts
- Skips already-completed phases
- Reuses the prior run's worktree

### 5.8. No knowledge routing

simple-workflow has a static `KNOWLEDGE_FILES` dict that delivers the same knowledge files to every run (with only a phase-based filter). shftty's `relevant_knowledge_docs()` maps the plan's file targets to specific knowledge docs -- database files get database.md, auth files get auth.md, frontend files get design-system-reference.md, test files get testing.md. Each agent gets precisely relevant context instead of a generic dump.

### 5.9. Plan phase has no tool access -- trusts triage blindly

simple-workflow's plan agent has NO tool access. It cannot verify that triage's file paths exist, cannot read the actual code, cannot check types or imports. It plans blind.

shftty's plan phase has full codebase access and runs 4 sub-phases in one session: extract claims, verify each claim with grep/read (recording evidence), assess size/testability, then build plan. Every file path in the plan is verified against reality.

### 5.10. No post-wave commit cleanup or push checkpoints

simple-workflow's `execute_waves()` does no commit cleanup after each wave. If an agent leaves uncommitted changes (common), they bleed into the next wave. No push checkpoints mean a crash loses all progress.

shftty's `_commit_wave()` runs `git add -A && git commit` after every wave. `implement.py` pushes with `--force-with-lease` after every passing gate. Work is never lost.

---

## 6. What to Steal from shftty

### 6.1. Two-step output extraction

**File:** `workflows/src/agent.py` lines 81-149

```python
def extract_structured(raw_text, schema_description, *, model="haiku", timeout=120):
    prompt = (
        "Extract structured data from the following text. "
        "Output ONLY valid JSON matching this schema, nothing else.\n\n"
        f"Schema:\n{schema_description}\n\n"
        f"Text:\n{raw_text}"
    )
    # runs haiku in tempdir, returns parsed dict or {}
```

Cost: ~$0.001. Saves: entire pipeline re-run ($0.50-$2.00).

### 6.2. Deterministic wave builder

**File:** `workflows/src/capabilities/waves.py` lines 53-151

Pure Python topological sort: builds dependency graph from `depends_on` + write-set overlap, runs Kahn's BFS, validates write-set exclusivity within waves, defers colliders to next wave, merges adjacent single-step waves (capped at 3 steps). Handles cycles gracefully (dumps remaining into last wave). Assigns gate labels per wave.

Replaces a 30-second LLM call with a 5ms function. Zero hallucination risk.

### 6.3. Triage fallback

**File:** `workflows/src/phases/triage.py` lines 25-48

```python
def _fallback_task(issue_number, issue_body):
    first_line = next((line.strip() for line in issue_body.splitlines() if line.strip()), f"Issue #{issue_number}")
    return {"atomic": True, "tasks": [{"id": 1, "title": first_line[:100], "scope": "...", "likely_files": [], "depends_on": []}], ...}
```

Never crash because triage output is unparseable. Wrap the issue in one task and let plan figure it out.

### 6.4. Prompt-to-file pattern

**File:** `workflows/src/agent.py` lines 192-229

Write prompt to file, pass short instruction as CLI arg, clean up in `finally`. Prevents shell arg explosion.

### 6.5. Neutral cwd for non-tool phases

**File:** `workflows/src/phases/triage.py` lines 92-99

```python
neutral_dir = tempfile.gettempdir()
agent_result = agent.run_phase_agent(neutral_dir, prompt, "triage", ...)
```

Prevents target repo CLAUDE.md from contaminating classification-only phases.

### 6.6. Resume with chain-following

**File:** `workflows/src/orchestrator.py` lines 116-226

Walks a linked list of `resume.json` files through prior runs. Each ancestor's `responses/` directory is scanned for completed phase artifacts. Phases are skipped if their response exists. Worktree is reused.

### 6.7. Knowledge routing by file path

**File:** `workflows/src/context.py` lines 126-183

Maps plan file targets to relevant knowledge docs. DB files get database.md, auth files get auth.md, test files get testing.md + ui-testing-standards.md. Keeps context tight and relevant instead of dumping a generic blob.

### 6.8. Post-wave commit + push checkpoint

**Files:** `workflows/src/capabilities/waves.py` lines 345-366, `workflows/src/phases/implement.py` lines 140-148

After each wave: `git add -A && git commit` if dirty. After each passing gate: `git push --force-with-lease`. Work survives crashes.

### 6.9. Per-wave gates

**File:** `workflows/src/phases/implement.py` line 127

`typecheck+lint` after every wave, `typecheck+lint+harness` after the final wave. Catches breakage early before subsequent waves build on a broken foundation. simple-workflow only runs red/green gates post-execute.

### 6.10. Code signatures via repo-map

**File:** `workflows/src/capabilities/waves.py` lines 29-48

```python
sig_text = _get_repo_map(all_files, worktree)
```

Runs `repo-map.py --blast-radius` on the wave's files to extract function/type signatures. Injected into implement prompts so the agent understands the codebase WITHOUT reading full files. Reduces exploration turns and token burn.

---

## 7. Concrete Recommendations

1. **Replace CLI prompt arg with file-based prompt passing.** Write prompt to `{run_dir}/{phase}-prompt.md`, pass `"Read {path} and execute every instruction in it."` as the positional arg. Add cleanup in `finally`. This is P0 -- the current approach WILL break on real issues with substantive context.

2. **Add `--permission-mode auto` and `--max-turns N` to execute-phase calls.** Without these, the execute agent is decorative. It cannot read files, run tests, or commit. The entire TDD loop in execute.md is a lie. Non-execute phases should use `--max-turns 3` and no permission mode (pure text output).

3. **Replace LLM wave planner with deterministic Python.** Port shftty's `_build_waves()`. Delete `workflows/issue-to-pr/prompts/wave-planner.md` and the `WavePlannerOutput` schema. Save one agent call ($0.01-0.10) per run and eliminate a hallucination failure point.

4. **Add two-step output extraction.** After `_parse_json()` returns None, call haiku with raw text + schema description. Cost: ~$0.001. Prevents pipeline death from formatting variance. Add this to `engine/agent.py` as a method.

5. **Add triage fallback.** When both parse and extraction fail, wrap the issue in a single task and continue. Never kill the run because triage couldn't produce pretty JSON.

6. **Run triage in a neutral cwd.** Use `tempfile.gettempdir()` as cwd for triage (and any other phase that doesn't need tool access). Prevents CLAUDE.md contamination, which the lessons file already documents as a known failure.

7. **Add `--resume` flag to the CLI.** Save phase responses to `{run_dir}/responses/{phase}.json`. On resume, load prior responses, skip completed phases, reuse worktree. Add `resume.json` for chain-following.

8. **Move red gate BEFORE the execute agent.** Make the test-plan phase an actual tool-access agent that writes and runs tests (like shftty's test phase). Confirm red before spending money on implementation.

9. **Add per-wave commit cleanup and push checkpoints.** After each execute-task agent returns, run `git add -A && git commit` if dirty. Push with `--force-with-lease` after each passing gate. Work should survive crashes.

10. **Add per-wave gates.** Run typecheck/lint after each wave, not just at the end. A type error in wave 1 will cascade through waves 2-4, wasting all that spend.

11. **Make validation degrade instead of kill.** On `ValidationError` for non-critical fields, log a warning and continue with partial output + defaults. Reserve `ValidationKill` for structurally unrecoverable failures (triage returns no tasks AND fallback fails). The Pydantic schemas are good -- use them for validation logging, not execution control flow.

12. **Add dynamic knowledge routing.** Map file paths from plan output to relevant knowledge docs. Stop injecting the same static set for every run. Port shftty's `relevant_knowledge_docs()` pattern.

13. **Give the plan phase tool access.** The plan agent should verify file paths and claims against the actual codebase (like shftty's Phase 2: Verify). A plan built on unverified assumptions wastes every downstream dollar. This is the single biggest quality difference in the plans themselves.

---

## Summary

simple-workflow has better formal validation (Pydantic schemas, typed contracts, semantic gates) but worse operational robustness. It is brittle -- any unexpected LLM output kills the run with no recovery. shftty is the opposite: loose validation but graceful degradation at every failure point.

The key pattern difference: simple-workflow treats LLM output as a **typed function return** (must conform or die). shftty treats it as a **noisy signal** to be extracted and cleaned.

The highest-ROI fixes are:
- **#1** (file-based prompts) -- eliminates the arg-length bomb
- **#2** (permission-mode + max-turns) -- makes the execute phase actually functional
- **#4** (two-step extraction) -- prevents most ValidationKill deaths
- **#3** (deterministic waves) -- removes an entire LLM call and failure point
- **#7** (resume) -- stops wasting money on re-runs

These five changes would transform simple-workflow from a prototype into a production-grade pipeline.
