"""Central sequencer for the github_claude engine.

Wires source -> storage -> workspace -> runtime -> destination.
Reads workflow.yaml for phase config (models, max_turns).

Usage:
    python -m engine owner/repo 123 [--budget 2.00] [--model opus]
"""
from __future__ import annotations

import argparse, glob, json, logging, os, re, sqlite3, subprocess, sys
from dataclasses import dataclass, field
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_EXCEPTION
from pathlib import Path
from typing import Any

import yaml
from . import source, runtime, storage, workspace, destination, gates, validate
from .learnings import capture_learnings, get_recent_learnings, format_learnings_for_prompt

log = logging.getLogger(__name__)
WORKFLOW_DIR = Path(__file__).resolve().parent.parent / "workflows" / "issue-to-pr"
WORKFLOWS_ROOT = Path(__file__).resolve().parent.parent / "workflows"


# ---------------------------------------------------------------------------
# Step-by-step execution: dataclass, parser, helpers
# ---------------------------------------------------------------------------

@dataclass
class TriageStep:
    number: int
    title: str
    files: list[str]
    changes: str
    verify: str
    depends_on: list[int]
    raw_text: str  # full markdown block for this step


# Primary regex: match ### Step N: Title
_STEP_HEADER_RE = re.compile(
    r"^###\s+Step\s+(\d+)\s*:\s*(.+)$",
    re.MULTILINE,
)

# Field extractors (within a step block)
_FILES_RE = re.compile(r"^\*\*Files?:\*\*\s*(.+)$", re.MULTILINE)
_CHANGES_RE = re.compile(r"^\*\*Changes?:\*\*\s*(.+(?:\n(?!\*\*).+)*)$", re.MULTILINE)
_VERIFY_RE = re.compile(r"^\*\*Verify:\*\*\s*(.+(?:\n(?!\*\*).+)*)$", re.MULTILINE)
_DEPENDS_RE = re.compile(r"^\*\*Depends?\s*on:\*\*\s*(.+)$", re.MULTILINE)


def _parse_triage_steps(triage_text: str) -> list[TriageStep]:
    """Parse numbered steps from triage output.

    Returns an empty list if no steps found (single-step issue or
    triage didn't use the step format). The orchestrator falls back
    to monolithic execute in that case.
    """
    headers = list(_STEP_HEADER_RE.finditer(triage_text))
    if not headers:
        return []

    steps: list[TriageStep] = []
    for i, match in enumerate(headers):
        number = int(match.group(1))
        title = match.group(2).strip()

        # Extract the block between this header and the next (or end)
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(triage_text)
        block = triage_text[start:end]

        # Parse fields
        files_match = _FILES_RE.search(block)
        files: list[str] = []
        if files_match:
            raw_files = files_match.group(1).strip()
            # Handle comma-separated and newline-separated file lists
            files = [f.strip().strip("`") for f in re.split(r"[,\n]", raw_files) if f.strip()]

        changes_match = _CHANGES_RE.search(block)
        changes = changes_match.group(1).strip() if changes_match else ""

        verify_match = _VERIFY_RE.search(block)
        verify = verify_match.group(1).strip() if verify_match else ""

        depends_match = _DEPENDS_RE.search(block)
        depends_on: list[int] = []
        if depends_match:
            dep_text = depends_match.group(1).strip().lower()
            if dep_text not in ("none", "n/a", "-", ""):
                # Extract step numbers: "Step 1", "Step 1, Step 2", "1, 2"
                depends_on = [int(d) for d in re.findall(r"\d+", dep_text)]

        steps.append(TriageStep(
            number=number,
            title=title,
            files=files,
            changes=changes,
            verify=verify,
            depends_on=depends_on,
            raw_text=triage_text[match.start():end].strip(),
        ))

    log.info("_parse_triage_steps: found %d steps", len(steps))
    return steps


def _load_step_prompt(wf_dir: Path) -> str:
    """Load the per-step execute prompt. Falls back to execute.md if absent."""
    step_path = wf_dir / "prompts" / "execute-step.md"
    if step_path.is_file():
        return step_path.read_text()
    # Fallback: wrap the monolithic execute.md with step context header
    log.warning("no execute-step.md found in %s — using execute.md with step header", wf_dir)
    execute_prompt = (wf_dir / "prompts" / "execute.md").read_text()
    header = (
        "## IMPORTANT: You are executing step {step_number} of {total_steps} ONLY.\n\n"
        "**This step:** {step_title}\n"
        "**Target files:** {step_files}\n"
        "**What to change:** {step_changes}\n\n"
        "Focus on this step only. Do not implement other steps.\n\n---\n\n"
    )
    return header + execute_prompt


def _commit_step(
    wt: str,
    step_num: int,
    step_title: str,
    issue_number: int | None,
    workflow: str,
) -> list[str]:
    """Git add + commit changes from a single step. Returns list of changed files.

    Returns empty list if the step made no changes (no-op step).
    """
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=wt, capture_output=True, text=True,
    ).stdout.strip()

    if not porcelain:
        log.info("[commit] step %d made no changes", step_num)
        return []

    # Parse changed files from porcelain output
    files = [line[3:].strip().strip('"') for line in porcelain.splitlines() if line.strip()]

    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)

    # Sanitize title for commit message (no issue refs from triage)
    safe_title = step_title[:60]
    issue_suffix = f" (#{issue_number})" if issue_number is not None else ""
    msg = f"feat(step-{step_num}): {safe_title}{issue_suffix}"

    result = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=wt, capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Commit failed — likely pre-commit hook
        log.warning("[commit] step %d commit failed: %s", step_num, result.stderr[:300])
        return []

    log.info("[commit] step %d committed: %d files", step_num, len(files))
    return files


def _execute_steps(
    steps: list[TriageStep],
    *,
    conn: sqlite3.Connection,
    wf_dir: Path,
    wt: str,
    execute_cfg: dict,
    execute_model: str,
    issue_number: int | None,
    issue_body: str,
    triage_text: str,
    repo_context: str,
    recent_learnings: str,
    workflow: str,
    budget: float,
    spent: float,
) -> tuple[float, list[dict]]:
    """Execute triage steps one at a time, committing after each.

    Returns (updated_spent, step_results).
    Raises on unrecoverable failure.
    """
    step_prompt_template = _load_step_prompt(wf_dir)
    step_results: list[dict] = []

    for step in steps:
        step_num = step.number
        phase_name = f"execute-step-{step_num}"

        log.info("[domain/execute] step %d/%d start: %s",
                 step_num, len(steps), step.title)

        # Build per-step context: prior steps' summaries (not full triage)
        prior_step_summaries = ""
        for prev in step_results:
            prior_step_summaries += (
                f"\n### Step {prev['step_number']} result: {prev['status']}\n"
                f"Files changed: {', '.join(prev.get('files_changed', []))}\n"
                f"Summary: {prev.get('summary', 'completed')}\n"
            )

        # Render per-step prompt
        step_prompt = step_prompt_template.replace("{step_number}", str(step_num))
        step_prompt = step_prompt.replace("{step_title}", step.title)
        step_prompt = step_prompt.replace("{step_files}", ", ".join(step.files) or "as needed")
        step_prompt = step_prompt.replace("{step_changes}", step.changes)
        step_prompt = step_prompt.replace("{step_verify}", step.verify)
        step_prompt = step_prompt.replace("{prior_steps}", prior_step_summaries or "This is the first step.")
        step_prompt = step_prompt.replace("{issue_number}", str(issue_number) if issue_number is not None else "N/A")
        step_prompt = step_prompt.replace("{issue_body}", issue_body)
        step_prompt = step_prompt.replace("{repo_context}", repo_context)
        step_prompt = step_prompt.replace("{recent_learnings}", recent_learnings or "No prior learnings available.")
        step_prompt = step_prompt.replace("{total_steps}", str(len(steps)))

        # SQLite: start phase
        pid = _start_phase(conn, phase_name, model=execute_model)

        # Call agent with per-step timeout (300s = 5 min)
        resp = runtime.call_agent(
            step_prompt,
            model=execute_model,
            cwd=wt,
            max_turns=execute_cfg.get("max_turns", 15),
            timeout=300,
        )
        resp["_prompt"] = step_prompt

        finish = resp.get("finish_reason", "unknown")
        step_cost = resp.get("cost", 0)
        step_duration = resp.get("duration_s", 0)
        spent += step_cost

        # Commit changes from this step
        files_changed = _commit_step(wt, step_num, step.title, issue_number, workflow)

        # Determine step status
        if finish == "error":
            status = "failed"
            log.error("[domain/execute] step %d failed: %s",
                      step_num, str(resp.get("content", ""))[:300])
        elif finish == "timeout":
            status = "timeout"
            log.warning("[domain/execute] step %d timed out", step_num)
        else:
            status = "completed" if files_changed else "no_changes"

        _finish_phase(conn, pid, resp, failed=(status == "failed"))

        # Log step event
        step_result = {
            "step_number": step_num,
            "title": step.title,
            "status": status,
            "cost": step_cost,
            "duration_s": step_duration,
            "files_changed": files_changed,
            "summary": _content(resp)[:500] if resp.get("content") else "",
        }
        storage.log_event(conn, "step_complete", step_result)
        step_results.append(step_result)

        log.info("[domain/execute] step %d/%d %s cost=$%.4f duration=%.1fs files=%d",
                 step_num, len(steps), status, step_cost, step_duration, len(files_changed))

        # Failure handling
        if status == "failed":
            # Retry once
            log.info("[domain/execute] retrying step %d", step_num)
            pid2 = _start_phase(conn, f"{phase_name}-retry", model=execute_model)
            retry_resp = runtime.call_agent(
                step_prompt,
                model=execute_model,
                cwd=wt,
                max_turns=execute_cfg.get("max_turns", 15),
                timeout=300,
            )
            retry_resp["_prompt"] = step_prompt
            retry_cost = retry_resp.get("cost", 0)
            spent += retry_cost

            retry_files = _commit_step(wt, step_num, step.title, issue_number, workflow)
            retry_finish = retry_resp.get("finish_reason", "unknown")
            retry_status = "completed" if retry_files else ("failed" if retry_finish == "error" else "no_changes")

            _finish_phase(conn, pid2, retry_resp, failed=(retry_status == "failed"))

            if retry_status == "failed":
                # Halt pipeline — prior steps' commits are preserved
                storage.log_event(conn, "step_halt", {
                    "step_number": step_num,
                    "reason": "retry_failed",
                })
                raise RuntimeError(
                    f"Step {step_num} failed after retry: {step.title}. "
                    f"Prior {len(step_results)-1} steps committed successfully."
                )
            else:
                step_results[-1] = {
                    **step_results[-1],
                    "status": retry_status,
                    "cost": step_cost + retry_cost,
                    "files_changed": retry_files,
                    "retried": True,
                }

        elif status == "timeout":
            # Timeout: halt (do not retry — stall likely to recur)
            storage.log_event(conn, "step_halt", {
                "step_number": step_num,
                "reason": "timeout",
            })
            raise RuntimeError(
                f"Step {step_num} timed out: {step.title}. "
                f"Prior {len(step_results)-1} steps committed successfully."
            )

        # Budget check after each step
        if spent > budget:
            log.warning("budget exceeded after step %d ($%.2f > $%.2f) — continuing to review",
                        step_num, spent, budget)
            break

    return spent, step_results


