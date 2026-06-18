"""Three-step orchestrator: investigate -> implement -> review+PR.

Uses a direct OpenAI SDK runtime (engines.three_step.runtime) against Z.ai's
OpenAI-compatible endpoint. Reuses shared modules from github_openhands for
source, storage, workspace, and destination.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from engines.three_step import runtime
from engines.github_openhands import source, storage, workspace, destination

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

# ---------------------------------------------------------------------------
# Prompt templates (embedded, no separate files)
# ---------------------------------------------------------------------------

INVESTIGATE_PROMPT = """\
You are investigating GitHub issue #{issue_number}.

Your working directory is the target repository. All paths are relative to it.

## Issue

{issue_body}

## Instructions

Read the issue carefully. Explore the codebase to understand what's happening.

Start from the user-facing symptom -- what component, page, or function handles \
the action described in the issue? Trace inward from there.

You have a STRICT turn budget. Spend turns 1-18 exploring. By turn 19, you MUST \
stop calling tools and write your final investigation report as a plain text message.

If you keep calling tools until you run out of turns, your report will be lost. \
STOP and WRITE before the limit.

Produce a structured investigation report with these sections:

1. **Root Cause** -- what exactly is wrong and why
2. **Affected Files** -- list every file that needs to change
3. **Proposed Fix** -- for each file, show the before/after code changes
4. **Risk Assessment** -- what could go wrong, what else might break

Be specific. Reference actual file paths, function names, and line numbers.
Do NOT make changes -- investigation only.
"""

IMPLEMENT_PROMPT = """\
You are implementing a fix for GitHub issue #{issue_number}.

Your working directory is the target repository. All paths are relative to it.

## Issue

{issue_body}

## Investigation Report

{investigation_report}

## Instructions

The investigation report above tells you what to change. Do NOT re-investigate. \
Start editing files immediately.

Spend at most 3 turns reading files to orient yourself, then start making changes.

1. Apply the code changes identified in the investigation
2. Run any existing tests to verify nothing is broken
3. If appropriate, add a test for the fix
4. After making all changes, run `git add -A && git commit -m 'fix: resolve #{issue_number}'`

When done, write a summary of what you changed as your final message.

Keep changes minimal. Do not refactor unrelated code. Do not leave debug \
statements or commented-out code.
"""

REVIEW_PROMPT = """\
You are reviewing changes for GitHub issue #{issue_number}.

Your working directory is the target repository.

## Issue

{issue_body}

## Diff

```diff
{diff}
```

## Instructions

Review the diff and check:

1. Does it actually fix the issue described?
2. Are there any bugs or potential regressions?
3. Are the changes minimal and focused?
4. Is there any debug code, commented-out code, or leftovers?

Produce a PR summary with:

- **What changed** -- brief description of the fix
- **How it works** -- technical explanation
- **Testing** -- what was tested or should be tested
- **Verdict** -- one of: APPROVE, REQUEST_CHANGES, NEEDS_DISCUSSION

If verdict is REQUEST_CHANGES, explain what needs to change.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import adapters

FALLBACK_MODEL = "deepseek-pro"

_RATE_LIMIT_INDICATORS = ("429", "rate limit", "quota", "exhausted", "too many requests")


class BudgetExceeded(RuntimeError):
    pass


def _guard(spent: float, budget: float) -> None:
    if spent > budget:
        raise BudgetExceeded(f"${spent:.2f} > budget ${budget:.2f}")


def _content(resp: dict) -> str:
    """Extract content string from agent response."""
    return resp["content"] if isinstance(resp["content"], str) else json.dumps(resp["content"])


def _start_phase(conn, name: str) -> int:
    return storage.log_phase(conn, name)


def _finish_phase(conn, phase_id: int, resp: dict | None, failed: bool = False) -> None:
    """Log messages and mark phase complete."""
    if resp:
        prompt = resp.get("_prompt", "")
        content = resp.get("content", "")
        if prompt:
            storage.log_message(conn, phase_id, turn_number=1, role="user", content=prompt)
        if content:
            storage.log_message(
                conn, phase_id, turn_number=1, role="assistant",
                content=content if isinstance(content, str) else json.dumps(content),
                tokens_in=resp.get("tokens_in", 0),
                tokens_out=resp.get("tokens_out", 0),
                cost=resp.get("cost", 0),
            )
    storage.finish_phase(
        conn, phase_id,
        status="failed" if failed else "completed",
        cost=resp.get("cost", 0) if resp else 0,
        tokens_in=resp.get("tokens_in", 0) if resp else 0,
        tokens_out=resp.get("tokens_out", 0) if resp else 0,
    )


