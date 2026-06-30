"""Issue read/write operations via the gh (GitHub) or glab (GitLab) CLI.

All subprocess calls that touch issues live here.
Returns plain dicts — no Pydantic models, no abstract bases.

Every public function takes a ``provider`` kwarg ("github" or "gitlab",
default "github") and dispatches to a `_<func>_github` / `_<func>_gitlab`
pair. Existing callers that don't pass `provider` are unaffected.
"""

from __future__ import annotations

import json
import logging
import subprocess

log = logging.getLogger(__name__)


class GitHubCLIError(RuntimeError):
    """Raised when a gh subprocess exits non-zero."""


class GitLabCLIError(RuntimeError):
    """Raised when a glab subprocess exits non-zero."""


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


def _run_glab(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a glab CLI command and return the CompletedProcess.

    Raises GitLabCLIError on non-zero exit (includes stderr in the message).
    """
    try:
        result = subprocess.run(
            ["glab", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise GitLabCLIError(
            "glab CLI not found. Install it: https://gitlab.com/gitlab-org/cli"
        )
    except subprocess.TimeoutExpired as exc:
        raise GitLabCLIError(f"glab command timed out after {timeout}s: {exc}")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise GitLabCLIError(f"glab exited {result.returncode}: {stderr}")

    return result


_EMPTY_ISSUE: dict = {
    "title": "",
    "body": "",
    "state": "",
    "labels": [],
    "assignees": [],
    "author": "",
    "url": "",
    "comments": [],
}


def fetch_issue(repo: str | None, issue_number: int | None, provider: str = "github") -> dict:
    """Fetch an issue with its metadata, body, and comments.

    Parameters
    ----------
    repo : str | None
        Full repo slug, e.g. ``"owner/repo"``. May be ``None`` when no issue
        is being fetched (``issue_number`` is also ``None`` in that case).
    issue_number : int | None
        Issue number. If ``None``, no fetch is performed and an empty issue
        dict is returned (the pipeline runs without issue context — the
        task is defined entirely by the workflow's own prompts).
    provider : str
        "github" (default) or "gitlab".

    Returns
    -------
    dict with keys:
        title, body, state, labels, assignees, author, url, comments
    """
    if issue_number is None:
        log.info("fetch_issue: issue_number is None — returning empty issue context")
        return dict(_EMPTY_ISSUE)
    if provider == "gitlab":
        return _fetch_issue_gitlab(repo, issue_number)
    return _fetch_issue_github(repo, issue_number)


def _fetch_issue_github(repo: str, issue_number: int) -> dict:
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


def _fetch_issue_gitlab(repo: str, issue_number: int) -> dict:
    # `glab issue view --output json` does not expose notes/comments in its
    # JSON payload (verified 2026-06-30 against gitlab.com), so comments are
    # always returned empty for the gitlab provider. State, labels, and the
    # rest map cleanly from the GitLab issue schema.
    result = _run_glab([
        "issue", "view", str(issue_number),
        "--repo", repo,
        "--output", "json",
    ])
    data = json.loads(result.stdout)

    issue: dict = {
        "title": data.get("title", ""),
        "body": data.get("description", ""),
        "state": data.get("state", ""),
        "labels": data.get("labels") or [],
        "assignees": [a.get("username", "") for a in (data.get("assignees") or [])],
        "author": (data.get("author") or {}).get("username", ""),
        "url": data.get("web_url", ""),
        "comments": [],
    }
    return issue


def post_comment(repo: str, issue_number: int, body: str, provider: str = "github") -> None:
    """Post a comment on an issue.

    Parameters
    ----------
    repo : str
        Full repo slug, e.g. ``"owner/repo"``.
    issue_number : int
        Issue number.
    body : str
        Comment body (Markdown).
    provider : str
        "github" (default) or "gitlab".
    """
    if provider == "gitlab":
        return _post_comment_gitlab(repo, issue_number, body)
    return _post_comment_github(repo, issue_number, body)


def _post_comment_github(repo: str, issue_number: int, body: str) -> None:
    _run_gh([
        "issue", "comment", str(issue_number),
        "--repo", repo,
        "--body", body,
    ])


def _post_comment_gitlab(repo: str, issue_number: int, body: str) -> None:
    _run_glab([
        "issue", "note", str(issue_number),
        "--repo", repo,
        "--message", body,
    ])


def update_labels(
    repo: str,
    issue_number: int,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    provider: str = "github",
) -> None:
    """Add and/or remove labels on an issue.

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
    provider : str
        "github" (default) or "gitlab".
    """
    if provider == "gitlab":
        return _update_labels_gitlab(repo, issue_number, add, remove)
    return _update_labels_github(repo, issue_number, add, remove)


def _update_labels_github(
    repo: str,
    issue_number: int,
    add: list[str] | None,
    remove: list[str] | None,
) -> None:
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


def _update_labels_gitlab(
    repo: str,
    issue_number: int,
    add: list[str] | None,
    remove: list[str] | None,
) -> None:
    if add:
        _run_glab([
            "issue", "update", str(issue_number),
            "--repo", repo,
            "--label-add", ",".join(add),
        ])

    if remove:
        _run_glab([
            "issue", "update", str(issue_number),
            "--repo", repo,
            "--label-remove", ",".join(remove),
        ])


def create_issue(
    repo: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
    provider: str = "github",
) -> str:
    """Create a new issue.

    If creation with labels fails (e.g. the labels don't exist on the
    target repo), retries once without labels before giving up.

    Returns the new issue's URL.

    Raises GitHubCLIError / GitLabCLIError if both attempts fail.
    """
    if provider == "gitlab":
        return _create_issue_gitlab(repo, title, body, labels)
    return _create_issue_github(repo, title, body, labels)


def _create_issue_github(
    repo: str,
    title: str,
    body: str,
    labels: list[str] | None,
) -> str:
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


def _create_issue_gitlab(
    repo: str,
    title: str,
    body: str,
    labels: list[str] | None,
) -> str:
    args = ["issue", "create", "--repo", repo, "--title", title, "--description", body, "--yes"]
    for label in labels or []:
        args += ["--label", label]

    try:
        result = _run_glab(args, timeout=30)
        return result.stdout.strip()
    except GitLabCLIError as exc:
        if not labels:
            raise
        log.warning(
            "glab issue create failed for %s (labels=%s), retrying without labels: %s",
            repo, labels, exc,
        )
        result = _run_glab(
            ["issue", "create", "--repo", repo, "--title", title, "--description", body, "--yes"],
            timeout=30,
        )
        return result.stdout.strip()


def find_open_prs_for_issue(repo: str, issue_number: int, provider: str = "github") -> list[dict]:
    """Search for open PRs (or MRs, for gitlab) referencing *issue_number* in *repo*.

    Returns a list of dicts with keys: number, title, url. Empty list if
    none found or the search itself fails (treated as "no PR found").
    """
    if provider == "gitlab":
        return _find_open_prs_for_issue_gitlab(repo, issue_number)
    return _find_open_prs_for_issue_github(repo, issue_number)


def _find_open_prs_for_issue_github(repo: str, issue_number: int) -> list[dict]:
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


def _find_open_prs_for_issue_gitlab(repo: str, issue_number: int) -> list[dict]:
    try:
        result = _run_glab([
            "mr", "list",
            "--repo", repo,
            "--search", f"issue {issue_number}",
            "--output", "json",
        ], timeout=15)
    except GitLabCLIError:
        log.warning("Failed to search for existing MRs on %s issue #%d", repo, issue_number)
        return []

    try:
        raw = json.loads(result.stdout) or []
    except json.JSONDecodeError:
        return []

    return [
        {
            "number": mr.get("iid", 0),
            "title": mr.get("title", ""),
            "url": mr.get("web_url", ""),
        }
        for mr in raw
        if mr.get("state") == "opened"
    ]