def _execute_ops_steps(
    steps: list[TriageStep],
    *,
    conn: sqlite3.Connection,
    wf_dir: Path,
    cwd: str,
    execute_cfg: dict,
    execute_model: str,
    task_description: str,
    triage_text: str,
    out_path: Path,
) -> tuple[float, list[dict]]:
    """Execute ops steps one at a time, appending to the output file.

    Each step appends its output to out_path (the final deliverable).
    """
    step_prompt_template = _load_step_prompt(wf_dir)
    step_results: list[dict] = []
    spent = 0.0

    # Initialize the output file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(f"# {task_description}\n\n")

    for step in steps:
        step_num = step.number
        phase_name = f"execute-step-{step_num}"

        # Build per-step context
        prior_step_summaries = ""
        for prev in step_results:
            prior_step_summaries += (
                f"\n### Step {prev['step_number']} result: {prev['status']}\n"
                f"Summary: {prev.get('summary', 'completed')}\n"
            )

        # Render prompt (ops steps use {task_description} instead of {issue_body})
        step_prompt = step_prompt_template.replace("{step_number}", str(step_num))
        step_prompt = step_prompt.replace("{step_title}", step.title)
        step_prompt = step_prompt.replace("{step_files}", ", ".join(step.files) or "as needed")
        step_prompt = step_prompt.replace("{step_changes}", step.changes)
        step_prompt = step_prompt.replace("{step_verify}", step.verify)
        step_prompt = step_prompt.replace("{prior_steps}", prior_step_summaries or "This is the first step.")
        step_prompt = step_prompt.replace("{issue_body}", task_description)
        step_prompt = step_prompt.replace("{issue_number}", "0")
        step_prompt = step_prompt.replace("{repo_context}", "")
        step_prompt = step_prompt.replace("{recent_learnings}", "No prior learnings available.")
        step_prompt = step_prompt.replace("{total_steps}", str(len(steps)))

        pid = _start_phase(conn, phase_name, model=execute_model)

        resp = runtime.call_agent(
            step_prompt,
            model=execute_model,
            cwd=cwd,
            max_turns=execute_cfg.get("max_turns", 15),
            timeout=300,
        )
        resp["_prompt"] = step_prompt

        step_cost = resp.get("cost", 0)
        spent += step_cost
        step_text = _content(resp)

        # Append step output to the deliverable file
        with open(out_path, "a") as f:
            f.write(f"\n\n## Step {step_num}: {step.title}\n\n{step_text}\n")

        status = "completed" if resp.get("finish_reason") == "end_turn" else resp.get("finish_reason", "unknown")
        _finish_phase(conn, pid, resp, failed=(status == "failed"))

        step_result = {
            "step_number": step_num,
            "title": step.title,
            "status": status,
            "cost": step_cost,
            "duration_s": resp.get("duration_s", 0),
            "output_len": len(step_text),
            "summary": step_text[:500],
        }
        storage.log_event(conn, "step_complete", step_result)
        step_results.append(step_result)

        log.info("[ops/execute] step %d/%d %s cost=$%.4f",
                 step_num, len(steps), status, step_cost)

    return spent, step_results


def _load_repo_context(worktree_path: str) -> str:
    """Read .workflows/ context files from the target repo checkout.

    Returns a formatted string with context.md, testing.md, and knowledge/*.md
    contents. Returns empty string if .workflows/ doesn't exist.
    """
    wf_dir = Path(worktree_path) / ".workflows"
    if not wf_dir.is_dir():
        return ""

    sections: list[str] = []

    context_file = wf_dir / "context.md"
    if context_file.is_file():
        sections.append(f"## Repo Context\n\n{context_file.read_text()}")

    testing_file = wf_dir / "testing.md"
    if testing_file.is_file():
        sections.append(f"## Testing\n\n{testing_file.read_text()}")

    knowledge_dir = wf_dir / "knowledge"
    if knowledge_dir.is_dir():
        knowledge_parts: list[str] = []
        for md in sorted(knowledge_dir.glob("*.md")):
            knowledge_parts.append(f"### {md.name}\n{md.read_text()}")
        if knowledge_parts:
            sections.append("## Domain Knowledge\n\n" + "\n\n".join(knowledge_parts))

    return "\n\n".join(sections)


def _resolve_workflow_dir(workflow: str | None) -> Path:
    """Resolve a workflow name to its directory path.

    If workflow is None, returns the default issue-to-pr WORKFLOW_DIR.
    If workflow is a name like "shftty-web", returns WORKFLOWS_ROOT / workflow.
    Raises FileNotFoundError if the resolved directory does not exist.
    """
    if workflow is None:
        return WORKFLOW_DIR
    resolved = WORKFLOWS_ROOT / workflow
    if not resolved.is_dir():
        raise FileNotFoundError(
            f"Workflow directory not found: {resolved}  "
            f"(looked for '{workflow}' under {WORKFLOWS_ROOT})"
        )
    log.info("_resolve_workflow_dir: workflow=%s resolved=%s", workflow, resolved)
    return resolved


def _parse_triage_signal(text: str) -> str:
    """Parse triage output for PROCEED/SKIP/ESCALATE keywords.

    Strategy (priority order):
    1. Look for the signal on its own line immediately after a '## Decision' header
       (case-insensitive). This is the canonical structured format.
    2. Fall back to the signal appearing as the sole word on its own line
       (not embedded mid-sentence). Prevents "this will FAIL in CI" false-matches.

    Returns the first signal found (ESCALATE > SKIP > PROCEED).
    Defaults to PROCEED if none found.
    """
    # Strategy 1: structured header match — "## Decision\nPROCEED" (trailing text allowed)
    header_match = re.search(
        r"^##\s*decision\s*\n+\s*(PROCEED|SKIP|ESCALATE)\b",
        text, re.IGNORECASE | re.MULTILINE,
    )
    if header_match:
        signal = header_match.group(1).upper()
        log.info("_parse_triage_signal: header match signal=%s", signal)
        return signal

    # Strategy 2: signal as the start of its own line (priority: ESCALATE > SKIP > PROCEED)
    # Allows trailing text, e.g. "SKIP: Already implemented in submodule bump"
    for signal in ("ESCALATE", "SKIP", "PROCEED"):
        if re.search(rf"^\s*{signal}\b", text, re.IGNORECASE | re.MULTILINE):
            log.info("_parse_triage_signal: standalone-line match signal=%s", signal)
            return signal

    log.info("_parse_triage_signal: no explicit signal found — defaulting to PROCEED")
    return "PROCEED"


def _parse_review_signal(text: str) -> str:
    """Parse review output for FAIL/WARN/PASS keywords.

    Strategy (priority order):
    1. Look for the signal on its own line immediately after a '## Verdict' header
       (case-insensitive). This is the canonical structured format.
    2. Fall back to the signal appearing as the sole word on its own line
       (not embedded mid-sentence). Prevents "this will FAIL in CI" false-matches.

    Returns the first signal found (FAIL > WARN > PASS).
    Defaults to PASS if none found, EXCEPT an empty/blank response fails closed (FAIL) —
    an empty review means the phase produced nothing to evaluate, not an implicit pass.
    """
    if text.strip() == "":
        log.warning("_parse_review_signal: empty review text — failing closed (FAIL)")
        return "FAIL"

    # Strategy 1: structured header match — "## Verdict\nPASS" (trailing text allowed)
    header_match = re.search(
        r"^##\s*verdict\s*\n+\s*(PASS|WARN|FAIL)\b",
        text, re.IGNORECASE | re.MULTILINE,
    )
    if header_match:
        signal = header_match.group(1).upper()
        log.info("_parse_review_signal: header match signal=%s", signal)
        return signal

    # Strategy 2: signal as the start of its own line (priority: FAIL > WARN > PASS)
    # Allows trailing text, e.g. "FAIL: missing test coverage"
    for signal in ("FAIL", "WARN", "PASS"):
        if re.search(rf"^\s*{signal}\b", text, re.IGNORECASE | re.MULTILINE):
            log.info("_parse_review_signal: standalone-line match signal=%s", signal)
            return signal

    log.info("_parse_review_signal: no explicit signal found — defaulting to PASS")
    return "PASS"


def _build_phases_summary(
    *,
    triage_cost: float,
    triage_signal: str,
    step_results: list[dict],
    review_cost: float,
    review_signal: str,
) -> list[dict]:
    """Assemble the phases-summary list passed to ``destination.format_pr_body``.

    Includes the triage phase, each execution step (from ``step_results``),
    and the review phase, so the PR body shows what ran, its cost, and the
    outcome of each phase.
    """
    summary: list[dict] = [
        {"name": "triage", "cost": triage_cost, "result": triage_signal},
    ]
    for step in step_results:
        name = f"step {step.get('step_number', '?')}: {step.get('title', '')}".strip()
        summary.append({
            "name": name,
            "cost": step.get("cost", 0) or 0,
            "duration_s": step.get("duration_s", 0) or 0,
            "result": step.get("status", "unknown"),
        })
    summary.append(
        {"name": "review", "cost": review_cost, "result": review_signal}
    )
    return summary


_DISCOVERY_PATTERNS = (
    "out of scope",
    "not fixed",
    "pre-existing",
    "discovered",
)


