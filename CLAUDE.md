# simple-workflow

Pipeline that transforms GitHub issues into tested, reviewed PRs. Reads an issue, decomposes it into tasks, plans implementation and tests, executes in dependency-ordered waves, reviews the combined diff, and opens a PR. Every LLM call is logged to a per-run SQLite database.

## Run

```bash
# Claude engine (default)
./scripts/run.sh owner/repo#123
./scripts/run.sh owner/repo#123 --budget 2.00 --model opus
python -m engine owner/repo#123

# Three-step engine (Claude subscription via CLI)
./scripts/run.sh owner/repo#123 --engine three-step
python -m engines.three_step owner/repo#123 --budget 3.00
python -m engines.three_step owner/repo#123 --model opus  # override all phases
```

## Stats

```bash
./scripts/stats.sh cost-by-phase
./scripts/stats.sh run-details <run_id>
./scripts/stats.sh  # shows all available queries
```

## Test

```bash
python3 -m pytest tests/
```

## Key Files

- `engine/orchestrator.py` -- CLI entry point, phase sequencing, context assembly
- `engine/source.py` -- fetches GitHub issues via `gh` CLI, posts status comments
- `engine/runtime.py` -- calls Claude CLI as subprocess, parses response JSON, tracks tokens/cost
- `engine/storage.py` -- per-run SQLite .db file (tables: run, phase, message, tool_call, event)
- `engine/workspace.py` -- git worktree lifecycle management
- `engine/destination.py` -- pushes branch and creates GitHub PR via `gh`
- `engine/gates.py` -- validation gates and post-phase checks
- `engine/eval.py` -- LLM-as-judge scoring, failure categorization (stubs)
- `engine/__main__.py` -- package entry point for `python -m engine`
- `engines/shared/` -- shared modules (source, storage, workspace, destination) used by three_step and github_minimax
- `engines/three_step/claude_runtime.py` -- Claude CLI subscription runtime (wraps `claude` with --output-format json)
- `engines/three_step/runtime.py` -- legacy OpenAI SDK agent loop against Z.ai (retained for reference, unused)
- `engines/three_step/orchestrator.py` -- 3-phase pipeline: investigate, implement, review+PR (uses claude_runtime)
- `workflows/issue-to-pr/workflow.yaml` -- phase definitions, model config, gates, budget
- `workflows/issue-to-pr/prompts/` -- frozen prompt templates per phase (triage, verify, plan, test-plan, wave-planner, execute, review, improve)
- `scripts/run.sh` -- shell entry point
- `scripts/stats.sh` -- SQLite dashboard queries

## Test run

```bash
# Quick test with haiku (cheapest)
./scripts/run.sh owner/repo#123 --budget 1.00 --model haiku --repo-path /path/to/repo

# On Mac Studio (auto-detects repo path)
./scripts/run.sh shanemmattner/shftty#870 --budget 1.00 --model haiku

# Check status while running
./scripts/status.sh

# Live tail
./scripts/tail.sh
```

## Stall protection

Three layers protect against hung `claude -p` processes:
1. **Env vars**: `CLAUDE_STREAM_IDLE_TIMEOUT_MS=600000`, `API_TIMEOUT_MS=1200000` (set automatically)
2. **Wall-clock cap**: `gtimeout` wrapper in run.sh (default 60 min, override via `CLAUDE_WALL_TIMEOUT_S`)
3. **Phase-aware watchdog**: Monitors stdout for content events, kills on 4 stall patterns (pre-token 300s, post-token 120s, content-stall 240s, post-result 30s)

## Important

- `engine/runs/` holds per-run `.db` files (gitignored). Each `.db` is self-contained -- full replay of every phase, message, tool call, and event.
- Prompts are frozen templates in `workflows/issue-to-pr/prompts/`. Change them deliberately. `system_prompt_hash` in phase logs links runs to exact prompt versions.
- Target repos provide context via `.workflows/` (context.md, testing.md, knowledge/).
- Three-step engine uses Claude CLI subscription (no API key needed — uses logged-in `claude` CLI). Default models: haiku (investigate/review), sonnet (implement). Override with `--model`.
- No abstract interfaces. Each engine is a self-contained folder. New engine = copy the folder, swap what differs.
