"""Validate phase helpers — poll for Vercel preview URL readiness.

Used by orchestrator.py to find and wait for the Vercel preview deployment
before launching the validate agent.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time

log = logging.getLogger(__name__)

# Max time to wait for preview URL (seconds)
PREVIEW_TIMEOUT_S = 300  # 5 minutes
POLL_INTERVAL_S = 15


def get_preview_url(repo: str, pr_number: int, *, timeout: int = PREVIEW_TIMEOUT_S) -> str | None:
    """Poll PR checks for a Vercel preview URL. Returns URL or None on timeout.

    Tries two strategies:
    1. `gh pr checks` — looks for a Vercel check with a URL
    2. `gh api` — queries the deployments/statuses API for a preview URL

    Args:
        repo: owner/repo string
        pr_number: PR number
        timeout: max seconds to wait (default 300)

    Returns:
        Preview URL string, or None if not found within timeout
    """
    deadline = time.monotonic() + timeout
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        log.info("[validate] polling for preview URL (attempt %d, %.0fs remaining)",
                 attempt, deadline - time.monotonic())

        # Strategy 1: gh pr checks
        url = _check_pr_checks(repo, pr_number)
        if url:
            log.info("[validate] preview URL found via pr checks: %s", url)
            return url

        # Strategy 2: gh api deployments
        url = _check_deployments_api(repo, pr_number)
        if url:
            log.info("[validate] preview URL found via deployments API: %s", url)
            return url

        remaining = deadline - time.monotonic()
        if remaining > POLL_INTERVAL_S:
            log.info("[validate] no preview URL yet, sleeping %ds", POLL_INTERVAL_S)
            time.sleep(POLL_INTERVAL_S)
        else:
            break

    log.warning("[validate] preview URL not found after %ds (%d attempts)", timeout, attempt)
    return None


def _check_pr_checks(repo: str, pr_number: int) -> str | None:
    """Extract Vercel preview URL from `gh pr checks` output."""
    try:
        result = subprocess.run(
            ["gh", "pr", "checks", str(pr_number), "--repo", repo],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.debug("[validate] gh pr checks failed: %s", result.stderr.strip()[:200])
            return None

        for line in result.stdout.splitlines():
            lower = line.lower()
            if "vercel" not in lower:
                continue
            # Extract URL from the check line
            url = _extract_url(line)
            if url and _is_preview_url(url):
                return url

    except subprocess.TimeoutExpired:
        log.warning("[validate] gh pr checks timed out")
    except Exception as e:
        log.warning("[validate] gh pr checks error: %s", e)

    return None


def _check_deployments_api(repo: str, pr_number: int) -> str | None:
    """Query GitHub API for deployment statuses with a preview URL."""
    try:
        # Get the PR's head SHA
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo,
             "--json", "headRefOid", "--jq", ".headRefOid"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        sha = result.stdout.strip()

        # Get deployment statuses for the head commit
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/deployments",
             "--jq", '.[].id'],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None

        for dep_id in result.stdout.strip().splitlines():
            dep_id = dep_id.strip()
            if not dep_id:
                continue
            status_result = subprocess.run(
                ["gh", "api", f"repos/{repo}/deployments/{dep_id}/statuses",
                 "--jq", '.[0] | select(.state == "success") | .environment_url // .target_url'],
                capture_output=True, text=True, timeout=30,
            )
            url = status_result.stdout.strip()
            if url and _is_preview_url(url):
                return url

    except subprocess.TimeoutExpired:
        log.warning("[validate] deployments API timed out")
    except Exception as e:
        log.warning("[validate] deployments API error: %s", e)

    return None


def _extract_url(text: str) -> str | None:
    """Extract the first HTTPS URL from a line of text."""
    match = re.search(r'https://[^\s\t]+', text)
    if match:
        return match.group(0).rstrip(')')
    return None


def _is_preview_url(url: str) -> bool:
    """Check if a URL looks like a Vercel preview deployment."""
    lower = url.lower()
    # Common Vercel preview patterns
    if '.vercel.app' in lower:
        return True
    if 'vercel' in lower and ('preview' in lower or '-git-' in lower):
        return True
    return False


def check_has_ui_changes(triage_output: str, issue_body: str) -> bool:
    """Heuristic: does the issue/triage suggest UI-visible changes?

    Returns True if validation is likely useful. False for backend-only,
    config-only, or non-web changes.

    This is deliberately permissive — false positives are better than
    false negatives for validation.
    """
    combined = (triage_output + " " + issue_body).lower()

    # Positive signals: UI-related terms
    ui_signals = [
        "page", "component", "button", "form", "modal", "dialog",
        "dashboard", "layout", "css", "style", "render", "display",
        "ui", "frontend", "view", "screen", "navigation", "route",
        ".tsx", ".jsx", ".css", ".scss", "tailwind",
        "next.js", "react", "html", "template",
    ]

    # Negative signals: backend-only terms
    backend_only_signals = [
        "migration", "schema", "api only", "backend only",
        "cron", "webhook handler", "database", "sql",
    ]

    has_ui = any(s in combined for s in ui_signals)
    is_backend_only = any(s in combined for s in backend_only_signals) and not has_ui

    return has_ui and not is_backend_only