def _extract_discoveries(step_results: list[dict]) -> list[str]:
    """Scan step summaries for out-of-scope / discovered-but-not-fixed findings.

    Keeps it simple: any paragraph (split on blank lines) in a step's
    summary text that mentions one of the known marker phrases gets pulled
    out verbatim and tagged with the originating step, so the PR body
    surfaces findings that would otherwise die in the run DB.
    """
    discoveries: list[str] = []
    for step in step_results:
        summary = step.get("summary", "") or ""
        if not summary:
            continue
        lowered = summary.lower()
        if not any(pattern in lowered for pattern in _DISCOVERY_PATTERNS):
            continue
        for paragraph in re.split(r"\n\s*\n", summary):
            para_lower = paragraph.lower()
            if any(pattern in para_lower for pattern in _DISCOVERY_PATTERNS):
                step_label = f"step {step.get('step_number', '?')}: {step.get('title', '')}".strip()
                discoveries.append(f"**{step_label}** — {paragraph.strip()}")
    return discoveries


def _load_workflow_config(wf_dir: Path) -> dict:
    """Load workflow config from workflow.md (OKF) or workflow.yaml (legacy)."""
    wf_md = wf_dir / "workflow.md"
    wf_yaml = wf_dir / "workflow.yaml"

    if wf_md.is_file():
        text = wf_md.read_text()
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return yaml.safe_load(parts[1]) or {}
        log.warning("workflow.md missing valid frontmatter in %s", wf_dir)
        return {}
    elif wf_yaml.is_file():
        return yaml.safe_load(wf_yaml.read_text()) or {}
    else:
        log.warning("no workflow.md or workflow.yaml in %s", wf_dir)
        return {}


def _load_workflow() -> dict:
    return _load_workflow_config(WORKFLOW_DIR)

def _load_prompt(phase: str) -> str:
    return (WORKFLOW_DIR / "prompts" / f"{phase}.md").read_text()

def _phase_cfg(workflow: dict) -> dict[str, dict]:
    return {p["name"]: {"model": p.get("model", "sonnet"), "max_turns": p.get("max_turns", 10)}
            for p in workflow.get("phases", [])}

def _render(template: str, issue_number: int, issue_body: str,
            prior: dict, prior_review: str = "", repo_context: str = "",
            recent_learnings: str = "") -> str:
    out = template.replace("{issue_number}", str(issue_number))
    out = out.replace("{issue_body}", issue_body)
    out = out.replace("{repo_context}", repo_context)
    out = out.replace("{prior_phases}", json.dumps(prior, indent=2, default=str) if prior else "")
    out = out.replace("{recent_learnings}", recent_learnings or "No prior learnings available.")
    if prior_review:
        out += f"\n\n## Prior run review\n\n{prior_review}"
    return out

def _call(phase: str, cfg: dict, *, cwd: str, issue_number: int, issue_body: str,
          prior: dict, prior_review: str = "", model_ov: str | None = None,
          repo_context: str = "", recent_learnings: str = "") -> dict:
    prompt = _render(_load_prompt(phase), issue_number, issue_body, prior, prior_review, repo_context, recent_learnings)
    model = model_ov or cfg.get("model", "sonnet")
    log.info("[%s] model=%s max_turns=%d prompt_len=%d", phase, model, cfg.get("max_turns", 10), len(prompt))
    resp = runtime.call_agent(prompt, model=model, cwd=cwd, max_turns=cfg.get("max_turns", 10))
    finish = resp.get("finish_reason", "unknown")
    content = resp.get("content", "")
    log.info("[%s] %.1fs $%.4f finish=%s content_len=%d",
             phase, resp["duration_s"], resp["cost"], finish, len(str(content)))
    if finish == "error":
        log.error("[%s] phase returned error: %s", phase, str(content)[:500])
    elif finish == "timeout":
        log.warning("[%s] phase timed out", phase)
    resp["_prompt"] = prompt  # stash for message logging
    return resp

def _check_resp(phase: str, resp: dict) -> None:
    """Raise RuntimeError if the agent response indicates failure."""
    finish = resp.get("finish_reason", "unknown")
    if finish == "error":
        content = resp.get("content", "")
        raise RuntimeError(f"Phase {phase} failed: {str(content)[:300]}")
    if finish == "timeout":
        raise RuntimeError(f"Phase {phase} timed out")

def _content(resp: dict) -> str:
    """Extract content string from agent response (agents respond in prose)."""
    return resp["content"] if isinstance(resp["content"], str) else json.dumps(resp["content"])

def _extract_json(prose: str, schema_hint: str, cwd: str) -> dict:
    """Make a dedicated extraction call to pull structured data from prose."""
    if not prose or not prose.strip():
        raise ValueError(
            f"_extract_json called with empty prose — the upstream phase produced no output. "
            f"schema_hint={schema_hint[:100]}"
        )
    prompt = f"""Extract structured data from the following text. Return ONLY valid JSON matching this schema (no markdown fences, no explanation):
{schema_hint}

Text to extract from:
{prose}"""
    resp = runtime.call_agent(prompt, model="haiku", cwd=cwd, max_turns=1)
    raw = _content(resp)
    log.debug("_extract_json raw response (len=%d): %s", len(raw), raw[:300])
    if not raw or not raw.strip():
        raise ValueError(
            f"_extract_json: haiku returned empty response. "
            f"Input prose (len={len(prose)}): {prose[:200]}"
        )
    # Strip markdown fences if present
    if raw.strip().startswith("```"):
        raw = raw.strip().split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"_extract_json: JSON parse failed ({exc}). "
            f"Raw response (len={len(raw)}): {raw[:500]}"
        ) from exc

def _start_phase(conn, name, model=None):
    """Create a phase record at the START of execution. Returns phase_id."""
    return storage.log_phase(conn, name, model=model)

def _resolve_model(cfg: dict | None, model_ov: str | None = None) -> str:
    """Resolve the model for a phase: override wins, else cfg, else 'sonnet'.

    Mirrors the model-selection logic in `_call()` so that the model stored
    in the `phase` SQLite row matches the model actually dispatched.
    """
    cfg = cfg or {}
    return model_ov or cfg.get("model", "sonnet")

def _log_phase_messages(conn, phase_id, resp):
    """Log the prompt/response pair as user + assistant messages for this phase."""
    if not resp:
        return
    prompt = resp.get("_prompt", "")
    content = resp.get("content", "")
    if prompt:
        storage.log_message(
            conn, phase_id, turn_number=1, role="user", content=prompt,
        )
    if content:
        msg_id = storage.log_message(
            conn, phase_id, turn_number=1, role="assistant",
            content=content if isinstance(content, str) else json.dumps(content),
            tokens_in=resp.get("tokens_in", 0),
            tokens_out=resp.get("tokens_out", 0),
            cost=resp.get("cost", 0),
        )

def _finish_phase(conn, phase_id, resp, failed=False):
    """Update the phase record AFTER execution completes. Also logs messages."""
    _log_phase_messages(conn, phase_id, resp)
    storage.finish_phase(
        conn, phase_id,
        status="failed" if failed else "completed",
        cost=resp.get("cost", 0) if resp else 0,
        tokens_in=resp.get("tokens_in", 0) if resp else 0,
        tokens_out=resp.get("tokens_out", 0) if resp else 0,
    )


class BudgetExceeded(RuntimeError): pass
class GateFailure(RuntimeError): pass

def _guard(spent: float, budget: float):
    if spent > budget:
        raise BudgetExceeded(f"${spent:.2f} > budget ${budget:.2f}")

def _run_gates(conn, phase_name: str, output: dict, **gate_kw) -> None:
    """Run phase gates. Only called when output is a structured dict."""
    results = gates.run_phase_gates(phase_name, output, **gate_kw)
    for r in results:
        storage.log_event(conn, "gate_result", {
            "phase": phase_name, "gate": r["gate"],
            "passed": r["passed"], "reason": r["reason"],
        })
        log.info("[gate] %s/%s: %s — %s", phase_name, r["gate"],
                 "PASS" if r["passed"] else "FAIL", r["reason"])
        if not r["passed"]:
            raise GateFailure(
                f"gate {r['gate']} failed for {phase_name}: {r['reason']}"
            )

def _resolve_resume_db(resume_ref: str) -> str:
    """Resolve a --resume value to a DB path.

    Accepts either a full path or a substring that matches a single .db file
    in the runs/ directory.
    """
    if os.path.isfile(resume_ref):
        return resume_ref
    runs_dir = Path(__file__).parent / "runs"
    matches = sorted(glob.glob(str(runs_dir / f"*{resume_ref}*.db")))
    if len(matches) == 1:
        return matches[0]
    if len(matches) == 0:
        raise FileNotFoundError(f"No run DB found matching '{resume_ref}'")
    raise ValueError(
        f"Ambiguous resume ref '{resume_ref}' — matches {len(matches)} DBs: "
        + ", ".join(os.path.basename(m) for m in matches[:5])
    )


def load_resume_state(db_path: str) -> dict:
    """Load completed phases and outputs from a prior run DB."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get completed phases
    phases = conn.execute(
        "SELECT phase_name, status FROM phase WHERE status = 'completed' ORDER BY id"
    ).fetchall()

    # Get assistant messages (phase outputs) for completed phases
    prior: dict[str, str] = {}
    for p in phases:
        msg = conn.execute(
            "SELECT m.content FROM message m "
            "JOIN phase ph ON m.phase_id = ph.id "
            "WHERE ph.phase_name = ? AND m.role = 'assistant' "
            "ORDER BY m.id DESC LIMIT 1",
            (p['phase_name'],)
        ).fetchone()
        if msg:
            prior[p['phase_name']] = msg['content']

    # Get run metadata
    run = conn.execute("SELECT * FROM run LIMIT 1").fetchone()
    branch = run['branch'] if run and 'branch' in run.keys() else None

    # Sum cost from completed phases
    cost_row = conn.execute(
        "SELECT COALESCE(SUM(cost), 0) as total FROM phase WHERE status = 'completed'"
    ).fetchone()
    spent = cost_row['total'] if cost_row else 0.0

    conn.close()
    return {
        'completed_phases': {p['phase_name'] for p in phases},
        'prior': prior,
        'branch': branch,
        'spent': spent,
        'db_path': db_path,
    }


def _post_failure(repo: str | None, num: int | None, err: str, provider: str = "github"):
    if num is None:
        log.info("[failure] no issue_number — skipping failure comment (repo=%s)", repo)
        return
    try: source.post_comment(repo, num, f"Pipeline failed.\n\n```\n{err[:2000]}\n```", provider=provider)
    except Exception: log.warning("failed to post failure comment on %s#%d", repo, num)


def _sanitize_branch_ref(ref: str) -> str:
    """Sanitize a git ref (branch/tag/commit hash) for embedding in a generated
    branch name, e.g. ``feature/foo bar`` -> ``feature-foo-bar``."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", ref).strip("-")
    return safe[:24] or "ref"


