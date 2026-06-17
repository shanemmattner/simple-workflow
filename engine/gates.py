"""Validation gates and post-phase checks.

All gates are zero-token, millisecond operations except the red/green gates.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

ALLOWED_TEST_PREFIXES = (
    "pytest",
    "python -m pytest",
    "vitest",
    "jest",
    "cargo test",
    "go test",
    "npm test",
    "npm run test",
    "swift test",
    "xcodebuild test",
)


def _validate_test_command(command: str) -> str | None:
    """Return error string if command is not on the allowlist, else None."""
    for prefix in ALLOWED_TEST_PREFIXES:
        if command == prefix or command.startswith(prefix + " "):
            return None
    return f"test command not on allowlist: {command!r}"


def _file_exists_fuzzy(filepath: str, worktree: Path) -> bool:
    """Check if a file exists, falling back to filename search for abbreviated paths."""
    # Exact match first
    if (worktree / filepath).exists():
        return True
    # Fuzzy fallback: search for the filename anywhere in the worktree
    filename = Path(filepath).name
    matches = list(worktree.rglob(filename))
    return len(matches) > 0


def validate_triage(output: dict, worktree_path: str) -> list[str]:
    errors: list[str] = []
    tasks = output.get("tasks", [])

    if len(tasks) > 5:
        errors.append(f"task count {len(tasks)} exceeds maximum of 5")

    all_files: list[str] = []
    for task in tasks:
        all_files.extend(task.get("target_files", []))

    if all_files:
        worktree = Path(worktree_path)
        existing = sum(
            1 for f in all_files if _file_exists_fuzzy(f, worktree)
        )
        ratio = existing / len(all_files)
        if ratio < 0.5:
            errors.append(
                f"only {existing}/{len(all_files)} target files exist "
                f"({ratio:.0%} < 50% threshold)"
            )

    return errors


def validate_plan(output: dict, worktree_path: str) -> list[str]:
    errors: list[str] = []
    steps = output.get("steps", [])

    # DAG cycle detection via topological sort (Kahn's algorithm)
    graph: dict[int, list[int]] = {}
    in_degree: dict[int, int] = {}
    step_ids = set()

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
        errors.append("plan DAG contains a cycle")

    for step in steps:
        for filepath in step.get("writes", []):
            parent = Path(worktree_path, filepath).parent
            if not parent.exists():
                log.warning("parent dir does not exist: %s", parent)

    return errors


def validate_test_plan(output: dict) -> list[str]:
    errors: list[str] = []

    test_file = output.get("test_file", "")
    if not test_file or not test_file.strip():
        errors.append("test_file is empty")

    test_command = output.get("test_command", "")
    if not test_command or not test_command.strip():
        errors.append("test_command is empty")

    return errors


def validate_wave_planner(
    output: dict, task_ids: list[int], max_parallel: int
) -> list[str]:
    errors: list[str] = []
    waves = output.get("waves", [])

    seen: set[int] = set()
    assigned: list[int] = []

    for i, wave in enumerate(waves):
        tasks = wave.get("tasks", [])
        if len(tasks) > max_parallel:
            errors.append(
                f"wave {i} has {len(tasks)} tasks, exceeds max_parallel={max_parallel}"
            )
        for tid in tasks:
            if tid in seen:
                errors.append(f"duplicate task ID {tid} in wave plan")
            seen.add(tid)
            assigned.append(tid)

    expected = set(task_ids)
    missing = expected - seen
    extra = seen - expected

    if missing:
        errors.append(f"task IDs missing from wave plan: {sorted(missing)}")
    if extra:
        errors.append(f"unexpected task IDs in wave plan: {sorted(extra)}")

    return errors


def run_red_gate(
    test_command: str, worktree_path: str, timeout: int = 120
) -> tuple[bool, str]:
    err = _validate_test_command(test_command)
    if err:
        return False, err

    try:
        args = shlex.split(test_command)
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=worktree_path,
        )
    except subprocess.TimeoutExpired:
        return False, "test command timed out"
    except Exception as exc:
        return False, f"test command error: {exc}"

    if proc.returncode == 0:
        return False, "tests pass before implementation"
    return True, "red confirmed"


def run_green_gate(
    test_command: str, worktree_path: str, timeout: int = 120
) -> tuple[bool, str]:
    err = _validate_test_command(test_command)
    if err:
        return False, err

    try:
        args = shlex.split(test_command)
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=worktree_path,
        )
    except subprocess.TimeoutExpired:
        return False, "test command timed out"
    except Exception as exc:
        return False, f"test command error: {exc}"

    if proc.returncode == 0:
        return True, "tests pass"
    return False, "tests still failing after implementation"


def check_commits_exist(
    worktree_path: str, base_branch: str
) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", "log", "--oneline", f"{base_branch}..HEAD"],
            capture_output=True,
            text=True,
            cwd=worktree_path,
            timeout=10,
        )
    except Exception as exc:
        return False, f"git log failed: {exc}"

    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    if not lines:
        return False, "no commits produced"
    return True, f"{len(lines)} commits"
