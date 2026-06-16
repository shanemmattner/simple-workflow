# simple_workflow

Pipeline that transforms GitHub issues into tested, reviewed PRs. Takes an issue URL, decomposes it into tasks, plans implementation and tests, executes in dependency-ordered waves, reviews the combined diff, and opens a PR.

## Run

```bash
./scripts/run.sh owner/repo#123
./scripts/run.sh owner/repo#123 --budget 2.00 --model opus
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

- `workflows/issue-to-pr/workflow.yaml` -- phase definitions, model config, gates, budget
- `workflows/issue-to-pr/prompts/` -- frozen prompt templates per phase
- `schemas.py` -- ALL phase I/O Pydantic contracts (triage, plan, test-plan, execute, review)
- `engine/orchestrator.py` -- CLI entry point, phase dispatch, context assembly
- `engine/agent.py` -- claude -p wrapper, stdin prompt piping, output capture
- `engine/gates.py` -- validation gates and post-phase checks
- `engine/worktree.py` -- git worktree lifecycle management
- `engine/waves.py` -- wave planner execution and parallel dispatch
- `db.py` -- SQLite schema and operations (pipeline_runs, phase_logs, reviews)
- `scripts/run.sh` -- shell entry point
- `scripts/stats.sh` -- SQLite dashboard queries

## Important

- `runs/` is gitignored. All run artifacts (prompts, responses, DB) go there.
- Prompts are frozen templates. Change them deliberately, not casually. Git history is the version control; `prompt_hash` in phase_logs links runs to prompt versions.
- `schemas.py` defines ALL phase I/O contracts. Change schemas carefully -- every phase, gate, and downstream consumer depends on them.
- Target repos provide context via `.workflows/` (context.md, testing.md, knowledge/).