def run_pipeline(repo: str, issue_number: int, *,
                 budget: float = 1.00, model_override: str | None = None,
                 repo_path: str | None = None,
                 resume_from: str | None = None) -> dict:
    healthy, detail = runtime._check_cli_health()
    if not healthy:
        log.error("aborting run_pipeline: claude CLI health check failed: %s", detail)
        raise RuntimeError(f"claude CLI health check failed before run start: {detail}")
    wf = _load_workflow()
    budget = budget or wf.get("budget", {}).get("max_per_run_usd", 1.00)
    max_par = wf.get("max_parallel_workers", 5)
    pcfg = _phase_cfg(wf)

    issue = source.fetch_issue(repo, issue_number)
    issue_body = f"# {issue['title']}\n\n{issue['body']}"

    # -- Resume state --
    resume_state: dict | None = None
    completed_phases: set[str] = set()
    if resume_from:
        resume_db = _resolve_resume_db(resume_from)
        resume_state = load_resume_state(resume_db)
        completed_phases = resume_state['completed_phases']
        log.info("resuming from %s — %d phases completed: %s",
                 resume_db, len(completed_phases), ", ".join(sorted(completed_phases)))

    db_path, conn = storage.create_run_db(repo, issue_number, model=model_override)
    run_id = db_path.stem if hasattr(db_path, 'stem') else str(db_path)
    prior_review = ""
    for r in storage.find_prior_runs(repo, issue_number):
        if r.get("review_summary"):
            prior_review = r["review_summary"]

    branch = resume_state['branch'] if resume_state and resume_state.get('branch') else f"sw/issue-{issue_number}"
    # Resolve the target repo path ONCE — this generic engine always requires an
    # explicit repo (no workflow-card lookup, unlike run_domain_pipeline). A bare
    # os.getcwd() fallback here would silently point at whatever directory the
    # process happened to be launched from (e.g. simple-workflow's own checkout),
    # producing a worktree under the wrong repo. Log loudly when it happens.
    if not repo_path:
        log.warning(
            "no --repo-path given for generic pipeline — falling back to cwd=%s. "
            "This is almost certainly wrong; pass --repo-path explicitly.", os.getcwd(),
        )
    effective_repo_path = repo_path or os.getcwd()
    log.info("run_pipeline effective_repo_path=%s", effective_repo_path)
    if resume_state:
        wt = workspace.reuse_or_create_workspace(effective_repo_path, branch)
    else:
        wt = workspace.create_workspace(effective_repo_path, branch)
    log.info("worktree created at wt=%s (branch=%s, repo=%s)", wt, branch, effective_repo_path)
    workspace.neutralize_claude_md(wt)
    repo_context = _load_repo_context(wt)
    if repo_context:
        log.info("loaded .workflows/ context (%d chars)", len(repo_context))
    prior: dict[str, Any] = {}
    if resume_state:
        prior.update(resume_state['prior'])
    spent = resume_state['spent'] if resume_state else 0.0
    recent_learnings_text = format_learnings_for_prompt(get_recent_learnings(5))
    log.info("recent_learnings injected chars=%d", len(recent_learnings_text))
    kw = dict(cwd=wt, issue_number=issue_number, issue_body=issue_body,
              model_ov=model_override, repo_context=repo_context,
              recent_learnings=recent_learnings_text)

    # Helper to check if a phase (or any execute-task-*) was completed in a prior run
    def _skip(phase_name: str) -> bool:
        if phase_name in completed_phases:
            log.info("resuming: skipping %s (completed in prior run)", phase_name)
            return True
        return False

    # For resume: check if all execute phases completed
    _any_execute_completed = any(p.startswith("execute-task-") for p in completed_phases)

    try:
        # -- Triage (prose -> extract task list) --
        if _skip("triage"):
            triage_text = prior.get("triage", "")
        else:
            pid = _start_phase(conn, "triage", model=_resolve_model(pcfg.get("triage", {}), kw.get("model_ov")))
            resp = _call("triage", pcfg.get("triage", {}), prior=prior, prior_review=prior_review, **kw)
            _check_resp("triage", resp)
            _finish_phase(conn, pid, resp); spent += resp["cost"]; _guard(spent, budget)
            triage_text = _content(resp)
            prior["triage"] = triage_text

        triage = _extract_json(triage_text, (
            '{"tasks": [{"id": 1, "title": "...", "target_files": [...], "depends_on": []}], '
            '"proof_type": "test_passes", "escalate": false}'
        ), cwd=wt)
        if "triage" not in completed_phases:
            _run_gates(conn, "triage", triage, worktree_path=wt)
        tasks = triage.get("tasks", [])

        # -- Verify (check claims against codebase) --
        if _skip("verify"):
            verify_text = prior.get("verify", "")
        else:
            pid = _start_phase(conn, "verify", model=_resolve_model(pcfg.get("verify", {}), kw.get("model_ov")))
            resp = _call("verify", pcfg.get("verify", {}), prior=prior, **kw)
            _check_resp("verify", resp)
            _finish_phase(conn, pid, resp); spent += resp["cost"]; _guard(spent, budget)
            verify_text = _content(resp)
            prior["verify"] = verify_text

        verify = _extract_json(verify_text, (
            '{"verified_tasks": [{"task_id": 1, "status": "CONFIRMED", "evidence": "...", '
            '"files_checked": [...], "lines": "...", "current_state": "..."}], '
            '"buildable_count": 1, "refuted_count": 0, "stale_count": 0, '
            '"recommendation": "proceed"}'
        ), cwd=wt)
        if "verify" not in completed_phases:
            _run_gates(conn, "verify", verify)
            storage.log_event(conn, "verify_result", {
                "buildable": verify.get("buildable_count", 0),
                "refuted": verify.get("refuted_count", 0),
                "stale": verify.get("stale_count", 0),
                "recommendation": verify.get("recommendation", ""),
            })

        # Filter tasks: only CONFIRMED or PARTIAL proceed
        buildable_ids: set[int] = set()
        for vt in verify.get("verified_tasks", []):
            if vt.get("status", "").upper() in ("CONFIRMED", "PARTIAL"):
                buildable_ids.add(vt.get("task_id"))

        recommendation = verify.get("recommendation", "proceed")
        if not buildable_ids or recommendation in ("already_fixed", "needs_clarification"):
            status = "already_fixed" if recommendation == "already_fixed" else "claims_invalid"
            msg = (
                f"Verify phase result: **{recommendation}**\n\n"
                f"All {len(tasks)} task(s) were "
                f"{'already resolved' if status == 'already_fixed' else 'not confirmed against the codebase'}.\n\n"
                f"Verified tasks:\n"
            )
            for vt in verify.get("verified_tasks", []):
                msg += f"- Task {vt.get('task_id')}: **{vt.get('status')}** — {vt.get('evidence', '')}\n"
            source.post_comment(repo, issue_number, msg)
            storage.finish_run(conn, status, total_cost=spent)
            log.info("verify exit: %s — no buildable tasks", status)
            return {"status": status, "spent_usd": spent, "run_id": run_id}

        tasks = [t for t in tasks if t["id"] in buildable_ids]
        log.info("verify passed: %d/%d tasks buildable", len(tasks), len(triage.get("tasks", [])))

        # -- Plan + Test-Plan (parallel per task, prose pass-through) --
        # Check if all plan/test-plan phases are already done
        _all_plans_done = all(
            f"plan-task-{t['id']}" in completed_phases and f"test-plan-task-{t['id']}" in completed_phases
            for t in tasks
        )
        plan_r: dict[int, str] = {}; tp_r: dict[int, str] = {}
        if _all_plans_done:
            log.info("resuming: skipping plan + test-plan phases (completed in prior run)")
            # Rebuild plan_r / tp_r from prior outputs
            if isinstance(prior.get("plans"), dict):
                plan_r = {int(k): v for k, v in prior["plans"].items()}
            if isinstance(prior.get("test_plans"), dict):
                tp_r = {int(k): v for k, v in prior["test_plans"].items()}
        else:
            with ThreadPoolExecutor(max_workers=min(len(tasks) * 2, max_par)) as pool:
                futs: dict[Any, tuple[str, int, int]] = {}
                for t in tasks:
                    tp = {**prior, "_current_task": t}
                    for ph in ("plan", "test-plan"):
                        phase_key = f"{ph}-task-{t['id']}"
                        if phase_key in completed_phases:
                            log.info("resuming: skipping %s (completed in prior run)", phase_key)
                            continue
                        pid = _start_phase(conn, phase_key, model=_resolve_model(pcfg.get(ph, {}), kw.get("model_ov")))
                        futs[pool.submit(_call, ph, pcfg.get(ph, {}), prior=tp, **kw)] = (ph, t["id"], pid)
                # Wait for ALL futures — collect results and errors; raise after cleanup
                done, not_done = wait(futs, return_when=FIRST_EXCEPTION)
                # Finish phases for not_done futures too (shouldn't happen, but defensive)
                for f in not_done:
                    ph, tid, pid = futs[f]
                    log.warning("[%s] task %d future not complete after wait — marking failed", ph, tid)
                    _finish_phase(conn, pid, None, failed=True)
                first_exc: Exception | None = None
                for f in done:
                    ph, tid, pid = futs[f]
                    try:
                        resp = f.result()
                        _check_resp(f"{ph}-task-{tid}", resp)
                        _finish_phase(conn, pid, resp)
                        spent += resp["cost"]
                        text = _content(resp)
                        (plan_r if ph == "plan" else tp_r)[tid] = text
                        log.info("[%s] task %d complete (len=%d)", ph, tid, len(text))
                    except Exception as exc:
                        log.error("[%s] task %d failed: %s", ph, tid, exc)
                        _finish_phase(conn, pid, None, failed=True)
                        if first_exc is None:
                            first_exc = exc
                if first_exc is not None:
                    raise first_exc
            _guard(spent, budget)
            prior["plans"] = plan_r; prior["test_plans"] = tp_r

        # -- Wave Planner (prose -> extract wave assignments) --
        if _skip("wave-planner"):
            wave_text = prior.get("wave_plan", "")
        else:
            pid = _start_phase(conn, "wave-planner", model=_resolve_model(pcfg.get("wave-planner", {}), kw.get("model_ov")))
            resp = _call("wave-planner", pcfg.get("wave-planner", {}), prior=prior, **kw)
            _check_resp("wave-planner", resp)
            _finish_phase(conn, pid, resp); spent += resp["cost"]
            if spent > budget:
                log.warning("budget exceeded after wave-planner ($%.2f > $%.2f) — continuing to execute", spent, budget)
            wave_text = _content(resp)
            prior["wave_plan"] = wave_text

        wave_plan = _extract_json(wave_text, (
            '{"waves": [[1, 2], [3, 4]]}  -- list of lists, each inner list is task IDs in that wave'
        ), cwd=wt)
        task_ids = [t["id"] for t in tasks]
        if "wave-planner" not in completed_phases:
            _run_gates(conn, "wave-planner", wave_plan,
                       task_ids=task_ids, max_parallel=max_par)

        # -- Execute (parallel within wave, serial across waves) --
        if _any_execute_completed:
            log.info("resuming: skipping execute phases (completed in prior run)")
            exec_results = prior.get("execute", [])
        else:
            exec_results: list[dict] = []
            for wi, wave_task_ids in enumerate(wave_plan.get("waves", [])):
                log.info("wave %d: %s", wi, wave_task_ids)
                with ThreadPoolExecutor(max_workers=max_par) as pool:
                    efuts: dict[Any, tuple[int, int]] = {}
                    for tid in wave_task_ids:
                        t = next((t for t in tasks if t["id"] == tid), None)
                        if not t: continue
                        ep = {**prior, "_current_task": t,
                              "_current_plan": plan_r.get(tid, ""),
                              "_current_test_plan": tp_r.get(tid, "")}
                        pid = _start_phase(conn, f"execute-task-{tid}", model=_resolve_model(pcfg.get("execute", {}), kw.get("model_ov")))
                        efuts[pool.submit(_call, "execute", pcfg.get("execute", {}), prior=ep, **kw)] = (tid, pid)
                    for f in as_completed(efuts):
                        tid, pid = efuts[f]
                        try:
                            resp = f.result()
                            _finish_phase(conn, pid, resp)
                        except Exception:
                            _finish_phase(conn, pid, None, failed=True)
                            raise
                        spent += resp["cost"]
                        exec_text = _content(resp)
                        exec_results.append({"task_id": tid, "response": exec_text})
                if spent > budget:
                    log.warning("budget exceeded after execute wave %d ($%.2f > $%.2f) — continuing to PR", wi, spent, budget)
            prior["execute"] = exec_results

        # -- Safety-net commit: catch changes the execute agent failed to commit --
        _porcelain = subprocess.run(
            ["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True,
        ).stdout.strip()
        _commits = subprocess.run(
            ["git", "log", "origin/main..HEAD", "--oneline"], cwd=wt, capture_output=True, text=True,
        ).stdout.strip()
        if _porcelain:
            log.warning("execute phase left uncommitted changes — committing as safety net")
            subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"feat: resolve #{issue_number}"],
                cwd=wt, check=True,
            )
            storage.log_event(conn, "safety_net_commit", {"files": _porcelain})
        elif not _commits:
            raise RuntimeError(
                "Execute phase produced no changes — no commits on branch and no uncommitted files"
            )

        # -- Review (prose pass-through) --
        diff = workspace.get_diff(wt)[:50_000]
        if _skip("review"):
            pass  # prior["review"] already populated from resume state
        else:
            pid = _start_phase(conn, "review", model=_resolve_model(pcfg.get("review", {}), kw.get("model_ov")))
            resp = _call("review", pcfg.get("review", {}),
                          prior={**prior, "_combined_diff": diff}, **kw)
            _finish_phase(conn, pid, resp); spent += resp["cost"]
            prior["review"] = _content(resp)

        # -- Improve (informational — does not block PR) --
        if _skip("improve"):
            pass  # prior["improve"] already populated from resume state
        else:
            try:
                cost_summary = json.dumps({
                    "total_spent_usd": round(spent, 4),
                    "budget_usd": budget,
                    "utilization_pct": round(spent / budget * 100, 1) if budget else 0,
                })
                improve_prompt = _load_prompt("improve")
                improve_prompt = improve_prompt.replace("{cost_summary}", cost_summary)
                improve_prompt = improve_prompt.replace("{combined_diff}", diff[:30_000])
                improve_prompt = improve_prompt.replace("{prior_phases}",
                    json.dumps(prior, indent=2, default=str))
                improve_cfg = pcfg.get("improve", {"model": "opus", "max_turns": 10})
                model = model_override or improve_cfg.get("model", "opus")
                pid = _start_phase(conn, "improve", model=_resolve_model(improve_cfg, kw.get("model_ov")))
                log.info("[improve] model=%s max_turns=%d", model, improve_cfg.get("max_turns", 10))
                resp = runtime.call_agent(improve_prompt, model=model, cwd=wt,
                                          max_turns=improve_cfg.get("max_turns", 10))
                resp["_prompt"] = improve_prompt
                _finish_phase(conn, pid, resp); spent += resp["cost"]
                improve_text = _content(resp)
                prior["improve"] = improve_text
                # Try to extract and log structured suggestions
                try:
                    improve_data = json.loads(improve_text) if improve_text.strip().startswith("{") \
                        else _extract_json(improve_text, (
                            '{"overall_score": 0, "phase_scores": {"triage": 0, "plan": 0, "execute": 0, "review": 0}, '
                            '"recommendations": [], "context_gaps": [], "code_quality_issues": [], '
                            '"cost_analysis": "", "pipeline_health": "", "summary": ""}'
                        ), cwd=wt)
                    storage.log_event(conn, "improvement_suggestions", improve_data)
                    log.info("[improve] overall_score=%s phase_scores=%s",
                             improve_data.get("overall_score"), improve_data.get("phase_scores", {}))
                    # Capture learnings for injection into future runs
                    try:
                        n_learnings = capture_learnings(
                            improve_output=improve_data,
                            run_id=run_id,
                            repo=repo,
                            issue_number=issue_number,
                        )
                        log.info("learning_capture count=%d", n_learnings)
                    except Exception as le:
                        log.warning("learning_capture_failed error=%s", le)
                except (json.JSONDecodeError, Exception) as je:
                    log.warning("[improve] could not extract structured JSON: %s", je)
                    storage.log_event(conn, "improvement_suggestions", {"raw": improve_text[:5000]})
            except Exception as ie:
                log.warning("[improve] phase failed (non-blocking): %s", ie)
                if 'pid' in dir():
                    _finish_phase(conn, pid, None, failed=True)

        # -- Push + PR --
        diff_ok, diff_reason = gates.validate_pr_diff(wt, base="main")
        storage.log_event(conn, "pr_diff_gate", {"passed": diff_ok, "reason": diff_reason})
        if not diff_ok:
            log.error("[pr-diff-gate] FAIL: %s", diff_reason)
            raise GateFailure(f"pr-diff gate failed: {diff_reason}")
        destination.push_branch(wt, branch)
        body = destination.format_pr_body(issue_number, prior["review"], db_path, [])
        pr = destination.create_pr(repo, branch, f"fix: resolve #{issue_number}", body)

        # -- Validate (optional — web preview testing via Playwright) --
        # Runs AFTER PR creation because Vercel needs the PR to create a preview.
        validate_result = None
        if _skip("validate"):
            validate_result = prior.get("validate")
        elif validate.check_has_ui_changes(prior.get("triage", ""), issue_body):
            try:
                pr_number = pr.get("number", issue_number)
                log.info("[validate] UI changes detected — polling for Vercel preview URL")
                preview_url = validate.get_preview_url(repo, pr_number)

                if preview_url:
                    log.info("[validate] preview ready: %s", preview_url)
                    validate_prompt = _load_prompt("validate")
                    validate_prompt = validate_prompt.replace("{pr_number}", str(pr_number))
                    validate_prompt = validate_prompt.replace("{repo}", repo)
                    validate_prompt = validate_prompt.replace("{preview_url}", preview_url)
                    validate_prompt = validate_prompt.replace("{issue_body}", issue_body)
                    validate_prompt = validate_prompt.replace("{prior_phases}",
                        json.dumps(prior, indent=2, default=str))

                    validate_cfg = pcfg.get("validate", {"model": "sonnet", "max_turns": 10})
                    model = model_override or validate_cfg.get("model", "sonnet")
                    pid = _start_phase(conn, "validate", model=_resolve_model(validate_cfg, kw.get("model_ov")))
                    log.info("[validate] model=%s max_turns=%d", model, validate_cfg.get("max_turns", 10))
                    resp = runtime.call_agent(validate_prompt, model=model, cwd=wt,
                                              max_turns=validate_cfg.get("max_turns", 10))
                    resp["_prompt"] = validate_prompt
                    _finish_phase(conn, pid, resp); spent += resp["cost"]
                    validate_text = _content(resp)
                    prior["validate"] = validate_text
                    validate_result = validate_text
                    storage.log_event(conn, "validate_result", {"preview_url": preview_url, "raw_len": len(validate_text)})
                    log.info("[validate] complete — %d chars output", len(validate_text))
                else:
                    log.warning("[validate] preview URL not available — skipping validation")
                    storage.log_event(conn, "validate_skipped", {"reason": "preview_url_timeout"})
            except Exception as ve:
                log.warning("[validate] phase failed (non-blocking): %s", ve)
                storage.log_event(conn, "validate_error", {"error": str(ve)})
        else:
            log.info("[validate] no UI changes detected — skipping validation")
            storage.log_event(conn, "validate_skipped", {"reason": "no_ui_changes"})

        storage.finish_run(conn, "ok", total_cost=spent, branch=branch)
        result = {"status": "ok", "pr_url": pr["url"], "spent_usd": spent, "run_id": run_id}
        if validate_result:
            result["validate"] = validate_result
        return result

    except GateFailure as e:
        log.error("gate failure: %s", e)
        storage.finish_run(conn, "gate_failed", total_cost=spent)
        _post_failure(repo, issue_number, str(e))
        return {"status": "error", "error": str(e), "spent_usd": spent, "run_id": run_id}
    except BudgetExceeded as e:
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
        log.info("run complete — worktree preserved at %s (branch: %s)", wt, branch)
        conn.close()


