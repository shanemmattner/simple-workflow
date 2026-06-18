# simple-workflow

Pipeline that transforms GitHub issues into tested, reviewed PRs. Reads an issue, decomposes it into tasks, plans implementation and tests, executes in dependency-ordered waves, reviews the combined diff, and opens a PR. Every LLM call is logged to a per-run SQLite database.

## Run

```bash
# Claude engine (default)
./scripts/run.sh owner/repo#123
./scripts/run.sh owner/repo#123 --budget 2.00 --model opus
python -m engines.github_claude owner/repo#123

# OpenHands engine (DeepSeek V4 Flash via OpenRouter)
./scripts/run.sh owner/repo#123 --engine openhands
./scripts/run.sh owner/repo#123 --engine openhands --budget 2.00
python -m engines.github_openhands owner/repo#123

# Three-step engine (GLM-5.2 via Z.ai subscription)
./scripts/run.sh owner/repo#123 --engine three-step
python -m engines.three_step owner/repo#123 --budget 3.00
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

- `engines/github_claude/orchestrator.py` -- CLI entry point, phase sequencing, context assembly
- `engines/github_claude/source.py` -- fetches GitHub issues via `gh` CLI, posts status comments
- `engines/github_claude/runtime.py` -- calls Claude CLI as subprocess, parses response JSON, tracks tokens/cost
- `engines/github_claude/storage.py` -- per-run SQLite .db file (tables: run, phase, message, tool_call, event)
- `engines/github_claude/workspace.py` -- git worktree lifecycle management
- `engines/github_claude/destination.py` -- pushes branch and creates GitHub PR via `gh`
- `engines/github_claude/gates.py` -- validation gates and post-phase checks
- `engines/github_claude/eval.py` -- LLM-as-judge scoring, failure categorization (stubs)
- `engines/github_claude/__main__.py` -- package entry point for `python -m engines.github_claude`
- `engines/github_openhands/runtime.py` -- OpenHands SDK runtime (DeepSeek V4 Flash via OpenRouter, no Docker)
- `engines/github_openhands/__main__.py` -- package entry point for `python -m engines.github_openhands`
- `engines/three_step/runtime.py` -- direct OpenAI SDK agent loop against Z.ai (no OpenHands, no LiteLLM)
- `engines/three_step/orchestrator.py` -- 3-phase pipeline: investigate, implement, review+PR
- `workflows/issue-to-pr/workflow.yaml` -- phase definitions, model config, gates, budget
- `workflows/issue-to-pr/prompts/` -- frozen prompt templates per phase (triage, verify, plan, test-plan, wave-planner, execute, review, improve)
- `scripts/run.sh` -- shell entry point
- `scripts/stats.sh` -- SQLite dashboard queries

## Important

- `engines/github_claude/runs/` holds per-run `.db` files (gitignored). Each `.db` is self-contained -- full replay of every phase, message, tool call, and event.
- Prompts are frozen templates in `workflows/issue-to-pr/prompts/`. Change them deliberately. `system_prompt_hash` in phase logs links runs to exact prompt versions.
- Target repos provide context via `.workflows/` (context.md, testing.md, knowledge/).
- `ZAI_API_KEY` required for three-step engine (GLM-5.2 via Z.ai subscription).
- No abstract interfaces. Each engine is a self-contained folder. New engine = copy the folder, swap what differs.
