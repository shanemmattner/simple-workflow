# Alternative Engine Plan: `engines/aider_promptfoo/`

**Date**: 2026-06-16
**Goal**: Build a second engine using pre-made components stitched together with minimal custom code.

---

## 1. Recommended Stack

| Slot | Component | Why |
|------|-----------|-----|
| **Source** | `gh` CLI (same as github_claude) | Already works. No reason to change. |
| **Runtime** | **aider** (`--message` flag, non-interactive) | Proven CLI automation. `--message` + `--yes-always` = fully non-interactive. Atomic git commits built in. Supports Claude, GPT, DeepSeek, local models. |
| **Storage** | Per-run SQLite (reuse our schema) | Copy `storage.py` from github_claude. Same 5-table schema. |
| **Workspace** | **aider's built-in git integration** | Aider auto-commits every change. We still create a branch, but skip worktree complexity. |
| **Destination** | `gh` CLI (same as github_claude) | Copy `destination.py`. |
| **Eval** | **promptfoo** CLI | YAML-based eval configs. LLM-as-judge built in. CLI-first. Runs from command line with `promptfoo eval`. |
| **Orchestrator** | **Bash script** (with Python for SQLite logging) | Sequences phases. Calls aider per phase. Calls promptfoo for eval. Minimal code. |

### Why aider over alternatives?

| Tool | Non-interactive? | Git integration | Ease of subprocess call | Verdict |
|------|------------------|-----------------|------------------------|---------|
| **aider** | Yes (`--message` + `--yes-always`) | Built-in (atomic commits) | Trivial | **Winner** |
| SWE-agent | Yes (`sweagent run`) | Needs Docker, patches as output | Medium complexity | Viable but heavier |
| OpenHands | Yes (`--headless -t "task"`) | Needs Docker container | Heavy (Docker required) | Too much infrastructure |
| Claude CLI | Yes (`claude --message`) | None (manual) | Trivial | Already our first engine |
| mini-swe-agent | Yes | Minimal | Trivial | Too simple (100 lines) |
| ccswarm | Yes (`ccswarm pipeline --task`) | Worktree isolation built in | Needs Rust/cargo | Good but less mature |

### Why promptfoo for eval?

- CLI-first: `promptfoo eval -c eval-config.yaml`
- YAML config for test cases (declarative, versionable)
- Built-in assertion types: contains, regex, JSON schema, LLM-graded
- Can compare across models and prompt versions
- OpenAI owns it now but core is MIT, self-hostable
- Already stores results in SQLite (`~/.promptfoo/promptfoo.db`)

---

## 2. How to Stitch Them Together

### Architecture

```
run.sh (bash orchestrator)
  │
  ├── 1. source: gh issue view → issue.json
  ├── 2. branch: git checkout -b issue-<N>
  ├── 3. triage: aider --message "<triage prompt>" --yes-always --no-auto-commits
  │       └── parse triage output → tasks.json
  ├── 4. plan (per task): aider --message "<plan prompt for task N>"
  ├── 5. execute (per task): aider --message "<execute prompt for task N>" --yes-always
  │       └── aider auto-commits each change
  ├── 6. test: run test command, check exit code
  ├── 7. review: aider --message "<review the diff>" --yes-always --no-auto-commits
  ├── 8. destination: gh pr create
  ├── 9. eval: promptfoo eval -c workflows/issue-to-pr/eval.yaml
  └── 10. log: python3 log_run.py (writes to SQLite)
```

### The Glue

The glue is a **single bash script** (~150 lines) that:
1. Reads the issue via `gh`
2. Creates a branch
3. Calls aider in non-interactive mode for each phase
4. Captures stdout/stderr and timing per phase
5. Runs tests between phases (gates)
6. Calls `gh pr create` at the end
7. Calls promptfoo for evaluation
8. Calls a small Python script to log everything to SQLite