def run_domain_pipeline(repo: str | None, issue_number: int | None, *,
                        workflow: str,
                        base_ref: str = "main",
                        budget: float = 10.00,
                        model_override: str | None = None,
                        repo_path: str | None = None) -> dict:
    """Simplified 5-phase pipeline for domain-specific workflows (shftty-web, shftty-ios, etc).

    Primary input is a local repo path + git ref (``base_ref``), not a GitHub
    issue. ``repo`` (owner/repo slug) and ``issue_number`` are both optional —
    when ``issue_number`` is provided, triage gets the issue body as extra
    context and status comments are posted back to it; when omitted, the
    pipeline runs purely off the workflow's own prompts and repo state.

    Unlike run_pipeline(), this function:
    - Loads prompts from a domain workflow dir (e.g. workflows/shftty-web/prompts/)
    - Runs 5 core phases: triage → plan → execute → review → improve
    - Passes phase outputs as formatted markdown text via {prior_phases} (no JSON extraction)
    - Exits early on SKIP/ESCALATE triage signal
    - Pushes the reviewed branch and stops — it does NOT create a PR. The branch
      with commits is the output of this pipeline; a separate `pr.sh` step
      (see destination.py) opens the PR from that branch when desired.
    - No wave planning, no parallel task execution, no JSON gates, no validate
      phase (post-PR preview-URL polling — duplicates review's own gates).
    - Runs triage -> plan -> execute -> review -> improve (informational
      retrospective, non-blocking) -> push.
    - Worktree branches from ``base_ref`` (default "main"); the final diff
      gate also diffs against ``base_ref``, not a hardcoded "main".
    """
    healthy, detail = runtime._check_cli_health()
    if not healthy:
        log.error("aborting run_domain_pipeline: claude CLI health check failed: %s", detail)
        raise RuntimeError(f"claude CLI health check failed before run start: {detail}")
    wf_dir = _resolve_workflow_dir(workflow)
    log.info("run_domain_pipeline start repo=%s issue=%s workflow=%s wf_dir=%s budget=%.2f base_ref=%s",
             repo, issue_number if issue_number is not None else "none", workflow, wf_dir, budget, base_ref)

    # Load workflow config from domain dir (falls back gracefully if missing)
    wf = _load_workflow_config(wf_dir)
    log.info("loaded workflow config from %s", wf_dir)
    budget = budget or wf.get("budget", {}).get("max_per_run_usd", 10.00)
    provider = wf.get("provider", "github")
    log.info("issue/PR provider=%s", provider)
    pcfg = _phase_cfg(wf)

    def load_prompt(phase: str) -> str:
        prompt_path = wf_dir / "prompts" / f"{phase}.md"
        if not prompt_path.is_file():
            raise FileNotFoundError(
                f"Prompt not found for phase '{phase}': {prompt_path}"
            )
        return prompt_path.read_text()

    def build_prior_text(prior: dict) -> str:
        """Format prior phase outputs as readable markdown for {prior_phases}."""
        if not prior:
            return ""
        return "\n\n---\n\n".join(
            f"## {name} phase output\n\n{text}" for name, text in prior.items()
        )

    def render_domain(template: str, prior: dict, repo_context: str = "",
                      recent_learnings: str = "", extra: dict | None = None) -> str:
        out = template.replace("{issue_number}", str(issue_number) if issue_number is not None else "N/A")
        out = out.replace("{issue_body}", issue_body)
        out = out.replace("{repo_context}", repo_context)
        out = out.replace("{prior_phases}", build_prior_text(prior))
        out = out.replace("{recent_learnings}", recent_learnings or "No prior learnings available.")
        # Individual phase outputs for prompts that need to distinguish them
        # e.g. {triage_output}, {execute_output}, {review_output}
        if prior:
            for phase_name, phase_text in prior.items():
                out = out.replace(f"{{{phase_name}_output}}", phase_text)
        if extra:
            for k, v in extra.items():
                out = out.replace(f"{{{k}}}", str(v))
        return out

    issue = source.fetch_issue(repo, issue_number, provider=provider)
    if issue_number is not None:
        issue_body = f"# {issue['title']}\n\n{issue['body']}"
    else:
        issue_body = (
            "No issue provided — task is defined entirely by the workflow's "
            "own prompts and current repo state."
        )
        log.info("[domain] no issue_number provided — triage runs without issue context")

    db_path, conn = storage.create_run_db(repo, issue_number, model=model_override)
    run_id = db_path.stem if hasattr(db_path, "stem") else str(db_path)
    log.info("run_domain_pipeline run_id=%s", run_id)

    # Resolve repo path ONCE: CLI arg > workflow card > cwd (last resort, logged loudly).
    #
    # This resolution must never fail silently. A silent fallback to os.getcwd()
    # here is what caused worktrees to be created under repos/simple-workflow/
    # instead of the target repo (e.g. repos/shftty/) — execute committed to
    # the wrong-rooted worktree, and push/PR creation later failed because the
    # branch lived under a path nobody was looking at. See
    # work/agent-reports/2026-06-30-pipeline-902-v7-review.md (attempt 6).
    if repo_path:
        log.info("repo_path resolved from CLI arg: %s", repo_path)
    else:
        card_path = wf.get("repo_path")
        if card_path and card_path != "from-cli":
            # repo_path in card is relative to the PA root (four levels up from
            # this file: engine/ -> simple-workflow/ -> repos/ -> PA root).
            candidate = Path(__file__).resolve().parent.parent.parent.parent / card_path
            if candidate.exists() and (candidate / ".git").exists():
                repo_path = str(candidate)
                log.info("repo_path resolved from workflow card: %s", repo_path)
            else:
                # Workflow card declared a repo_path but it didn't resolve to a
                # real git repo — this is a config/environment error, not an
                # ambiguous case. Fail loud instead of silently falling back to
                # cwd, which can point at simple-workflow's own checkout.
                raise FileNotFoundError(
                    f"workflow '{workflow}' declares repo_path={card_path!r} "
                    f"but {candidate} does not exist or is not a git repo "
                    f"(checked {candidate}/.git). Pass --repo-path explicitly "
                    f"to override, or fix workflow.md."
                )
        else:
            log.warning(
                "no repo_path in workflow card and no --repo-path given — "
                "falling back to cwd=%s. This is almost certainly wrong for "
                "code workflows; pass --repo-path explicitly.", os.getcwd(),
            )

    effective_repo_path = repo_path or os.getcwd()
    log.info("run_domain_pipeline effective_repo_path=%s", effective_repo_path)

    # Resolve the worktree path ONCE here and use this same `wt` value for every
    # subsequent phase (execute, review, push, PR) — never re-derive it.
    branch_suffix = str(issue_number) if issue_number is not None else _sanitize_branch_ref(base_ref)
    branch = f"sw/{workflow}-{branch_suffix}"
    wt = workspace.create_workspace(effective_repo_path, branch, base=base_ref)
    log.info("worktree created at wt=%s (branch=%s, repo=%s, base_ref=%s)", wt, branch, effective_repo_path, base_ref)
    workspace.neutralize_claude_md(wt)
    repo_context = _load_repo_context(wt)
    if repo_context:
        log.info("loaded .workflows/ context (%d chars)", len(repo_context))

    recent_learnings_text = format_learnings_for_prompt(get_recent_learnings(5))
    log.info("recent_learnings injected chars=%d", len(recent_learnings_text))

    prior: dict[str, str] = {}
    spent: float = 0.0

    try:
        # ---- Triage phase -------------------------------------------------------
        triage_cfg = pcfg.get("triage", {"model": "sonnet", "max_turns": 10})
        triage_model = _resolve_model(triage_cfg, model_override)
        pid = _start_phase(conn, "triage", model=triage_model)
        log.info("[domain/triage] start model=%s", triage_model)

        triage_prompt = render_domain(
            load_prompt("triage"), prior,
            repo_context=repo_context, recent_learnings=recent_learnings_text,
        )
        triage_resp = runtime.call_agent(
            triage_prompt, model=triage_model, cwd=wt,
            max_turns=triage_cfg.get("max_turns", 10),
        )
        triage_resp["_prompt"] = triage_prompt
        _check_resp("triage", triage_resp)
        _finish_phase(conn, pid, triage_resp)
        spent += triage_resp["cost"]
        triage_text = _content(triage_resp)
        prior["triage"] = triage_text
        storage.log_event(conn, "triage_complete", {
            "cost": triage_resp["cost"], "content_len": len(triage_text),
        })

        triage_signal = _parse_triage_signal(triage_text)
        log.info("[domain/triage] signal=%s spent=%.4f", triage_signal, spent)
        storage.log_event(conn, "triage_signal", {"signal": triage_signal})

        if triage_signal in ("SKIP", "ESCALATE"):
            msg = (
                f"Pipeline {triage_signal.lower()}ped by triage phase "
                f"(workflow: {workflow}).\n\n"
                f"Triage output:\n\n{triage_text[:3000]}"
            )
            if issue_number is not None:
                source.post_comment(repo, issue_number, msg, provider=provider)
            else:
                log.info("[domain/triage] no issue_number — skipping early-exit comment")
            storage.finish_run(conn, f"triage_{triage_signal.lower()}", total_cost=spent)
            log.info("[domain/triage] early exit signal=%s", triage_signal)
            return {"status": f"triage_{triage_signal.lower()}", "spent_usd": spent, "run_id": run_id}

        _guard(spent, budget)

        # ---- Plan phase (optional — runs if prompts/plan.md exists) --------------
        plan_prompt_path = wf_dir / "prompts" / "plan.md"
        if plan_prompt_path.is_file():
            plan_cfg = pcfg.get("plan", {"model": "sonnet", "max_turns": 20})
            plan_model = _resolve_model(plan_cfg, model_override)
            pid = _start_phase(conn, "plan", model=plan_model)
            log.info("[domain/plan] start model=%s", plan_model)

            plan_prompt = render_domain(
                load_prompt("plan"), prior,
                repo_context=repo_context, recent_learnings=recent_learnings_text,
            )
            plan_resp = runtime.call_agent(
                plan_prompt, model=plan_model, cwd=wt,
                max_turns=plan_cfg.get("max_turns", 20),
            )
            plan_resp["_prompt"] = plan_prompt
            _check_resp("plan", plan_resp)
            _finish_phase(conn, pid, plan_resp)
            spent += plan_resp["cost"]
            plan_text = _content(plan_resp)
            prior["plan"] = plan_text
            storage.log_event(conn, "plan_complete", {
                "cost": plan_resp["cost"], "content_len": len(plan_text),
            })
            log.info("[domain/plan] complete spent=%.4f content_len=%d", spent, len(plan_text))

            _guard(spent, budget)

            # Parse steps from plan output (plan now owns the Steps section)
            steps = _parse_triage_steps(plan_text)
        else:
            log.info("[domain/plan] no plan.md found in %s — skipping plan phase", wf_dir)
            # Legacy fallback: parse steps from triage output directly
            steps = _parse_triage_steps(triage_text)

        # ---- Execute phase -------------------------------------------------------
        execute_cfg = pcfg.get("execute", {"model": "sonnet", "max_turns": 30})
        execute_model = _resolve_model(execute_cfg, model_override)

        if steps:
            log.info("[domain/execute] step mode: %d steps parsed from plan", len(steps))
            spent, step_results = _execute_steps(
                steps,
                conn=conn,
                wf_dir=wf_dir,
                wt=wt,
                execute_cfg=execute_cfg,
                execute_model=execute_model,
                issue_number=issue_number,
                issue_body=issue_body,
                triage_text=triage_text,
                repo_context=repo_context,
                recent_learnings=recent_learnings_text,
                workflow=workflow,
                budget=budget,
                spent=spent,
            )
            prior["execute"] = json.dumps(step_results, indent=2)
            storage.log_event(conn, "execute_complete", {
                "mode": "step",
                "total_steps": len(steps),
                "completed_steps": sum(1 for r in step_results if r["status"] == "completed"),
                "total_cost": sum(r["cost"] for r in step_results),
            })
        else:
            # Fallback: monolithic execute (no steps parsed from triage)
            log.warning("[domain/execute] no steps found in plan/triage — falling back to monolithic execute")
            pid = _start_phase(conn, "execute", model=execute_model)
            log.info("[domain/execute] start model=%s prior_phases=%s", execute_model, list(prior.keys()))

            execute_prompt = render_domain(
                load_prompt("execute"), prior,
                repo_context=repo_context, recent_learnings=recent_learnings_text,
            )
            execute_resp = runtime.call_agent(
                execute_prompt, model=execute_model, cwd=wt,
                max_turns=execute_cfg.get("max_turns", 30),
            )
            execute_resp["_prompt"] = execute_prompt
            _check_resp("execute", execute_resp)
            _finish_phase(conn, pid, execute_resp)
            spent += execute_resp["cost"]
            prior["execute"] = _content(execute_resp)
            storage.log_event(conn, "execute_complete", {
                "mode": "monolithic",
                "cost": execute_resp["cost"],
                "content_len": len(prior["execute"]),
            })
            log.info("[domain/execute] monolithic complete spent=%.4f content_len=%d",
                     spent, len(prior["execute"]))
            # No per-step results in monolithic mode — synthesize a single
            # entry so phases_summary/discoveries still reflect this phase.
            step_results = [{
                "step_number": 1,
                "title": "execute (monolithic)",
                "status": "completed",
                "cost": execute_resp["cost"],
                "duration_s": execute_resp.get("duration_s", 0),
                "summary": prior["execute"][:500],
            }]

        _guard(spent, budget)

        # ---- Safety-net commit ---------------------------------------------------
        _porcelain = subprocess.run(
            ["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True,
        ).stdout.strip()
        _commits = subprocess.run(
            ["git", "log", f"origin/{base_ref}..HEAD", "--oneline"], cwd=wt, capture_output=True, text=True,
        ).stdout.strip()
        if _porcelain:
            log.warning("[domain] execute phase left uncommitted changes — safety-net commit")
            subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
            _issue_suffix = f" #{issue_number}" if issue_number is not None else ""
            subprocess.run(
                ["git", "commit", "-m", f"feat: resolve{_issue_suffix} ({workflow})"],
                cwd=wt, check=True,
            )
            storage.log_event(conn, "safety_net_commit", {"files": _porcelain})
        elif not _commits:
            raise RuntimeError(
                "Execute phase produced no changes — no commits on branch and no uncommitted files"
            )

        # ---- Review phase -------------------------------------------------------
        diff = workspace.get_diff(wt, base=f"origin/{base_ref}")[:50_000]
        review_cfg = pcfg.get("review", {"model": "sonnet", "max_turns": 10})
        review_model = _resolve_model(review_cfg, model_override)
        pid = _start_phase(conn, "review", model=review_model)
        log.info("[domain/review] start model=%s diff_len=%d", review_model, len(diff))

        review_prompt = render_domain(
            load_prompt("review"), prior,
            repo_context=repo_context, recent_learnings=recent_learnings_text,
            extra={"combined_diff": diff},
        )
        review_resp = runtime.call_agent(
            review_prompt, model=review_model, cwd=wt,
            max_turns=review_cfg.get("max_turns", 10),
        )
        review_resp["_prompt"] = review_prompt
        _check_resp("review", review_resp)
        _finish_phase(conn, pid, review_resp)
        spent += review_resp["cost"]
        review_text = _content(review_resp)
        prior["review"] = review_text
        storage.log_event(conn, "review_complete", {
            "cost": review_resp["cost"], "content_len": len(review_text),
        })

        review_signal = _parse_review_signal(review_text)
        log.info("[domain/review] signal=%s spent=%.4f", review_signal, spent)
        storage.log_event(conn, "review_signal", {"signal": review_signal})

        # ---- Improve (informational retrospective — does not block push) --------
        try:
            improve_cfg = pcfg.get("improve", {"model": "sonnet", "max_turns": 10})
            improve_model = _resolve_model(improve_cfg, model_override)
            cost_summary = json.dumps({
                "total_spent_usd": round(spent, 4),
                "budget_usd": budget,
                "utilization_pct": round(spent / budget * 100, 1) if budget else 0,
            })
            improve_prompt = render_domain(
                load_prompt("improve"), prior,
                repo_context=repo_context, recent_learnings=recent_learnings_text,
                extra={"cost_summary": cost_summary, "combined_diff": diff[:30_000]},
            )
            pid = _start_phase(conn, "improve", model=improve_model)
            log.info("[domain/improve] start model=%s max_turns=%d", improve_model, improve_cfg.get("max_turns", 10))
            improve_resp = runtime.call_agent(
                improve_prompt, model=improve_model, cwd=wt,
                max_turns=improve_cfg.get("max_turns", 10),
            )
            improve_resp["_prompt"] = improve_prompt
            _finish_phase(conn, pid, improve_resp)
            spent += improve_resp["cost"]
            improve_text = _content(improve_resp)
            prior["improve"] = improve_text
            try:
                improve_data = json.loads(improve_text) if improve_text.strip().startswith("{") \
                    else _extract_json(improve_text, (
                        '{"overall_score": 0, "phase_scores": {"triage": 0, "plan": 0, "execute": 0, "review": 0}, '
                        '"recommendations": [], "context_gaps": [], "code_quality_issues": [], '
                        '"cost_analysis": "", "pipeline_health": "", "summary": ""}'
                    ), cwd=wt)
                storage.log_event(conn, "improvement_suggestions", improve_data)
                log.info("[domain/improve] overall_score=%s phase_scores=%s",
                         improve_data.get("overall_score"), improve_data.get("phase_scores", {}))
                try:
                    n_learnings = capture_learnings(
                        improve_output=improve_data,
                        run_id=run_id,
                        repo=repo,
                        issue_number=issue_number,
                    )
                    log.info("[domain/improve] learning_capture count=%d", n_learnings)
                except Exception as le:
                    log.warning("[domain/improve] learning_capture_failed error=%s", le)
            except (json.JSONDecodeError, Exception) as je:
                log.warning("[domain/improve] could not extract structured JSON: %s", je)
                storage.log_event(conn, "improvement_suggestions", {"raw": improve_text[:5000]})
        except Exception as ie:
            log.warning("[domain/improve] phase failed (non-blocking): %s", ie)
            if "pid" in dir():
                _finish_phase(conn, pid, None, failed=True)

        # ---- Push ------------------------------------------------------------
        diff_ok, diff_reason = gates.validate_pr_diff(wt, base=base_ref)
        storage.log_event(conn, "pr_diff_gate", {"passed": diff_ok, "reason": diff_reason})
        if not diff_ok:
            log.error("[domain/pr-diff-gate] FAIL: %s", diff_reason)
            raise GateFailure(f"pr-diff gate failed: {diff_reason}")
        destination.push_branch(wt, branch)
        log.info("[domain] branch pushed branch=%s review_signal=%s", branch, review_signal)
        storage.log_event(conn, "branch_pushed", {"branch": branch, "review_signal": review_signal})

        storage.finish_run(conn, "ok", total_cost=spent, branch=branch)
        result: dict = {
            "status": "ok",
            "branch": branch,
            "review_signal": review_signal,
            "spent_usd": spent,
            "run_id": run_id,
        }
        if issue_number is not None:
            source.post_comment(
                repo, issue_number,
                f"Branch pushed: {branch}, review: {review_signal}",
                provider=provider,
            )
        else:
            log.info("[domain] no issue_number — skipping completion comment")
        log.info("[domain] complete branch=%s review=%s spent=$%.2f", branch, review_signal, spent)
        return result

    except BudgetExceeded as e:
        log.error("[domain] budget exceeded: %s", e)
        storage.finish_run(conn, "budget_exceeded", total_cost=spent)
        _post_failure(repo, issue_number, str(e), provider=provider)
        return {"status": "error", "error": str(e), "spent_usd": spent, "run_id": run_id}
    except Exception as e:
        log.exception("[domain] pipeline failed")
        storage.finish_run(conn, "error", total_cost=spent)
        storage.log_event(conn, "pipeline_error", {"error": str(e)})
        _post_failure(repo, issue_number, str(e), provider=provider)
        return {"status": "error", "error": str(e), "spent_usd": spent, "run_id": run_id}
    finally:
        log.info("[domain] run complete — worktree preserved at %s (branch: %s)", wt, branch)
        conn.close()


def run_ops_pipeline(workflow: str, task_description: str, *,
                     model_override: str | None = None) -> dict:
    """3-phase pipeline for operational (non-code) workflows that produce markdown deliverables.

    Unlike run_domain_pipeline(), this function:
    - Has no git worktree — agents run with cwd set to the workflow directory itself
    - Uses {task_description} instead of {issue_body} in prompt templates
    - Writes the execute phase output to work/outputs/<workflow>/<date>-<task_slug>.md
    - No PR, no branch creation, no GitHub interaction
    - No budget guard (ops tasks are typically cheap)
    """
    healthy, detail = runtime._check_cli_health()
    if not healthy:
        log.error("aborting run_ops_pipeline: claude CLI health check failed: %s", detail)
        raise RuntimeError(f"claude CLI health check failed before run start: {detail}")
    wf_dir = _resolve_workflow_dir(workflow)
    # Same off-by-one class of bug as the worktree path resolution above:
    # parents[2] from engine/orchestrator.py lands on repos/, not the PA root.
    # That silently wrote ops outputs to repos/work/outputs/ instead of
    # work/outputs/ — found while auditing path resolution for the worktree fix.
    pa_root = Path(__file__).resolve().parents[3]
    log.info("run_ops_pipeline start workflow=%s wf_dir=%s task=%r pa_root=%s",
             workflow, wf_dir, task_description[:80], pa_root)

    wf = _load_workflow_config(wf_dir)
    log.info("loaded workflow config from %s", wf_dir)
    pcfg = _phase_cfg(wf)

    def load_prompt(phase: str) -> str:
        prompt_path = wf_dir / "prompts" / f"{phase}.md"
        if not prompt_path.is_file():
            raise FileNotFoundError(
                f"Prompt not found for phase '{phase}': {prompt_path}"
            )
        return prompt_path.read_text()

    def build_prior_text(prior: dict) -> str:
        if not prior:
            return ""
        return "\n\n---\n\n".join(
            f"## {name} phase output\n\n{text}" for name, text in prior.items()
        )

    def render_ops(template: str, prior: dict, extra: dict | None = None) -> str:
        out = template.replace("{task_description}", task_description)
        # Also replace {issue_body} in case a shared template uses it
        out = out.replace("{issue_body}", task_description)
        out = out.replace("{prior_phases}", build_prior_text(prior))
        # Individual phase outputs for prompts that need to distinguish them
        # e.g. {triage_output}, {execute_output}, {review_output}
        if prior:
            for phase_name, phase_text in prior.items():
                out = out.replace(f"{{{phase_name}_output}}", phase_text)
        if extra:
            for k, v in extra.items():
                out = out.replace(f"{{{k}}}", str(v))
        return out

    # Slugify the task description for use in the output filename
    task_slug = re.sub(r"[^a-z0-9]+", "-", task_description.lower()).strip("-")[:60]
    today = date.today().isoformat()

    # Fake repo/issue for storage compatibility (ops runs have no GitHub issue)
    fake_repo = f"ops/{workflow}"
    fake_issue = 0

    db_path, conn = storage.create_run_db(fake_repo, fake_issue, model=model_override)
    run_id = db_path.stem if hasattr(db_path, "stem") else str(db_path)
    log.info("run_ops_pipeline run_id=%s", run_id)

    prior: dict[str, str] = {}
    spent: float = 0.0
    cwd = str(wf_dir)

    try:
        # ---- Triage phase -------------------------------------------------------
        triage_cfg = pcfg.get("triage", {"model": "sonnet", "max_turns": 10})
        triage_model = _resolve_model(triage_cfg, model_override)
        pid = _start_phase(conn, "triage", model=triage_model)
        log.info("[ops/triage] start model=%s", triage_model)

        triage_prompt = render_ops(load_prompt("triage"), prior)
        triage_resp = runtime.call_agent(
            triage_prompt, model=triage_model, cwd=cwd,
            max_turns=triage_cfg.get("max_turns", 10),
        )
        triage_resp["_prompt"] = triage_prompt
        _check_resp("triage", triage_resp)
        _finish_phase(conn, pid, triage_resp)
        spent += triage_resp["cost"]
        triage_text = _content(triage_resp)
        prior["triage"] = triage_text
        storage.log_event(conn, "triage_complete", {
            "cost": triage_resp["cost"], "content_len": len(triage_text),
        })

        triage_signal = _parse_triage_signal(triage_text)
        log.info("[ops/triage] signal=%s spent=%.4f", triage_signal, spent)
        storage.log_event(conn, "triage_signal", {"signal": triage_signal})

        if triage_signal in ("SKIP", "ESCALATE"):
            log.info("[ops/triage] early exit signal=%s", triage_signal)
            storage.finish_run(conn, f"triage_{triage_signal.lower()}", total_cost=spent)
            return {"status": f"triage_{triage_signal.lower()}", "spent_usd": spent, "run_id": run_id}

        # ---- Execute phase -------------------------------------------------------
        execute_cfg = pcfg.get("execute", {"model": "sonnet", "max_turns": 30})
        execute_model = _resolve_model(execute_cfg, model_override)

        # Parse steps from triage output
        steps = _parse_triage_steps(triage_text)

        # Pre-compute output path for ops step execution
        out_dir = pa_root / "work" / "outputs" / workflow
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{today}-{task_slug}.md"

        if steps:
            log.info("[ops/execute] step mode: %d steps parsed from triage", len(steps))
            ops_step_spent, step_results = _execute_ops_steps(
                steps,
                conn=conn,
                wf_dir=wf_dir,
                cwd=cwd,
                execute_cfg=execute_cfg,
                execute_model=execute_model,
                task_description=task_description,
                triage_text=triage_text,
                out_path=out_path,
            )
            spent += ops_step_spent
            prior["execute"] = json.dumps(step_results, indent=2)
            storage.log_event(conn, "execute_complete", {
                "mode": "step",
                "total_steps": len(steps),
                "completed_steps": sum(1 for r in step_results if r["status"] == "completed"),
                "total_cost": ops_step_spent,
            })
        else:
            # Fallback: monolithic execute (no steps parsed from triage)
            log.warning("[ops/execute] no steps found in triage — falling back to monolithic execute")
            pid = _start_phase(conn, "execute", model=execute_model)
            log.info("[ops/execute] start model=%s prior_phases=%s", execute_model, list(prior.keys()))

            execute_prompt = render_ops(load_prompt("execute"), prior)
            execute_resp = runtime.call_agent(
                execute_prompt, model=execute_model, cwd=cwd,
                max_turns=execute_cfg.get("max_turns", 30),
            )
            execute_resp["_prompt"] = execute_prompt
            _check_resp("execute", execute_resp)
            _finish_phase(conn, pid, execute_resp)
            spent += execute_resp["cost"]
            execute_text = _content(execute_resp)
            prior["execute"] = execute_text
            # Write monolithic output to file
            out_path.write_text(execute_text)
            storage.log_event(conn, "execute_complete", {
                "mode": "monolithic",
                "cost": execute_resp["cost"],
                "content_len": len(execute_text),
            })
            log.info("[ops/execute] monolithic complete spent=%.4f content_len=%d", spent, len(execute_text))

        # ---- Review phase -------------------------------------------------------
        review_cfg = pcfg.get("review", {"model": "sonnet", "max_turns": 10})
        review_model = _resolve_model(review_cfg, model_override)
        pid = _start_phase(conn, "review", model=review_model)
        log.info("[ops/review] start model=%s", review_model)

        review_prompt = render_ops(load_prompt("review"), prior)
        review_resp = runtime.call_agent(
            review_prompt, model=review_model, cwd=cwd,
            max_turns=review_cfg.get("max_turns", 10),
        )
        review_resp["_prompt"] = review_prompt
        _finish_phase(conn, pid, review_resp)
        spent += review_resp["cost"]
        review_text = _content(review_resp)
        prior["review"] = review_text
        storage.log_event(conn, "review_complete", {
            "cost": review_resp["cost"], "content_len": len(review_text),
        })

        review_signal = _parse_review_signal(review_text)
        log.info("[ops/review] signal=%s spent=%.4f", review_signal, spent)
        storage.log_event(conn, "review_signal", {"signal": review_signal})

        # ---- Log output path -----------------------------------------------
        # (output file already written — by _execute_ops_steps in step mode,
        # or by monolithic fallback above)
        log.info("[ops] output at %s", out_path)
        storage.log_event(conn, "output_written", {"path": str(out_path)})

        storage.finish_run(conn, "ok", total_cost=spent)
        result: dict = {
            "status": "ok",
            "output_path": str(out_path),
            "spent_usd": spent,
            "run_id": run_id,
            "review_signal": review_signal,
        }
        log.info("run_ops_pipeline complete status=ok output=%s spent=%.4f review_signal=%s",
                 out_path, spent, review_signal)
        return result

    except Exception as e:
        log.exception("[ops] pipeline failed")
        storage.finish_run(conn, "error", total_cost=spent)
        storage.log_event(conn, "pipeline_error", {"error": str(e)})
        return {"status": "error", "error": str(e), "spent_usd": spent, "run_id": run_id}
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="github_claude: issue -> PR pipeline")
    ap.add_argument("repo", nargs="?", help="owner/repo (required for code workflows, optional for ops)")
    ap.add_argument("issue", nargs="?", type=int, help="Issue number (required for code workflows)")
    ap.add_argument("--budget", type=float, default=None,
                    help="Max spend USD (default: 10.00 for domain workflows, 1.00 for generic)")
    ap.add_argument("--model", default=None, help="Override model for all phases")
    ap.add_argument("--repo-path", default=None, help="Local filesystem path to the repo (default: cwd)")
    ap.add_argument("--resume", default=None, help="Resume from a prior run DB path or run ID")
    ap.add_argument("--workflow", default=None,
                    help="Workflow name (e.g., shftty-web, cody-business). "
                         "If the workflow.yaml has type: ops, uses run_ops_pipeline().")
    ap.add_argument("--task", default=None,
                    help="Task description for ops workflows (replaces GitHub issue number).")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    # Detect ops workflow
    if args.workflow:
        wf_cfg = _load_workflow_config(_resolve_workflow_dir(args.workflow))
        wf_type = wf_cfg.get("type")

        if wf_type == "ops" or args.task:
            if not args.task:
                ap.error("--task is required for ops workflows")
            print(f"github_claude (ops): workflow={args.workflow}  task={args.task!r}")
            result = run_ops_pipeline(
                args.workflow, args.task,
                model_override=args.model,
            )
        else:
            if args.repo is None or args.issue is None:
                ap.error("repo and issue are required for code workflows")
            effective_budget = args.budget if args.budget is not None else 10.00
            print(f"github_claude (domain): {args.repo}#{args.issue}  "
                  f"workflow={args.workflow}  budget=${effective_budget:.2f}")
            result = run_domain_pipeline(
                args.repo, args.issue,
                workflow=args.workflow,
                budget=effective_budget,
                model_override=args.model,
                repo_path=args.repo_path,
            )
    else:
        if args.repo is None or args.issue is None:
            ap.error("repo and issue are required")
        effective_budget = args.budget if args.budget is not None else 1.00
        print(f"github_claude: {args.repo}#{args.issue}  budget=${effective_budget:.2f}")
        result = run_pipeline(args.repo, args.issue, budget=effective_budget,
                              model_override=args.model, repo_path=args.repo_path,
                              resume_from=args.resume)

    print(f"\n{'='*50}")
    for k, label in [("status","Status"), ("spent_usd","Cost"), ("pr_url","PR"),
                     ("output_path","Output"), ("review_signal","Review"), ("error","Error")]:
        v = result.get(k)
        if v is not None:
            print(f"  {label}: {'${:.4f}'.format(v) if k == 'spent_usd' else v}")
    print(f"{'='*50}")
    sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