def _call_with_fallback(prompt: str, *, model: str, cwd: str, max_turns: int) -> dict:
    """Route to correct API backend via the adapter layer, with rate-limit fallback."""
    config = adapters.get_config(model)
    resp = runtime.call_agent(
        prompt, model=config["model"], cwd=cwd, max_turns=max_turns,
        api_key=config["api_key"], base_url=config["base_url"],
    )
    if resp.get("finish_reason") == "error":
        content = (resp.get("content", "") or "").lower()
        if any(ind in content for ind in _RATE_LIMIT_INDICATORS):
            log.warning("%s rate-limited, falling back to %s", model, FALLBACK_MODEL)
            fallback_config = adapters.get_config(FALLBACK_MODEL)
            resp = runtime.call_agent(
                prompt, model=fallback_config["model"], cwd=cwd, max_turns=max_turns,
                api_key=fallback_config["api_key"], base_url=fallback_config["base_url"],
            )
    return resp


def _find_repo_path(repo: str) -> str:
    """Auto-resolve a local clone path for 'owner/repo' style repo strings.

    Search order:
    1. Walk up from this file's directory looking for repos/{name}
    2. Check cwd/../repos/{name}
    3. Glob /Users/shanemattner/Desktop/personal-assistant-clones/*/repos/{name}
    """
    name = repo.split("/")[-1] if "/" in repo else repo

    # 1. Walk up from script directory looking for repos/{name}
    script_dir = Path(__file__).resolve().parent
    cursor = script_dir
    for _ in range(10):  # cap to avoid infinite walk
        candidate = cursor / "repos" / name
        if candidate.is_dir() and (candidate / ".git").exists():
            log.info("auto-resolved repo_path: %s (walk-up from script)", candidate)
            return str(candidate)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent

    # 2. Check relative to cwd
    cwd_candidate = Path(os.getcwd()).parent / "repos" / name
    if cwd_candidate.is_dir() and (cwd_candidate / ".git").exists():
        log.info("auto-resolved repo_path: %s (relative to cwd)", cwd_candidate)
        return str(cwd_candidate)

    # 3. Glob PA clone directories
    for match in glob.glob(f"/Users/shanemattner/Desktop/personal-assistant-clones/*/repos/{name}"):
        p = Path(match)
        if p.is_dir() and (p / ".git").exists():
            log.info("auto-resolved repo_path: %s (glob)", p)
            return str(p)

    raise FileNotFoundError(
        f"Could not find local clone of {repo!r} (looked for 'repos/{name}'). "
        "Pass --repo-path explicitly."
    )


