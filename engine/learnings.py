"""
Learning capture from improve-phase retrospectives.
Appends structured learnings to runs/learnings.jsonl for injection into future runs.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEARNINGS_FILE = Path(__file__).parent.parent / "runs" / "learnings.jsonl"


def capture_learnings(improve_output: dict[str, Any], run_id: str, repo: str, issue_number: int) -> int:
    """
    Parse improve_output and append structured learnings to runs/learnings.jsonl.
    Returns the number of learnings written.
    """
    # Extract learnings from the improve output fields
    learnings = []
    ts = datetime.now(timezone.utc).isoformat()

    base = {
        "run_id": run_id,
        "repo": repo,
        "issue_number": issue_number,
        "timestamp": ts,
        "overall_score": improve_output.get("overall_score"),
    }

    # Each recommendation becomes a learning
    for rec in improve_output.get("recommendations", []):
        learnings.append({**base, "type": "recommendation", "text": rec})

    # Each context gap becomes a learning
    for gap in improve_output.get("context_gaps", []):
        learnings.append({**base, "type": "context_gap", "text": gap})

    # Each code quality issue becomes a learning
    for issue in improve_output.get("code_quality_issues", []):
        learnings.append({**base, "type": "code_quality", "text": issue})

    # Summary as a single learning
    if improve_output.get("summary"):
        learnings.append({**base, "type": "summary", "text": improve_output["summary"]})

    if not learnings:
        return 0

    LEARNINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LEARNINGS_FILE, "a") as f:
        for learning in learnings:
            f.write(json.dumps(learning) + "\n")

    return len(learnings)


def get_recent_learnings(n: int = 10) -> list[dict[str, Any]]:
    """Return the last N learnings from runs/learnings.jsonl."""
    if not LEARNINGS_FILE.exists():
        return []

    lines = LEARNINGS_FILE.read_text().strip().splitlines()
    recent = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            recent.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(recent) >= n:
            break
    return list(reversed(recent))


def format_learnings_for_prompt(learnings: list[dict[str, Any]]) -> str:
    """Format learnings as a concise block for prompt injection."""
    if not learnings:
        return "No prior learnings available."

    lines = []
    for l in learnings:
        ts = l.get("timestamp", "")[:10]  # just the date
        ltype = l.get("type", "note")
        text = l.get("text", "")
        repo = l.get("repo", "")
        lines.append(f"[{ts}] [{ltype}] ({repo}): {text}")

    return "\n".join(lines)
