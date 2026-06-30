# simple-workflow

Transforms GitHub issues into tested, reviewed pull requests. Point it at an issue, and it investigates the codebase, writes a fix, runs tests, reviews the diff, and opens a PR. Every LLM call is logged to a per-run SQLite database for full replay and cost tracking.

See [PRD.md](PRD.md) for architecture details and design rationale.

---

## Quick start

### Prerequisites

- Python 3.11+
- `claude` CLI installed and logged in (`claude --version` should work)
- `gh` CLI authenticated (`gh auth status` should show a logged-in account)
- `git` with worktree support

No `pip install` needed. The project uses stdlib + the `claude` CLI subprocess.

### Clone

```bash
git clone https://github.com/your-org/simple-workflow
cd simple-workflow
```

### Run

```bash
# Three-step engine (production — uses Claude subscription)
./scripts/run.sh owner/repo#123

# Specify a model or budget
./scripts/run.sh owner/repo#123 --model opus
./scripts/run.sh owner/repo#123 --budget 3.00

# Reference engine (8-phase with gates)
./scripts/run.sh owner/repo#123 --engine claude
```

The script auto-detects the repo path. If your checkout lives at `../repo` relative to this directory, it will be found automatically. Otherwise pass `--repo-path /path/to/repo`.

---

## Engines

| Engine | Flag | Phases | Description |
|--------|------|--------|-------------|
| `three-step` | `--engine three-step` | investigate → implement → review + PR (+ retry, deep review, audit, learning) | Production engine. Uses Claude CLI subscription. Default when no engine is specified via `./scripts/run.sh`. |
| `github_claude` | `--engine claude` | triage → verify → plan → test-plan → wave-planner → execute → review → improve | Reference design. 8-phase pipeline with per-phase gates and parallel execution waves. SQLite logging. |

Run any engine directly with Python if you need to pass extra flags:

```bash
python -m engines.three_step owner/repo#123 --budget 3.00 --model opus
python -m engines.github_claude owner/repo#123
```

---

## Model backends

The adapter router in `adapters/__init__.py` picks the right backend based on the model name prefix.

| Adapter | Backend | Available models | Short names | Env var |
|---------|---------|-----------------|-------------|---------|
| `claude_cli` | Claude subscription via CLI | `sonnet`, `haiku`, `opus` | `claude`, `claude-sonnet`, `claude-haiku`, `claude-opus` | None — uses logged-in `claude` CLI |
| `openrouter` | OpenRouter API | deepseek-v4-flash/pro, deepseek-v3.2, grok-code-fast-1, gemini-3-flash, mimo, devstral, gpt-oss-120b | `deepseek`, `deepseek-flash`, `deepseek-pro`, `grok`, `gemini-flash`, `mimo`, `devstral`, `gpt-oss` | `OPENROUTER_API_KEY` |
| `zai` | Z.ai subscription | `glm-5.2`, `glm-4.7`, `glm-4.7-flash` | `glm`, `glm-flash` | `ZAI_API_KEY` (or `GLM_API_KEY`) |
| `minimax` | MiniMax API | `MiniMax-M3` | `minimax`, `m3`, `minimax-m3` | `MINIMAX_API_KEY` |

### How routing works

Pass a short name or full model name to `--model`. The router checks the prefix:

- Starts with `glm` → Z.ai adapter
- Starts with `claude`, or is `sonnet`/`haiku`/`opus` → Claude CLI adapter
- Starts with `codex` → Codex adapter
- Everything else → OpenRouter (with approved-model gate)

### Per-phase model routing (github_claude engine)

Defined in `workflows/issue-to-pr/workflow.yaml`:

| Phase | Model | Reason |
|-------|-------|--------|
| triage, verify, test-plan, wave-planner, review, improve | haiku | Cheap — reading and structuring |
| plan, execute | sonnet | Writing correct code |

Override all phases with `--model opus` at the CLI.

---

## Per-repo setup

Target repos can provide context that agents inject into every phase. Create a `.workflows/` directory in the target repo:

```
.workflows/
  context.md        # Tech stack, conventions, project overview
  knowledge/
    auth.md         # Domain knowledge (one file per topic)
    database.md
    api.md
```

The `github_claude` engine reads these files and prepends them to every agent prompt. If `.workflows/` does not exist, the engine runs without repo context.

There are no required files — all are optional. More context = fewer hallucinated file paths and better-aligned fixes.

---

## Stats

```bash
./scripts/stats.sh              # show available queries
./scripts/stats.sh cost-by-phase
./scripts/stats.sh run-details <run_id>
```

Run `.db` files are stored in `engines/github_claude/runs/` (gitignored). Each file is self-contained — full replay of every phase, message, tool call, and event for that run.

---

## Key directories

| Directory | Description |
|-----------|-------------|
| `engines/github_claude/` | Reference 8-phase engine: orchestrator, runtime, storage, gates, workspace, destination |
| `engines/three_step/` | Production 3-phase engine using Claude CLI subscription runtime |
| `engines/shared/` | Shared modules (source, storage, workspace, destination) used by three_step and github_minimax |
| `adapters/` | Provider adapters: claude_cli, openrouter, zai, minimax |
| `workflows/issue-to-pr/` | Phase definitions (`workflow.yaml`) and frozen prompt templates (`prompts/`) |
| `workspace/` | Temporary git worktrees (cleaned up after each run) |
| `work/` | Research docs, plans, session notes |

---

## Tests

```bash
python3 -m pytest tests/
```

---

## Docker sandbox (optional)

For isolated runs that cannot touch your host filesystem, see the sandbox scripts at `../../scripts/pipeline-sandbox/` (requires OrbStack or Docker).

---

## Notes

- Prompts are frozen templates in `workflows/issue-to-pr/prompts/`. Each phase logs a `system_prompt_hash` so runs are linked to exact prompt versions. Change prompts deliberately.
- The three-step engine embeds its prompts directly in `engines/three_step/orchestrator.py` — no separate prompt files.
- Each engine is a self-contained folder. To add a new engine, copy a folder and change what differs. There are no abstract interfaces or plugin machinery.
- `engines/github_claude/runs/` accumulates `.db` files over time. They are gitignored and safe to delete if disk space is a concern.