def _post_failure(repo: str, num: int, err: str) -> None:
    try:
        source.post_comment(repo, num, f"Pipeline failed.\n\n```\n{err[:2000]}\n```")
    except Exception:
        log.warning("failed to post failure comment on %s#%d", repo, num)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    repo: str,
    issue_number: int,
    *,
    budget: float = 5.00,
    model_override: str | None = None,
    repo_path: str | None = None,
) -> dict:
    """Run the 3-step pipeline: investigate -> implement -> review+PR.

    Returns dict with keys: status, pr_url, spent_usd, run_id, error.
    """
    model = model_override or "glm-5.2"

    # Fetch issue
    issue = source.fetch_issue(repo, issue_number)
    issue_body = f"# {issue['title']}\n\n{issue['body']}"

    # Set up storage
    db_path, conn = storage.create_run_db(repo, issue_number, model=model)
    run_id = db_path.stem if hasattr(db_path, "stem") else str(db_path)
    # db_path is a string from storage module, extract run_id from filename
    run_id = os.path.splitext(os.path.basename(db_path))[0]

    # Set up workspace
    branch = f"three-step/issue-{issue_number}"
    if not repo_path:
        repo_path = _find_repo_path(repo)
    wt = workspace.create_workspace(repo_path, branch)
    spent = 0.0

    try:
        # ---- Phase 1: Investigate ----
        log.info("[investigate] starting (max_turns=25, model=%s)", model)
        pid = _start_phase(conn, "investigate")
        prompt = INVESTIGATE_PROMPT.format(
            issue_number=issue_number, issue_body=issue_body,
        )
        resp = _call_with_fallback(prompt, model=model, cwd=wt, max_turns=25)
        resp["_prompt"] = prompt
        spent += resp["cost"]
        _finish_phase(conn, pid, resp,
                      failed=(resp.get("finish_reason") == "error"))
        _guard(spent, budget)

        investigation_report = _content(resp)
        log.info("[investigate] done (%.1fs, $%.4f, %d chars)",
                 resp["duration_s"], resp["cost"], len(investigation_report))

        if resp.get("finish_reason") == "error" and len(investigation_report) < 200:
            raise RuntimeError(f"Investigate phase failed: {investigation_report[:500]}")
        if resp.get("finish_reason") in ("error", "max_iterations"):
            log.warning("[investigate] finished with %s but has %d chars of content — continuing",
                        resp.get("finish_reason"), len(investigation_report))

        # ---- Phase 2: Implement ----
        log.info("[implement] starting (max_turns=30, model=%s)", model)
        pid = _start_phase(conn, "implement")
        prompt = IMPLEMENT_PROMPT.format(
            issue_number=issue_number,
            issue_body=issue_body,
            investigation_report=investigation_report,
        )
        resp = _call_with_fallback(prompt, model=model, cwd=wt, max_turns=30)
        resp["_prompt"] = prompt
        spent += resp["cost"]
        _finish_phase(conn, pid, resp,
                      failed=(resp.get("finish_reason") == "error"))
        _guard(spent, budget)

        log.info("[implement] done (%.1fs, $%.4f)",
                 resp["duration_s"], resp["cost"])

        # Safety-net commit: catch changes the agent failed to commit
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"], cwd=wt,
            capture_output=True, text=True,
        ).stdout.strip()
        commits = subprocess.run(
            ["git", "log", "origin/main..HEAD", "--oneline"], cwd=wt,
            capture_output=True, text=True,
        ).stdout.strip()

        if porcelain:
            log.warning("implement phase left uncommitted changes -- safety-net commit")
            subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"fix: resolve #{issue_number}"],
                cwd=wt, check=True,
            )
            storage.log_event(conn, "safety_net_commit", {"files": porcelain})
        elif not commits:
            raise RuntimeError(
                "Implement phase produced no changes -- "
                "no commits on branch and no uncommitted files"
            )

        # ---- Phase 3: Review + PR ----
        diff = workspace.get_diff(wt)[:50_000]
        log.info("[review] starting (max_turns=5, model=%s)", model)
        pid = _start_phase(conn, "review")
        prompt = REVIEW_PROMPT.format(
            issue_number=issue_number,
            issue_body=issue_body,
            diff=diff,
        )
        resp = _call_with_fallback(prompt, model=model, cwd=wt, max_turns=5)
        resp["_prompt"] = prompt
        spent += resp["cost"]
        _finish_phase(conn, pid, resp,
                      failed=(resp.get("finish_reason") == "error"))

        review_text = _content(resp)
        log.info("[review] done (%.1fs, $%.4f)", resp["duration_s"], resp["cost"])

        # Push and create PR
        destination.push_branch(wt, branch)
        body = destination.format_pr_body(issue_number, review_text, db_path, [])
        pr = destination.create_pr(repo, branch, f"fix: resolve #{issue_number}", body)

        storage.finish_run(conn, "ok", total_cost=spent, branch=branch)
        return {
            "status": "ok",
            "pr_url": pr["url"],
            "spent_usd": spent,
            "run_id": run_id,
        }

    except BudgetExceeded as e:
        log.error("budget exceeded: %s", e)
        storage.finish_run(conn, "budget_exceeded", total_cost=spent)
        _post_failure(repo, issue_number, str(e))
        return {"status": "error", "error": str(e), "spent_usd": spent, "run_id": run_id}

    except Exception as e:
        log.exception("pipeline failed")
        storage.finish_run(conn, "error", total_cost=spent)
        storage.log_event(conn, "pipeline_error", {"error": str(e)})
        _post_failure(repo, issue_number, str(e))
        return {"status": "error", "error": str(e), "spent_usd": spent, "run_id": run_id}

    finally:
        # Do NOT clean up the worktree -- just log its location
        log.info("worktree preserved at: %s", wt)
        conn.close()