Plus a **small Python helper** (~100 lines) that:
- Creates the SQLite DB
- Logs phase outcomes, timing, costs
- Parses aider's output for token counts (aider reports these)

### Key insight: aider flags that make this work

```bash
aider \
  --message "Implement the login form per the spec in issue.md" \
  --yes-always \           # No confirmation prompts
  --no-auto-commits \      # We control when to commit (for phases like triage/review)
  --model claude-sonnet-4-20250514 \
  --read issue.md \        # Read-only context file
  --file src/login.tsx \   # Files aider can edit
  --no-stream              # Clean output for parsing
```

For execute phases, enable auto-commits:
```bash
aider \
  --message "Implement: <task description>" \
  --yes-always \
  --auto-commits \         # Each change = atomic commit
  --model claude-sonnet-4-20250514 \
  --file <files from plan>
```

---

## 3. What's Already Proven

### Aider in automation pipelines

- Aider's `--message` mode is explicitly designed for scripting (documented at aider.chat/docs/scripting.html)
- People use it in CI/CD pipelines today
- Auto-commit means you get a clean git log for free
- Supports `--message-file` for longer prompts

### Promptfoo for agent evaluation

- Has a specific guide for evaluating coding agents (promptfoo.dev/docs/guides/evaluate-coding-agents/)
- GitHub Action available (promptfoo/promptfoo-action) for CI integration
- Can use LLM-as-judge assertions (`llm-rubric` type)
- Can compare runs across prompt versions

### ccswarm worktree isolation (if we need parallel)

- Proven in production at `cargo install ccswarm`
- Could be added later for parallel task execution
- For now, sequential aider calls are simpler

### Combinations people have used

- **aider + gh CLI**: Many people script `gh issue view` → `aider --message` → `gh pr create`
- **promptfoo + any agent**: promptfoo evaluates any text output regardless of how it was produced
- **metaswarm phases**: The triage→plan→execute→review sequence is proven across multiple projects
- No single "glue project" combines all of these, but each pair is well-tested

---

## 4. Effort Estimate

| Component | Custom code needed | Time |
|-----------|-------------------|------|
| `run.sh` orchestrator | ~150 lines bash | 2-3 hours |
| `log_run.py` SQLite logger | ~100 lines Python (or copy from github_claude) | 1 hour |
| `source.py` | Copy from github_claude, zero changes | 0 |
| `destination.py` | Copy from github_claude, zero changes | 0 |
| Prompt templates | Copy from `workflows/issue-to-pr/prompts/`, minor adaptation | 1-2 hours |
| `eval.yaml` for promptfoo | ~50 lines YAML | 1 hour |
| Gate checks (test pass/fail) | ~20 lines bash (just exit code checks) | 30 min |
| README.md | ~50 lines | 30 min |

**Total custom code**: ~300 lines (bash + Python + YAML)
**Total effort**: ~1 day

Compare to github_claude engine: ~800 lines Python across 8 files.

---

## 5. Trade-offs vs Our Claude Engine

### What we gain

| Advantage | Detail |
|-----------|--------|
| **Model flexibility** | Aider supports Claude, GPT-4, DeepSeek, Gemini, local models. Swap with a flag. |
| **Less custom code** | 300 lines vs 800 lines. Aider handles git, commits, file editing. |
| **Built-in eval framework** | Promptfoo is purpose-built. Our eval.py is stubs. |
| **Proven at scale** | Aider has massive community, battle-tested in automation. |
| **Atomic commits per change** | Aider's git integration gives granular commit history. |
| **No Claude subscription required** | Can use API keys with any model. |
| **Faster iteration** | Change a prompt template, re-run. No Python to modify. |

### What we lose

