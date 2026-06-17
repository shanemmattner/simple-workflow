"""GitHub PR creation and branch push logic.

Extracted from engine/orchestrator.py — plain functions, plain dicts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess

log = logging.getLogger(__name__)


class PushFailed(RuntimeError):
    """Branch push to origin failed (auth, network, etc.)."""
    pass


class PRAlreadyExists(RuntimeError):
    """A PR already exists for this branch."""
    pass


class BranchNotFound(RuntimeError):
    """The branch does not exist locally."""
    pass


def push_branch(workspace_path: str, branch: str) -> None:
    """Push *branch* to origin from the given workspace (worktree) path.

    Raises PushFailed on auth/network errors, BranchNotFound if the
    branch doesn't exist locally.
    """
    # Verify the branch exists locally
    check = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        capture_output=True, text=True, cwd=workspace_path, timeout=10,
    )
    if check.returncode != 0:
        raise BranchNotFound(f"branch {branch!r} not found in {workspace_path}")

    result = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        capture_output=True, text=True, cwd=workspace_path, timeout=60,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise PushFailed(f"git push failed: {stderr}")


def create_pr(
    repo: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
) -> dict:
    """Create a GitHub PR via ``gh pr create``.

    Returns ``{"number": int, "url": str}``.

    Raises PRAlreadyExists if a PR for *branch* already exists,
    RuntimeError on other failures.
    """
    result = subprocess.run(
        [
            "gh", "pr", "create",
            "--repo", repo,
            "--head", branch,
            "--base", base,
            "--title", title,
            "--body", body,
        ],
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "already exists" in stderr.lower():
            raise PRAlreadyExists(f"PR already exists for branch {branch!r}: {stderr}")
        raise RuntimeError(f"gh pr create failed: {stderr}")

    url = result.stdout.strip()

    # Extract PR number from URL (https://github.com/owner/repo/pull/123)
    number = 0
    parts = url.rstrip("/").split("/")
    if parts and parts[-1].isdigit():
        number = int(parts[-1])

    return {"number": number, "url": url}


def format_pr_body(
    issue_number: int,
    review_summary: str,
    run_db_path: str,
    phases_summary: list[dict],
) -> str:
    """Build the PR description body.

    Parameters
    ----------
    issue_number:
        The GitHub issue this PR addresses.
    review_summary:
        Formatted review findings text (may be empty).
    run_db_path:
        Path to the runs.db SQLite database, used to pull cost data.
    phases_summary:
        List of dicts with at least ``{"phase": str, "cost_usd": float,
        "duration_s": float}``.
    """
    lines: list[str] = []

    # Link to issue
    lines.append(f"Closes #{issue_number}")
    lines.append("")

    # What was done
    if phases_summary:
        lines.append("### Pipeline phases")
        lines.append("")
        for phase in phases_summary:
            name = phase.get("phase", "unknown")
            cost = phase.get("cost_usd", 0) or 0
            duration = phase.get("duration_s", 0) or 0
            lines.append(f"- **{name}** — ${cost:.4f}, {duration:.0f}s")
        lines.append("")

    # Total cost from DB
    total_cost = _read_total_cost(run_db_path)
    if total_cost is not None:
        lines.append(f"**Total cost:** ${total_cost:.4f}")
        lines.append("")

    # Review findings
    if review_summary:
        lines.append("### Review findings")
        lines.append("")
        lines.append(review_summary)
        lines.append("")

    lines.append("---")
    lines.append("*Automated by simple_workflow*")

    return "\n".join(lines)


def _read_total_cost(db_path: str) -> float | None:
    """Read the most recent run's total spend from the DB.

    Returns None if the DB is missing or unreadable.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT spent_usd FROM pipeline_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return float(row["spent_usd"])
    except Exception:
        log.debug("could not read cost from %s", db_path, exc_info=True)
    return None
