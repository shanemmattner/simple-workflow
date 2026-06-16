"""Wave execution and parallel dispatch."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from engine import gates
from engine.agent import run_agent

log = logging.getLogger(__name__)

PROGRESS_FILE = "build-progress.json"


def load_progress(run_dir: str) -> dict | None:
    path = Path(run_dir) / PROGRESS_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_progress(run_dir: str, progress: dict) -> None:
    path = Path(run_dir) / PROGRESS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(progress, indent=2), encoding="utf-8")


def execute_task(
    task_id: int,
    task_context: dict,
    *,
    run_dir: str,
    db: Any,
    run_id: str,
) -> dict:
    """Execute a single task: write tests -> red gate -> implement -> green gate.

    Returns {task_id, success, commits, gate_results, error}.
    """
    result: dict[str, Any] = {
        "task_id": task_id,
        "success": False,
        "commits": [],
        "gate_results": {},
        "error": None,
    }

    worktree_path = task_context.get("worktree_path", "")
    test_command = task_context.get("test_command", "")
    prompt = task_context.get("execute_prompt", "")
    model = task_context.get("model", "sonnet")
    base_branch = task_context.get("base_branch", "main")
    phase_label = f"execute-task-{task_id}"

    # Agent call: write tests + implement
    agent_result = run_agent(
        prompt,
        model=model,
        worktree_path=worktree_path,
        run_dir=run_dir,
        phase_label=phase_label,
    )

    if db:
        from db import log_phase

        log_phase(
            db,
            run_id,
            phase_label,
            model=model,
            duration_s=agent_result.duration_s,
            tokens_in=agent_result.tokens_in,
            tokens_out=agent_result.tokens_out,
            cost_usd=agent_result.cost_usd,
            num_turns=agent_result.num_turns,
            stop_reason=agent_result.stop_reason,
            prompt_hash=agent_result.prompt_hash,
            output_hash=agent_result.output_hash,
            output_path=agent_result.output_path,
        )

    # Red gate: tests must fail before implementation
    if test_command:
        red_ok, red_msg = gates.run_red_gate(test_command, worktree_path)
        result["gate_results"]["red"] = {"passed": red_ok, "message": red_msg}

        if not red_ok:
            result["error"] = f"red gate failed: {red_msg}"
            return result

    # Green gate: tests must pass after implementation
    if test_command:
        green_ok, green_msg = gates.run_green_gate(test_command, worktree_path)
        result["gate_results"]["green"] = {
            "passed": green_ok,
            "message": green_msg,
        }

        if not green_ok:
            # One retry
            log.info("task %d green gate failed, retrying agent call", task_id)
            retry_prompt = (
                f"{prompt}\n\nThe tests are still failing. "
                f"Error: {green_msg}. Fix the implementation."
            )
            retry_result = run_agent(
                retry_prompt,
                model=model,
                worktree_path=worktree_path,
                run_dir=run_dir,
                phase_label=f"{phase_label}-retry",
            )

            if db:
                log_phase(
                    db,
                    run_id,
                    f"{phase_label}-retry",
                    model=model,
                    duration_s=retry_result.duration_s,
                    tokens_in=retry_result.tokens_in,
                    tokens_out=retry_result.tokens_out,
                    cost_usd=retry_result.cost_usd,
                    num_turns=retry_result.num_turns,
                    stop_reason=retry_result.stop_reason,
                    prompt_hash=retry_result.prompt_hash,
                    output_hash=retry_result.output_hash,
                    output_path=retry_result.output_path,
                )

            green_ok2, green_msg2 = gates.run_green_gate(
                test_command, worktree_path
            )
            result["gate_results"]["green_retry"] = {
                "passed": green_ok2,
                "message": green_msg2,
            }

            if not green_ok2:
                result["error"] = f"green gate failed after retry: {green_msg2}"
                return result

    # Check commits
    commits_ok, commits_msg = gates.check_commits_exist(
        worktree_path, base_branch
    )
    result["gate_results"]["commits"] = {
        "passed": commits_ok,
        "message": commits_msg,
    }

    result["success"] = commits_ok
    if not commits_ok:
        result["error"] = commits_msg

    return result


def execute_waves(
    wave_plan: dict,
    task_contexts: dict,
    *,
    max_parallel: int = 5,
    run_dir: str,
    db: Any,
    run_id: str,
) -> dict:
    """Execute wave plan: parallel within waves, serial across waves."""
    waves = wave_plan.get("waves", [])
    all_results: list[dict] = []
    completed_tasks: set[int] = set()

    prior = load_progress(run_dir)
    if prior:
        completed_tasks = set(prior.get("completed_tasks", []))
        all_results = prior.get("results", [])
        log.info("resuming from checkpoint, %d tasks done", len(completed_tasks))

    for wave_idx, wave in enumerate(waves):
        task_ids = [
            tid for tid in wave.get("tasks", [])
            if tid not in completed_tasks
        ]

        if not task_ids:
            continue

        log.info("wave %d: dispatching %d tasks", wave_idx, len(task_ids))
        wave_results: list[dict] = []

        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            futures = {}
            for tid in task_ids:
                ctx = task_contexts.get(tid, {})
                future = pool.submit(
                    execute_task,
                    tid,
                    ctx,
                    run_dir=run_dir,
                    db=db,
                    run_id=run_id,
                )
                futures[future] = tid

            for future in as_completed(futures):
                tid = futures[future]
                try:
                    task_result = future.result()
                except Exception as exc:
                    task_result = {
                        "task_id": tid,
                        "success": False,
                        "commits": [],
                        "gate_results": {},
                        "error": str(exc),
                    }
                wave_results.append(task_result)
                completed_tasks.add(tid)

        all_results.extend(wave_results)

        save_progress(run_dir, {
            "completed_tasks": sorted(completed_tasks),
            "results": all_results,
        })

        succeeded = sum(1 for r in wave_results if r.get("success"))
        log.info(
            "wave %d complete: %d/%d tasks succeeded",
            wave_idx,
            succeeded,
            len(wave_results),
        )

    total = len(all_results)
    passed = sum(1 for r in all_results if r.get("success"))

    return {
        "total_tasks": total,
        "passed": passed,
        "failed": total - passed,
        "results": all_results,
    }
