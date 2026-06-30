"""GitHub issue read/write operations via the gh CLI.

All subprocess calls that touch GitHub issues live here.
Returns plain dicts — no Pydantic models, no abstract bases.
"""

from __future__ import annotations

import json
import logging
import subprocess

log = logging.getLogger(__name__)


class GitHubCLIError(RuntimeError):
    """Raised when a gh subprocess exits non-zero."""


def _run_gh(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a gh CLI command and return the CompletedProcess.

    Raises GitHubCLIError on non-zero exit (includes stderr in the message).
    """
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise GitHubCLIError(
            "gh CLI not found. Install it: https://cli.github.com/"
        )
    except subprocess.TimeoutExpired as exc:
        raise GitHubCLIError(f"gh command timed out after {timeout}s: {exc}")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitHubCLIError(f"gh exited {result.returncode}: {stderr}")

    return result


def fetch_issue(repo: str, issue_number: int) -> dict:
    """Fetch a GitHub issue with its metadata, body, and comments.

    Parameters
    ----------
    repo : str
        Full repo slug, e.g. ``"owner/repo"``.
    issue_number : int
        Issue number.

    Returns
    -------
    dict with keys:
        title, body, state, labels, assignees, author, url, comments
    """
    # Fetch the issue itself
    fields = "title,body,state,labels,assignees,author,url"
    result = _run_gh([
        "issue", "view", str(issue_number),
        "--repo", repo,
        "--json", fields,
    ])
    data = json.loads(result.stdout)

    # Normalize nested objects to plain strings/lists
    issue: dict = {
        "title": data.get("title", ""),
        "body": data.get("body", ""),
        "state": data.get("state", ""),
        "labels": [lb.get("name", "") for lb in (data.get("labels") or [])],
        "assignees": [a.get("login", "") for a in (data.get("assignees") or [])],
        "author": (data.get("author") or {}).get("login", ""),
        "url": data.get("url", ""),
    }

    # Fetch comments separately (gh issue view --json comments)
    try:
        comments_result = _run_gh([
            "issue", "view", str(issue_number),
            "--repo", repo,
            "--json", "comments",
        ])
        raw_comments = json.loads(comments_result.stdout).get("comments") or []
        issue["comments"] = [
            {
                "author": (c.get("author") or {}).get("login", ""),
                "body": c.get("body", ""),
                "created_at": c.get("createdAt", ""),
            }
            for c in raw_comments
        ]
    except GitHubCLIError:
        log.warning("Failed to fetch comments for %s#%d", repo, issue_number)
        issue["comments"] = []

    return issue


def post_comment(repo: str, issue_number: int, body: str) -> None:
    """Post a comment on a GitHub issue.

    Parameters
    ----------
    repo : str
        Full repo slug, e.g. ``"owner/repo"``.
    issue_number : int
        Issue number.
    body : str
        Comment body (Markdown).
    """
    _run_gh([
        "issue", "comment", str(issue_number),
        "--repo", repo,
        "--body", body,
    ])


def update_labels(
    repo: str,
    issue_number: int,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> None:
    """Add and/or remove labels on a GitHub issue.

    Parameters
    ----------
    repo : str
        Full repo slug, e.g. ``"owner/repo"``.
    issue_number : int
        Issue number.
    add : list[str] | None
        Labels to add.
    remove : list[str] | None
        Labels to remove.
    """
    if add:
        _run_gh([
            "issue", "edit", str(issue_number),
            "--repo", repo,
            "--add-label", ",".join(add),
        ])

    if remove:
        _run_gh([
            "issue", "edit", str(issue_number),
            "--repo", repo,
            "--remove-label", ",".join(remove),
        ])


def create_issue(
    repo: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> str:
    """Create a new GitHub issue via ``gh issue create``.

    If creation with labels fails (e.g. the labels don't exist on the
    target repo), retries once without labels before giving up.

    Returns the new issue's URL.

    Raises GitHubCLIError if both attempts fail.
    """
    args = ["issue", "create", "--repo", repo, "--title", title, "--body", body]
    for label in labels or []:
        args += ["--label", label]

    try:
        result = _run_gh(args, timeout=30)
        return result.stdout.strip()
    except GitHubCLIError as exc:
        if not labels:
            raise
        log.warning(
            "issue create failed for %s (labels=%s), retrying without labels: %s",
            repo, labels, exc,
        )
        result = _run_gh(
            ["issue", "create", "--repo", repo, "--title", title, "--body", body],
            timeout=30,
        )
        return result.stdout.strip()


def find_open_prs_for_issue(repo: str, issue_number: int) -> list[dict]:
    """Search for open PRs referencing *issue_number* in *repo*.

    Returns a list of dicts with keys: number, title, url. Empty list if
    none found or the search itself fails (treated as "no PR found").
    """
    try:
        result = _run_gh([
            "pr", "list",
            "--repo", repo,
            "--search", f"issue {issue_number}",
            "--state", "open",
            "--json", "number,title,url",
        ], timeout=15)
    except GitHubCLIError:
        log.warning("Failed to search for existing PRs on %s issue #%d", repo, issue_number)
        return []

    try:
        return json.loads(result.stdout) or []
    except json.JSONDecodeError:
        return []
