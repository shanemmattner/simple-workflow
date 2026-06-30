"""Fix phase — targeted repair from review findings.

Given a list of P0/P1 findings from the review phase, builds a focused
fix prompt and dispatches it to an agent.  Does NOT receive the original
plan — only the findings + standing rules — to prevent scope creep.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import runtime

log = logging.getLogger(__name__)


def _extract_findings(review_prose: str, cwd: str) -> list[dict]:
    """Use haiku to extract structured findings from review prose.

    Returns a list of dicts: {"severity": "critical"|"warning", "file": str,
    "line": int|null, "description": str}
    """
    prompt = """Extract ALL findings from this code review into a JSON array. Return ONLY valid JSON, no markdown fences, no explanation.

Schema: [{"severity": "critical" or "warning" or "info", "file": "path/to/file.ext", "line": null or integer, "description": "what is wrong and how to fix"}]

If no findings, return [].

Review text:
""" + review_prose

    resp = runtime.call_agent(prompt, model="haiku", cwd=cwd, max_turns=1)
    raw = resp["content"] if isinstance(resp["content"], str) else json.dumps(resp["content"])

    # Strip markdown fences if present
    if raw.strip().startswith("```"):
        raw = raw.strip().split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        findings = json.loads(raw)
        if not isinstance(findings, list):
            return []
        return findings
    except (json.JSONDecodeError, TypeError):
        log.warning("Failed to parse findings extraction: %s", raw[:200])
        return []


def _build_fix_prompt(findings: list[dict], branch: str) -> str:
    """Build a targeted fix prompt from structured findings."""
    lines = [
        f"You are fixing review findings on branch `{branch}`. The implementation is "
        "complete but the reviewer found issues that must be addressed before merge.",
        "",
        "## Review findings to fix",
        "",
    ]

    for i, f in enumerate(findings, 1):
        sev = f.get("severity", "unknown").upper()
        file = f.get("file", "unknown")
        line = f.get("line")
        loc = f"{file}:{line}" if line else file
        desc = f.get("description", "no description")
        lines.append(f"### Finding {i}: {sev} — {loc}")
        lines.append(desc)
        lines.append("")

    lines.extend([
        "## Rules",
        "- Fix ONLY the findings above. Do not refactor, do not add features.",
        "- Commit each fix separately with message: `fix(<scope>): address review finding — <description>`",
        "- If a finding is incorrect or cannot be fixed without broader changes, add a code comment explaining why.",
        "- After all fixes, verify the code compiles / passes lint if tooling is available.",
    ])

    return "\n".join(lines)


def run_fix(
    review_prose: str,
    *,
    cwd: str,
    branch: str,
    model: str = "sonnet",
    max_turns: int = 15,
) -> dict[str, Any]:
    """Run the fix phase: extract findings, dispatch fix agent.

    Returns:
        {"findings": [...], "fixable": [...], "response": <agent_resp>, "cost": float}
        If no fixable findings, response is None and cost is only the extraction cost.
    """
    # Extract structured findings (cheap haiku call)
    findings = _extract_findings(review_prose, cwd)
    extraction_cost = 0.001  # approximate haiku cost

    # Filter to P0/P1 (critical/warning)
    fixable = [f for f in findings if f.get("severity") in ("critical", "warning")]

    if not fixable:
        log.info("[fix] no fixable (critical/warning) findings — skipping fix phase")
        return {
            "findings": findings,
            "fixable": [],
            "response": None,
            "cost": extraction_cost,
        }

    # Build and dispatch fix prompt
    prompt = _build_fix_prompt(fixable, branch)
    log.info("[fix] dispatching fix agent for %d findings", len(fixable))
    resp = runtime.call_agent(prompt, model=model, cwd=cwd, max_turns=max_turns)
    log.info("[fix] %.1fs $%.4f", resp["duration_s"], resp["cost"])

    return {
        "findings": findings,
        "fixable": fixable,
        "response": resp,
        "cost": resp["cost"] + extraction_cost,
    }