| Disadvantage | Detail |
|--------------|--------|
| **Less control over tool access** | Claude CLI gives full tool access (Read/Write/Edit/Bash). Aider has its own tool model (search-replace edits only). |
| **No multi-turn within a phase** | Aider's `--message` is single-turn. Claude CLI can do 30 turns of autonomous work. |
| **Observability is weaker** | Aider doesn't expose per-turn token counts in a structured way. Less granular logging. |
| **No worktree isolation** | Aider works in the repo directly. Parallel execution would need ccswarm or manual worktrees. |
| **Bash orchestrator is less robust** | Error handling, retry logic harder in bash vs Python. |
| **Gate logic is primitive** | Just exit codes. No structured JSON gate checks. |
| **Aider's edit model is narrower** | Search-replace blocks only. No arbitrary file creation, no running tests mid-turn. |

### The critical difference

Our Claude engine gives the agent **full autonomy within a phase** — 30 turns of thinking, reading files, running tests, editing, iterating. Aider gives you **one shot per call** — it reads your message, makes edits, done.

This means:
- Simple tasks (< 5 file changes): Aider engine is faster and cheaper
- Complex tasks (multi-file refactors, TDD loops): Claude engine is more capable
- The aider engine is better for **high-volume, simpler issues**
- The Claude engine is better for **complex, multi-step issues**

---

## 6. Concrete Plan

### Step 1: Install prerequisites

```bash
# aider
pip install aider-chat

# promptfoo
npm install -g promptfoo

# Already have: gh, git, python3, sqlite3
```

### Step 2: Create the engine folder

```bash
mkdir -p engines/aider_promptfoo/{runs,tools}
```

### Step 3: Copy reusable pieces from github_claude

```bash
cp engines/github_claude/source.py engines/aider_promptfoo/
cp engines/github_claude/destination.py engines/aider_promptfoo/
cp engines/github_claude/storage.py engines/aider_promptfoo/
```

### Step 4: Write the orchestrator (`run.sh`)

```bash
#!/bin/bash
# engines/aider_promptfoo/run.sh
set -euo pipefail

ISSUE_REF="$1"  # owner/repo#123
REPO=$(echo "$ISSUE_REF" | cut -d'#' -f1)
ISSUE_NUM=$(echo "$ISSUE_REF" | cut -d'#' -f2)
RUN_ID="$(date +%Y%m%d-%H%M)-issue-${ISSUE_NUM}"
MODEL="${MODEL:-claude-sonnet-4-20250514}"
DB="engines/aider_promptfoo/runs/${REPO//\//-}-${ISSUE_NUM}-$(date +%Y%m%d-%H%M).db"

# 1. Fetch issue
ISSUE_BODY=$(gh issue view "$ISSUE_NUM" -R "$REPO" --json title,body,comments -q '.')

# 2. Create branch
BRANCH="issue-${ISSUE_NUM}-${RUN_ID}"
git checkout -b "$BRANCH"

# 3. Triage phase
TRIAGE_PROMPT=$(cat workflows/issue-to-pr/prompts/triage.md)
TRIAGE_START=$(date +%s%3N)
aider --message "$TRIAGE_PROMPT\n\nIssue:\n$ISSUE_BODY" \
  --model "$MODEL" --yes-always --no-auto-commits --no-stream \
  > /tmp/triage-output.txt 2>&1
TRIAGE_END=$(date +%s%3N)

# 4. Execute phase (simplified — single pass)
EXECUTE_PROMPT=$(cat workflows/issue-to-pr/prompts/execute.md)
EXECUTE_START=$(date +%s%3N)
aider --message "$EXECUTE_PROMPT\n\nTask:\n$ISSUE_BODY" \
  --model "$MODEL" --yes-always --auto-commits --no-stream \
  > /tmp/execute-output.txt 2>&1
EXECUTE_END=$(date +%s%3N)

# 5. Run tests (gate)
if ! make test 2>/dev/null; then
  echo "Tests failed — aborting"
  # Log failure to SQLite
  python3 engines/aider_promptfoo/log_run.py --db "$DB" --outcome failure
  exit 1
fi

# 6. Push + PR
git push -u origin "$BRANCH"
PR_URL=$(gh pr create -R "$REPO" --title "fix: issue #${ISSUE_NUM}" --body "Automated by aider engine")

# 7. Eval
promptfoo eval -c workflows/issue-to-pr/eval.yaml --output "/tmp/eval-${RUN_ID}.json"

# 8. Log everything
python3 engines/aider_promptfoo/log_run.py \
  --db "$DB" --outcome success --pr-url "$PR_URL" \
  --triage-output /tmp/triage-output.txt \
  --execute-output /tmp/execute-output.txt \
  --triage-duration $((TRIAGE_END - TRIAGE_START)) \
  --execute-duration $((EXECUTE_END - EXECUTE_START))
```

