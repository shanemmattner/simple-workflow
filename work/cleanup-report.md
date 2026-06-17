# Old Engine Cleanup Report

Generated: 2026-06-16

## 1. Confirmed: New engine has ZERO imports from old code

```
grep -r "from engine\." engines/github_claude/   -> (empty)
grep -r "import engine"  engines/github_claude/   -> (empty)
grep -r "from db "       engines/github_claude/   -> (empty)
grep -r "import db"      engines/github_claude/   -> (empty)
grep -r "from schemas"   engines/github_claude/   -> (empty)
grep -r "import schemas" engines/github_claude/   -> (empty)
```

The new engine (`engines/github_claude/`) is fully self-contained with its own
`storage.py` (per-run SQLite), `gates.py`, `workspace.py`, etc.

## 2. Files safe to delete

These files are only referenced by each other. No code outside the old
`engine/` package imports them:

| File | Notes |
|------|-------|
| `engine/orchestrator.py` | Old orchestrator. Replaced by `engines/github_claude/orchestrator.py` |
| `engine/agent.py` | Old agent runner. Replaced by `engines/github_claude/runtime.py` |
| `engine/gates.py` | Old gate checks. Replaced by `engines/github_claude/gates.py` |
| `engine/worktree.py` | Old worktree mgmt. Replaced by `engines/github_claude/workspace.py` |
| `engine/waves.py` | Old wave dispatch. Inlined into new orchestrator |
| `engine/__init__.py` | Package init (empty) |
| `engine/capabilities/` | Empty subdirectory |
| `engine/__pycache__/` | Stale bytecode cache |
| `db.py` | Old shared SQLite. Replaced by `engines/github_claude/storage.py` |
| `schemas.py` | Old Pydantic models (see caveat below) |

### Caveat on schemas.py

`schemas.py` is only imported by `engine/orchestrator.py` -- no external
consumer. However, the Pydantic model names (`TriageOutput`, `PlanOutput`,
`TestPlanOutput`, `WavePlannerOutput`, `ReviewOutput`) may appear in prompt
templates or evaluation fixtures. Worth a quick grep for those class names
before deleting. If nothing hits outside `engine/`, it is safe to delete
alongside the rest.

## 3. Required config update BEFORE deletion

**`pyproject.toml` line 11** still points to the old engine:

```toml
[project.scripts]
sw = "engine.orchestrator:main"
```

This must be updated to the new entry point (likely
`engines.github_claude.__main__:main` or similar) or removed. Deleting the old
engine without updating this will break `pip install -e .` and the `sw` CLI
command.

## 4. Other cleanup items

| Item | Location | Action |
|------|----------|--------|
| Stale `__pycache__` | `engine/__pycache__/` | Delete with the rest of `engine/` |
| `work/` docs reference old paths | `work/implementation-plan.md`, `work/pipeline-comparison.md` | These are historical design docs. No code depends on them. Leave as-is or update path references if desired. |
| `engines/github_claude/agents/.gitkeep` | Placeholder | Keep -- signals future agent definitions directory |
| `engines/github_claude/tools/.gitkeep` | Placeholder | Keep -- signals future tools directory |

## 5. Recommended deletion sequence

1. Update `pyproject.toml` entry point to new engine (or remove the `[project.scripts]` section if the CLI is no longer used).
2. `rm -rf engine/` (removes all 6 .py files + capabilities/ + __pycache__/)
3. `rm db.py`
4. `rm schemas.py` (after confirming no prompt templates reference the class names)
5. Commit with message like: `chore: remove old engine/, db.py, schemas.py (replaced by engines/github_claude/)`
