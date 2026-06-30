"""Phase-output gates for the github_openhands engine.

Each gate is a plain function that returns a dict:
    {"passed": bool, "reason": str}

No ABCs, no imports from engine/.  Logging to storage is the caller's
responsibility — gates just return verdicts.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Security allowlist for test commands
# ---------------------------------------------------------------------------

ALLOWED_TEST_PREFIXES = (
    "pytest",
    "python -m pytest",
    "python3 -m pytest",
    "vitest",
    "jest",
    "cargo test",
    "go test",
    "npm test",
    "npm run test",
    "swift test",
    "xcodebuild test",
    "./scripts/",
)

_CD_PREFIX_RE = re.compile(r"^cd\s+\S+\s+&&\s+")


def check_test_command_allowed(command: str) -> dict:
    """Security gate: is this test command on the allowlist?"""
    check = _CD_PREFIX_RE.sub("", command)
    for prefix in ALLOWED_TEST_PREFIXES:
        if check == prefix or check.startswith(prefix + " "):
            return {"passed": True, "reason": "command on allowlist"}
    return {"passed": False, "reason": f"test command not on allowlist: {command!r}"}


# ---------------------------------------------------------------------------
# Phase output validators
# ---------------------------------------------------------------------------

def validate_triage(output: dict, worktree_path: str) -> dict:
    """Check triage output: task count, target-file existence (warning only).

    File existence below 50% is a WARNING, not a gate failure — create-file
    tasks produce target_files that don't exist yet by design.

    Note: the openhands engine extracts proof_type at the top level of the
    triage JSON (not per-task), so we log it but do not gate on it.
    """
    tasks = output.get("tasks", [])
    proof_type = output.get("proof_type", "")
    log.info("[gate/triage] checking %d task(s), top-level proof_type=%r", len(tasks), proof_type)

    if len(tasks) > 5:
        reason = f"task count {len(tasks)} exceeds maximum of 5"
        log.warning("[gate/triage] FAIL: %s", reason)
        return {"passed": False, "reason": reason}

    all_files: list[str] = []
    for task in tasks:
        tf = task.get("target_files", [])
        log.debug("[gate/triage] task %s target_files: %s", task.get("id"), tf)
        all_files.extend(tf)

    if all_files:
        wt = Path(worktree_path)
        file_checks: list[tuple[str, bool]] = [
            (f, _file_exists_fuzzy(f, wt)) for f in all_files
        ]
        existing = sum(1 for _, exists in file_checks if exists)
        ratio = existing / len(all_files)
        log.info(
            "[gate/triage] file existence: %d/%d (%.0f%%) — %s",
            existing, len(all_files), ratio * 100,
            "OK" if ratio >= 0.5 else "LOW (create-file tasks expected — warning only)",
        )
        for filepath, exists in file_checks:
            log.debug("[gate/triage]   %s %s", "EXISTS" if exists else "ABSENT", filepath)
        if ratio < 0.5:
            log.warning(
                "[gate/triage] triage file existence low: %d/%d (%.0f%%) — "
                "this is normal for create-file issues; verify phase will confirm claims",
                existing, len(all_files), ratio * 100,
            )
    else:
        log.info("[gate/triage] no target_files listed — skipping file existence check")

    log.info("[gate/triage] PASS: triage output valid")
    return {"passed": True, "reason": "triage output valid"}


def validate_verify(output: dict) -> dict:
    """Check verify output: verified_tasks present, valid statuses."""
    tasks = output.get("verified_tasks", [])
    log.info("[gate/verify] checking %d verified_task(s)", len(tasks))

    if not tasks:
        log.warning("[gate/verify] FAIL: verified_tasks is empty")
        return {"passed": False, "reason": "verified_tasks is empty"}

    valid_statuses = {"CONFIRMED", "REFUTED", "STALE", "PARTIAL"}
    for t in tasks:
        status = t.get("status", "").upper()
        log.info(
            "[gate/verify] task %s status=%s evidence=%r",
            t.get("task_id"), status, t.get("evidence", "")[:120],
        )
        if status not in valid_statuses:
            reason = f"task {t.get('task_id')} has invalid status: {t.get('status')!r}"
            log.warning("[gate/verify] FAIL: %s", reason)
            return {"passed": False, "reason": reason}

    recommendation = output.get("recommendation", "")
    log.info("[gate/verify] recommendation=%r", recommendation)
    if recommendation not in ("proceed", "already_fixed", "needs_clarification"):
        reason = f"invalid recommendation: {recommendation!r}"
        log.warning("[gate/verify] FAIL: %s", reason)
        return {"passed": False, "reason": reason}

    log.info("[gate/verify] PASS: verify output valid")
    return {"passed": True, "reason": "verify output valid"}


def validate_plan(output: dict, worktree_path: str) -> dict:
    """Check plan output: DAG acyclic, file paths plausible."""
    steps = output.get("steps", [])

    # DAG cycle detection (Kahn's algorithm)
    graph: dict[int, list[int]] = {}
    in_degree: dict[int, int] = {}
    step_ids: set[int] = set()

    for step in steps:
        sid = step.get("id")
        step_ids.add(sid)
        graph.setdefault(sid, [])
        in_degree.setdefault(sid, 0)

    for step in steps:
        sid = step.get("id")
        for dep in step.get("depends_on", []):
            if dep in step_ids:
                graph.setdefault(dep, []).append(sid)
                in_degree[sid] = in_degree.get(sid, 0) + 1

    queue = [n for n in step_ids if in_degree.get(n, 0) == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for neighbor in graph.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(step_ids):
        return {"passed": False, "reason": "plan DAG contains a cycle"}

    return {"passed": True, "reason": "plan output valid"}


def validate_test_plan(output: dict) -> dict:
    """Check test-plan output: file and command specified."""
    test_file = output.get("test_file", "")
    if not test_file or not test_file.strip():
        return {"passed": False, "reason": "test_file is empty"}

    test_command = output.get("test_command", "")
    if not test_command or not test_command.strip():
        return {"passed": False, "reason": "test_command is empty"}

    return {"passed": True, "reason": "test plan output valid"}


def validate_wave_plan(
    output: dict, task_ids: list[int], max_parallel: int
) -> dict:
    """Check wave-planner output: all tasks assigned, no dupes, size ok."""
    waves = output.get("waves", [])
    errors: list[str] = []

    seen: set[int] = set()
    for i, wave in enumerate(waves):
        tasks = wave if isinstance(wave, list) else wave.get("tasks", [])
        if len(tasks) > max_parallel:
            errors.append(
                f"wave {i} has {len(tasks)} tasks, exceeds max_parallel={max_parallel}"
            )
        for tid in tasks:
            if tid in seen:
                errors.append(f"duplicate task ID {tid}")
            seen.add(tid)

    expected = set(task_ids)
    missing = expected - seen
    extra = seen - expected
    if missing:
        errors.append(f"missing task IDs: {sorted(missing)}")
    if extra:
        errors.append(f"unexpected task IDs: {sorted(extra)}")

    if errors:
        return {"passed": False, "reason": "; ".join(errors)}
    return {"passed": True, "reason": "wave plan valid"}


# ---------------------------------------------------------------------------
# Execute-phase gates (these shell out)
# ---------------------------------------------------------------------------

def run_red_gate(
    test_command: str, worktree_path: str, timeout: int = 120
) -> dict:
    """Red gate: tests must FAIL before implementation (TDD red step)."""
    sec = check_test_command_allowed(test_command)
    if not sec["passed"]:
        return sec

    passed, reason = _run_test(test_command, worktree_path, timeout)
    if passed is None:
        return {"passed": False, "reason": reason}  # error / timeout
    if passed:
        return {"passed": False, "reason": "tests pass before implementation"}
    return {"passed": True, "reason": "red confirmed"}


def run_green_gate(
    test_command: str, worktree_path: str, timeout: int = 120
) -> dict:
    """Green gate: tests must PASS after implementation."""
    sec = check_test_command_allowed(test_command)
    if not sec["passed"]:
        return sec

    passed, reason = _run_test(test_command, worktree_path, timeout)
    if passed is None:
        return {"passed": False, "reason": reason}
    if passed:
        return {"passed": True, "reason": "tests pass"}
    return {"passed": False, "reason": "tests still failing after implementation"}


def check_commits_exist(worktree_path: str, base_branch: str) -> dict:
    """Verify the agent produced at least one commit."""
    try:
        proc = subprocess.run(
            ["git", "log", "--oneline", f"{base_branch}..HEAD"],
            capture_output=True, text=True,
            cwd=worktree_path, timeout=10,
        )
    except Exception as exc:
        return {"passed": False, "reason": f"git log failed: {exc}"}

    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return {"passed": False, "reason": "no commits produced"}
    return {"passed": True, "reason": f"{len(lines)} commit(s)"}


# ---------------------------------------------------------------------------
# Convenience: run all gates for a phase
# ---------------------------------------------------------------------------

def run_phase_gates(
    phase_name: str,
    output: dict,
    *,
    worktree_path: str = "",
    task_ids: list[int] | None = None,
    max_parallel: int = 5,
    test_command: str = "",
    base_branch: str = "main",
) -> list[dict]:
    """Run every declared gate for a phase. Returns list of result dicts.

    Each dict: {"gate": str, "passed": bool, "reason": str}
    Stops on first failure (fail-fast).
    """
    results: list[dict] = []

    def _record(gate_name: str, result: dict) -> bool:
        results.append({"gate": gate_name, **result})
        return result["passed"]

    if phase_name == "triage":
        if not _record("validate_triage", validate_triage(output, worktree_path)):
            return results

    elif phase_name == "verify":
        if not _record("validate_verify", validate_verify(output)):
            return results

    elif phase_name == "plan":
        if not _record("validate_plan", validate_plan(output, worktree_path)):
            return results

    elif phase_name == "test-plan":
        if not _record("validate_test_plan", validate_test_plan(output)):
            return results

    elif phase_name == "wave-planner":
        if not _record(
            "validate_wave_plan",
            validate_wave_plan(output, task_ids or [], max_parallel),
        ):
            return results

    elif phase_name == "execute":
        if test_command:
            if not _record(
                "check_test_command_allowed",
                check_test_command_allowed(test_command),
            ):
                return results
        # red/green/commit gates are run by the caller at the right moment
        # (before impl, after impl, after commit) — not batch-runnable here.

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _file_exists_fuzzy(filepath: str, worktree: Path) -> bool:
    """Check if file exists, with filename-only fallback."""
    if (worktree / filepath).exists():
        return True
    filename = Path(filepath).name
    return len(list(worktree.rglob(filename))) > 0


def _run_test(
    command: str, worktree_path: str, timeout: int
) -> tuple[bool | None, str]:
    """Run a test command. Returns (passed, reason).

    passed is None on error/timeout.
    """
    try:
        args = shlex.split(command)
        proc = subprocess.run(
            args,
            capture_output=True, text=True,
            timeout=timeout, cwd=worktree_path,
        )
    except subprocess.TimeoutExpired:
        return None, "test command timed out"
    except Exception as exc:
        return None, f"test command error: {exc}"

    return (proc.returncode == 0), ""