### Step 5: Write the promptfoo eval config

```yaml
# workflows/issue-to-pr/eval.yaml
description: Evaluate issue-to-PR run quality

providers:
  - id: file:///tmp/execute-output.txt  # or use a custom provider

prompts:
  - "Review this PR diff and score it on correctness (0-10), completeness (0-10), and code quality (0-10)."

tests:
  - vars:
      diff: "{{git diff main..HEAD}}"
    assert:
      - type: llm-rubric
        value: "The code change correctly addresses the issue requirements"
      - type: llm-rubric
        value: "The code follows existing patterns in the codebase"
      - type: llm-rubric
        value: "No obvious bugs or missing error handling"
```

### Step 6: Write the SQLite logger

```python
# engines/aider_promptfoo/log_run.py
# Thin wrapper — imports storage.py and logs phases
```

### Step 7: Write the README

Document what's there, how to run it, prerequisites.

---

## 7. Future Enhancements (Not For V1)

| Enhancement | Tool | When |
|-------------|------|------|
| Parallel task execution | ccswarm worktrees | When we need >1 task per run |
| Multi-turn execution | Switch aider calls to Claude CLI for complex tasks | When simple issues are solved, tackle hard ones |
| Better observability | Langfuse (self-hosted Docker) with OTEL export | When we have enough runs to analyze |
| Cross-model review | Run review phase with a different model flag | Free with aider's `--model` flag |
| Batch processing | Loop over `gh issue list --label ready-for-agent` | When pipeline is stable |
| CI/CD integration | GitHub Action that calls `run.sh` | When we trust it enough |

---

## 8. Decision: What to Build First

**Recommendation**: Build the aider engine as a "light" complement to the Claude engine.

- Use it for **simple, well-scoped issues** (bug fixes, small features, doc updates)
- Keep the Claude engine for **complex, multi-step issues** (refactors, new features with TDD)
- The two engines share the same `workflows/` prompts and `storage.py` schema
- Comparing results across engines (same issue, different engine) tells us where each excels

**First milestone**: Run `engines/aider_promptfoo/run.sh owner/repo#123` and get a PR + SQLite log + promptfoo eval score. That is the proof of concept.

---

## Sources

- [Aider scripting docs](https://aider.chat/docs/scripting.html)
- [Aider git integration](https://aider.chat/docs/git.html)
- [Promptfoo evaluate coding agents guide](https://www.promptfoo.dev/docs/guides/evaluate-coding-agents/)
- [ccswarm on crates.io](https://crates.io/crates/ccswarm)
- [ccswarm GitHub](https://github.com/nwiizo/ccswarm)
- [metaswarm GitHub](https://github.com/dsifry/metaswarm)
- [SWE-agent CLI tutorial](https://swe-agent.com/latest/usage/cl_tutorial/)
- [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)
- [OpenHands headless mode](https://docs.openhands.dev/openhands/usage/cli/headless)
- [Langfuse OpenTelemetry integration](https://langfuse.com/integrations/native/opentelemetry)
- [Promptfoo GitHub Action](https://github.com/promptfoo/promptfoo-action)
- [Aider 2026 guide](https://www.deployhq.com/guides/aider)
