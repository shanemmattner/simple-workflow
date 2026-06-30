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

    If the remote branch already exists and a normal push fails with
    non-fast-forward, retries with --force-with-lease.

    Raises PushFailed on auth/network errors, BranchNotFound if the
    branch doesn't exist locally.
    """
    result = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        capture_output=True, text=True, cwd=workspace_path, timeout=60,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "non-fast-forward" in stderr.lower() or "rejected" in stderr.lower():
            log.warning("Non-fast-forward on %s, retrying with --force-with-lease", branch)
            result = subprocess.run(
                ["git", "push", "--force-with-lease", "-u", "origin", branch],
                capture_output=True, text=True, cwd=workspace_path, timeout=60,
            )
            if result.returncode != 0:
                raise PushFailed(f"git push --force-with-lease failed: {result.stderr.strip()}")
        else:
            raise PushFailed(f"git push failed: {stderr}")


def create_pr(
    repo: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
    provider: str = "github",
    total_cost_usd: float | None = None,
) -> dict:
    """Create a GitHub PR (``gh pr create``) or GitLab MR (``glab mr create``).

    Returns ``{"number": int, "url": str}``.

    *total_cost_usd*, when provided, is the in-memory run cost at PR-creation
    time (the caller's ``spent`` accumulator). It is not embedded in the body
    here — the caller is expected to have already baked it into *body* via
    ``format_pr_body(..., total_cost_usd=...)`` — but it is logged so the
    run's cost-at-PR-time is visible even if the body formatting changes.

    Raises PRAlreadyExists if a PR/MR for *branch* already exists,
    RuntimeError on other failures.
    """
    log.info(
        "create_pr repo=%s branch=%s provider=%s total_cost_usd=%s",
        repo, branch, provider, total_cost_usd,
    )
    if provider == "gitlab":
        return _create_pr_gitlab(repo, branch, title, body, base)
    return _create_pr_github(repo, branch, title, body, base)


def _create_pr_github(repo: str, branch: str, title: str, body: str, base: str) -> dict:
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


def _create_pr_gitlab(repo: str, branch: str, title: str, body: str, base: str) -> dict:
    result = subprocess.run(
        [
            "glab", "mr", "create",
            "--repo", repo,
            "--source-branch", branch,
            "--target-branch", base,
            "--title", title,
            "--description", body,
            "--yes",
        ],
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "already exists" in stderr.lower():
            raise PRAlreadyExists(f"MR already exists for branch {branch!r}: {stderr}")
        raise RuntimeError(f"glab mr create failed: {stderr}")

    # glab prints progress lines and the MR url; the url is the last
    # line that looks like a URL.
    url = ""
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("http"):
            url = line
    if not url:
        url = result.stdout.strip()

    # Extract MR IID from URL (https://gitlab.com/owner/repo/-/merge_requests/123)
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
    notes: list[str] | None = None,
    total_cost_usd: float | None = None,
) -> str:
    """Build the PR description body.

    Parameters
    ----------
    issue_number:
        The GitHub issue this PR addresses.
    review_summary:
        Formatted review findings text (may be empty).
    run_db_path:
        Path to the runs.db SQLite database. Used as a fallback cost source
        when *total_cost_usd* is not provided (see below).
    phases_summary:
        List of dicts describing each phase/step that ran. Accepts either
        ``{"phase": str, "cost_usd": float, "duration_s": float}`` or the
        looser ``{"name": str, "cost": float, "result": str}`` shape —
        whichever keys are present are used, with sensible fallbacks.
    notes:
        Optional list of excerpts describing out-of-scope or discovered-but-
        not-fixed findings surfaced during execution. Rendered as a
        "### Discovered Issues" section when non-empty.
    total_cost_usd:
        The caller's in-memory running cost total (e.g. the ``spent``
        accumulator in ``run_domain_pipeline``), captured at PR-creation
        time. PR creation happens BEFORE ``finish_run()`` writes the final
        ``total_cost`` to the run DB, so reading from the DB here always
        returns the stale default (0.0). Pass this explicitly whenever the
        caller already tracks cost in memory. Falls back to
        ``_read_total_cost(run_db_path)`` when omitted, for backwards
        compatibility with callers that don't.
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
            name = phase.get("phase") or phase.get("name") or "unknown"
            cost = phase.get("cost_usd", phase.get("cost", 0)) or 0
            duration = phase.get("duration_s", 0) or 0
            result = phase.get("result")
            line = f"- **{name}** — ${cost:.4f}"
            if duration:
                line += f", {duration:.0f}s"
            if result:
                line += f" — {result}"
            lines.append(line)
        lines.append("")

    # Total cost: prefer the caller's in-memory value (accurate at PR-creation
    # time); fall back to the DB read for backwards-compat callers.
    total_cost = total_cost_usd if total_cost_usd is not None else _read_total_cost(run_db_path)
    if total_cost is not None:
        lines.append(f"**Total cost:** ${total_cost:.4f}")
        lines.append("")

    # Review findings
    if review_summary:
        lines.append("### Review findings")
        lines.append("")
        lines.append(review_summary)
        lines.append("")

    # Discovered out-of-scope issues
    if notes:
        lines.append("### Discovered Issues")
        lines.append("")
        for note in notes:
            lines.append(note)
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
            "SELECT total_cost FROM run ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return float(row["total_cost"])
    except Exception:
        log.debug("could not read cost from %s", db_path, exc_info=True)
    return None
