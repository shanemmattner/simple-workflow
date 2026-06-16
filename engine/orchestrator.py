"""CLI entry point and phase dispatcher for simple_workflow.

Usage:
    python3 -m engine.orchestrator owner/repo#123 [--budget 2.00] [--model sonnet]
    python3 -m engine.orchestrator owner/repo#123 --workflow issue-to-pr --workflow-dir path
    python3 -m engine.orchestrator owner/repo#123 --gitlab   # force GitLab mode
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from engine.agent import AgentResult, run_agent
from engine import gates
from engine import waves
from engine.worktree import worktree as worktree_ctx, cleanup_worktree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db as db_mod
import schemas

log = logging.getLogger(__name__)

PLATFORM_GITHUB = "github"
PLATFORM_GITLAB = "gitlab"

KNOWLEDGE_TOKEN_CAP = 8000  # 8K tokens ~= 32K chars / 4
KNOWLEDGE_FILES = {
    "context.md": {"phases": None},  # all phases
    "testing.md": {"phases": {"test-plan", "execute"}},
    "knowledge/architecture.md": {"phases": {"plan", "execute"}},
    "knowledge/hard-rules.md": {"phases": None},
    "knowledge/common-pitfalls.md": {"phases": {"execute", "review"}},
}


def parse_issue_ref(ref: str) -> tuple[str, str, int]:
    if "#" not in ref:
        raise ValueError(f"Invalid issue ref {ref!r} — expected owner/repo#NNN")
    repo_part, number_str = ref.rsplit("#", 1)
    return repo_part.split("/")[0], repo_part.split("/")[1], int(number_str)


def detect_platform(repo_path: str) -> str:
    """Auto-detect GitHub vs GitLab from the git remote URL."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=repo_path, timeout=10,
        )
        url = result.stdout.strip().lower()
        if "gitlab.com" in url or "gitlab" in url:
            return PLATFORM_GITLAB
    except Exception:
        pass
    return PLATFORM_GITHUB


def _gitlab_project_from_remote(target_repo: str) -> str | None:
    """Extract the GitLab project path (owner/project) from the git remote URL."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=target_repo, timeout=10,
        )
        url = result.stdout.strip()
        # git@gitlab.com:owner/project.git
        if url.startswith("git@"):
            path = url.split(":", 1)[1]
            return path.removesuffix(".git")
        # https://gitlab.com/owner/project.git
        if "gitlab.com" in url:
            # strip scheme + host, take the path
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.path.lstrip("/").removesuffix(".git")
    except Exception:
        pass
    return None


def fetch_issue(
    owner: str, repo: str, number: int,
    *, platform: str = PLATFORM_GITHUB, target_repo: str = "",
) -> str:
    if platform == PLATFORM_GITLAB:
        # When target_repo is available, read the actual GitLab project path
        # from the git remote (the local dir name may differ from the remote).
        project = f"{owner}/{repo}"
        if target_repo:
            remote_project = _gitlab_project_from_remote(target_repo)
            if remote_project:
                project = remote_project
        result = subprocess.run(
            ["glab", "issue", "view", str(number),
             "-R", project, "--output", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"glab issue view failed: {result.stderr.strip()}")
        data = json.loads(result.stdout)
        title = data.get("title", "")
        description = data.get("description", "")
        return f"# {title}\n\n{description}"
    else:
        result = subprocess.run(
            ["gh", "issue", "view", str(number), "--repo", f"{owner}/{repo}",
             "--json", "body", "--jq", ".body"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh issue view failed: {result.stderr.strip()}")
        return result.stdout.strip()


def load_workflow(workflow_dir: Path) -> dict:
    yaml_path = workflow_dir / "workflow.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"workflow.yaml not found at {yaml_path}")
    return yaml.safe_load(yaml_path.read_text(encoding="utf-8"))


def inject_context(
    template: str,
    *,
    issue_number: int,
    issue_body: str,
    prior_phases: dict[str, Any],
    worktree_path: str,
    workflow_dir: Path,
    phase: str,
) -> str:
    """Build full prompt from template + repo context + knowledge files."""
    context_parts: list[str] = []

    # Repo context: .workflows/context.md from worktree, fallback to prompts/_repo-context.md
    wt_context = Path(worktree_path) / ".workflows" / "context.md"
    fallback_context = workflow_dir / "prompts" / "_repo-context.md"
    if wt_context.exists():
        context_parts.append(wt_context.read_text(encoding="utf-8"))
    elif fallback_context.exists():
        context_parts.append(fallback_context.read_text(encoding="utf-8"))

    # Testing context for relevant phases
    if phase in ("test-plan", "execute"):
        wt_testing = Path(worktree_path) / ".workflows" / "testing.md"
        if wt_testing.exists():
            context_parts.append(wt_testing.read_text(encoding="utf-8"))

    # Knowledge files with progressive disclosure
    total_chars = 0
    char_cap = KNOWLEDGE_TOKEN_CAP * 4
    for filename, config in KNOWLEDGE_FILES.items():
        allowed_phases = config["phases"]
        if allowed_phases is not None and phase not in allowed_phases:
            continue

        knowledge_path = Path(worktree_path) / ".workflows" / filename
        if not knowledge_path.exists():
            continue

        content = knowledge_path.read_text(encoding="utf-8")
        if total_chars + len(content) > char_cap:
            remaining = char_cap - total_chars
            if remaining > 200:
                content = content[:remaining] + "\n\n[TRUNCATED — knowledge cap reached]"
            else:
                break
        context_parts.append(content)
        total_chars += len(content)

    # Render template placeholders
    prior_text = ""
    if prior_phases:
        prior_text = json.dumps(prior_phases, indent=2, default=str)

    rendered = template.replace("{issue_number}", str(issue_number))
    rendered = rendered.replace("{issue_body}", issue_body)
    rendered = rendered.replace("{prior_phases}", prior_text)

    if context_parts:
        context_block = "\n\n---\n\n".join(context_parts)
        return f"{context_block}\n\n---\n\n{rendered}"
    return rendered


def _log_agent_result(
    conn: Any,
    run_id: str,
    phase: str,
    result: AgentResult,
    model: str,
    parse_success: bool = True,
    validation_errors: str = "",
) -> None:
    db_mod.log_phase(
        conn, run_id, phase,
        model=model,
        duration_s=result.duration_s,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
        num_turns=result.num_turns,
        stop_reason=result.stop_reason,
        prompt_hash=result.prompt_hash,
        output_hash=result.output_hash,
        output_path=result.output_path,
        parse_success=int(parse_success),
        validation_errors=validation_errors,
    )


def _load_prompt(workflow_dir: Path, phase: str) -> str:
    path = workflow_dir / "prompts" / f"{phase}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def _check_budget(spent: float, budget: float) -> None:
    if spent > budget:
        raise BudgetExceeded(f"budget exceeded: ${spent:.2f} > ${budget:.2f}")


class BudgetExceeded(RuntimeError):
    pass


class ValidationKill(RuntimeError):
    def __init__(self, phase: str, errors: list[str]):
        self.phase = phase
        self.errors = errors
        super().__init__(f"validation failed in {phase}: {'; '.join(errors)}")


def run_pipeline(
    owner: str,
    repo: str,
    issue_number: int,
    *,
    budget: float = 1.00,
    model: str = "sonnet",
    workflow_name: str = "issue-to-pr",
    workflow_dir: Path | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parent.parent
    if workflow_dir is None:
        workflow_dir = repo_root / "workflows" / workflow_name

    workflow = load_workflow(workflow_dir)
    budget = budget or workflow.get("budget", {}).get("max_per_run_usd", 1.00)
    max_parallel = workflow.get("max_parallel_workers", 5)
    phase_models = {p["name"]: p.get("model", model) for p in workflow.get("phases", [])}

    run_id = str(uuid.uuid4())
    db_path = str(repo_root / "runs" / "runs.db")
    conn = db_mod.init_db(db_path)
    config_snapshot = json.dumps(workflow, default=str)
    db_mod.create_run(conn, run_id, f"{owner}/{repo}#{issue_number}",
                      workflow_name, model, config_snapshot, budget)

    run_dir = repo_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run_dir_str = str(run_dir)

    prior_phases: dict[str, Any] = {}
    spent_usd = 0.0
    pr_url = ""

    # Resolve target repo path — the repo we're creating a worktree for.
    # For now: clone or locate it. Assumes repo is already cloned locally.
    # Use the worktree_path as the target repo root for git operations.
    target_repo = _find_target_repo(owner, repo)

    # Auto-detect platform if not explicitly set
    if platform is None:
        platform = detect_platform(target_repo)
    print(f"  platform: {platform}")

    issue_body = fetch_issue(owner, repo, issue_number, platform=platform, target_repo=target_repo)

    branch_name = f"issue-{issue_number}-{run_id[:8]}"
    worktree_path = ""

    try:
        worktree_path = _create_worktree(target_repo, branch_name)
        print(f"[run {run_id[:8]}] worktree: {worktree_path}")

        # --- Phase 1: Triage ---
        triage_result, triage_agent = _run_phase(
            "triage", phase_models.get("triage", "haiku"),
            workflow_dir=workflow_dir, worktree_path=worktree_path,
            issue_number=issue_number, issue_body=issue_body,
            prior_phases=prior_phases, run_dir=run_dir_str,
        )
        spent_usd += triage_agent.cost_usd

        try:
            triage_output = schemas.TriageOutput.model_validate(triage_result)
        except ValidationError as exc:
            _log_agent_result(conn, run_id, "triage", triage_agent,
                              phase_models.get("triage", "haiku"),
                              parse_success=False, validation_errors=str(exc))
            raise ValidationKill("triage", [str(exc)])

        _log_agent_result(conn, run_id, "triage", triage_agent, phase_models.get("triage", "haiku"))

        triage_errors = gates.validate_triage(triage_result, worktree_path)
        if triage_errors:
            raise ValidationKill("triage", triage_errors)

        prior_phases["triage"] = triage_result
        db_mod.update_spent(conn, run_id, spent_usd)
        _check_budget(spent_usd, budget)

        tasks = triage_output.tasks
        task_ids = [t.id for t in tasks]

        # --- Phase 2: Plan + Test Plan (parallel per task) ---
        plan_results: dict[int, dict] = {}
        test_plan_results: dict[int, dict] = {}

        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            futures: dict[Any, tuple[str, int]] = {}

            for task in tasks:
                task_prior = {**prior_phases, "_current_task": task.model_dump()}

                plan_future = pool.submit(
                    _run_phase,
                    "plan", phase_models.get("plan", model),
                    workflow_dir=workflow_dir, worktree_path=worktree_path,
                    issue_number=issue_number, issue_body=issue_body,
                    prior_phases=task_prior, run_dir=run_dir_str,
                    phase_label=f"plan-task-{task.id}",
                )
                futures[plan_future] = ("plan", task.id)

                tp_future = pool.submit(
                    _run_phase,
                    "test-plan", phase_models.get("test-plan", model),
                    workflow_dir=workflow_dir, worktree_path=worktree_path,
                    issue_number=issue_number, issue_body=issue_body,
                    prior_phases=task_prior, run_dir=run_dir_str,
                    phase_label=f"test-plan-task-{task.id}",
                )
                futures[tp_future] = ("test-plan", task.id)

            for future in as_completed(futures):
                phase_type, task_id = futures[future]
                parsed, agent_res = future.result()
                phase_label = f"{phase_type}-task-{task_id}"
                _log_agent_result(conn, run_id, phase_label, agent_res,
                                  phase_models.get(phase_type, model))
                spent_usd += agent_res.cost_usd

                if phase_type == "plan":
                    try:
                        schemas.PlanOutput.model_validate(parsed)
                    except ValidationError as exc:
                        raise ValidationKill(phase_label, [str(exc)])

                    plan_errors = gates.validate_plan(parsed, worktree_path)
                    if plan_errors:
                        raise ValidationKill(phase_label, plan_errors)
                    plan_results[task_id] = parsed

                else:
                    try:
                        schemas.TestPlanOutput.model_validate(parsed)
                    except ValidationError as exc:
                        raise ValidationKill(phase_label, [str(exc)])

                    tp_errors = gates.validate_test_plan(parsed)
                    if tp_errors:
                        raise ValidationKill(phase_label, tp_errors)
                    test_plan_results[task_id] = parsed

        prior_phases["plans"] = plan_results
        prior_phases["test_plans"] = test_plan_results
        db_mod.update_spent(conn, run_id, spent_usd)
        _check_budget(spent_usd, budget)

        # --- Phase 3: Wave Planner ---
        wp_result, wp_agent = _run_phase(
            "wave-planner", phase_models.get("wave-planner", model),
            workflow_dir=workflow_dir, worktree_path=worktree_path,
            issue_number=issue_number, issue_body=issue_body,
            prior_phases=prior_phases, run_dir=run_dir_str,
        )
        _log_agent_result(conn, run_id, "wave-planner", wp_agent,
                          phase_models.get("wave-planner", model))
        spent_usd += wp_agent.cost_usd

        try:
            schemas.WavePlannerOutput.model_validate(wp_result)
        except ValidationError as exc:
            raise ValidationKill("wave-planner", [str(exc)])

        wp_errors = gates.validate_wave_planner(wp_result, task_ids, max_parallel)
        if wp_errors:
            raise ValidationKill("wave-planner", wp_errors)

        prior_phases["wave_plan"] = wp_result
        db_mod.update_spent(conn, run_id, spent_usd)
        _check_budget(spent_usd, budget)

        # --- Phase 4: Execute Waves ---
        task_contexts: dict[int, dict] = {}
        for task in tasks:
            tid = task.id
            plan = plan_results.get(tid, {})
            test_plan = test_plan_results.get(tid, {})

            execute_template = _load_prompt(workflow_dir, "execute")
            execute_prior = {
                **prior_phases,
                "_current_task": task.model_dump(),
                "_current_plan": plan,
                "_current_test_plan": test_plan,
            }
            execute_prompt = inject_context(
                execute_template,
                issue_number=issue_number, issue_body=issue_body,
                prior_phases=execute_prior, worktree_path=worktree_path,
                workflow_dir=workflow_dir, phase="execute",
            )
            task_contexts[tid] = {
                "worktree_path": worktree_path,
                "test_command": test_plan.get("test_command", ""),
                "execute_prompt": execute_prompt,
                "model": phase_models.get("execute", model),
                "base_branch": "main",
            }

        wave_results = waves.execute_waves(
            wp_result, task_contexts,
            max_parallel=max_parallel,
            run_dir=run_dir_str,
            db=conn,
            run_id=run_id,
        )

        # Sum up execute costs from phase_logs
        execute_logs = [
            r for r in db_mod.get_phase_logs(conn, run_id)
            if r["phase"].startswith("execute")
        ]
        for log_entry in execute_logs:
            spent_usd += log_entry.get("cost_usd", 0) or 0

        prior_phases["execute"] = wave_results
        db_mod.update_spent(conn, run_id, spent_usd)
        _check_budget(spent_usd, budget)

        # --- Phase 5: Review ---
        diff_proc = subprocess.run(
            ["git", "diff", "main..HEAD"],
            capture_output=True, text=True, cwd=worktree_path, timeout=30,
        )
        combined_diff = diff_proc.stdout[:50000]  # cap diff size

        review_prior = {**prior_phases, "_combined_diff": combined_diff}
        review_result, review_agent = _run_phase(
            "review", phase_models.get("review", "haiku"),
            workflow_dir=workflow_dir, worktree_path=worktree_path,
            issue_number=issue_number, issue_body=issue_body,
            prior_phases=review_prior, run_dir=run_dir_str,
        )
        _log_agent_result(conn, run_id, "review", review_agent,
                          phase_models.get("review", "haiku"))
        spent_usd += review_agent.cost_usd

        review_output = None
        try:
            review_output = schemas.ReviewOutput.model_validate(review_result)
        except ValidationError:
            log.warning("review output failed validation, continuing")

        if review_output:
            db_mod.add_review(
                conn, run_id, "review",
                reviewer_model=phase_models.get("review", "haiku"),
                score=review_output.score,
                verdict=review_output.verdict,
                findings_json=json.dumps(
                    [f.model_dump() for f in review_output.findings], default=str
                ),
            )

        prior_phases["review"] = review_result
        db_mod.update_spent(conn, run_id, spent_usd)

        # --- Phase 6: Push + PR ---
        subprocess.run(
            ["git", "push", "-u", "origin", f"sw/{branch_name}"],
            capture_output=True, text=True, cwd=worktree_path, timeout=60,
            check=True,
        )

        pr_body = f"Closes #{issue_number}\n\nAutomated pipeline run `{run_id[:8]}`."
        if review_output and review_output.findings:
            findings_text = "\n".join(
                f"- **{f.severity}** ({f.category}): {f.description}"
                for f in review_output.findings
            )
            pr_body += f"\n\n### Review Findings\n{findings_text}"

        if platform == PLATFORM_GITLAB:
            pr_proc = subprocess.run(
                ["glab", "mr", "create",
                 "-R", f"{owner}/{repo}",
                 "--source-branch", f"sw/{branch_name}",
                 "--target-branch", "main",
                 "--title", f"fix: resolve #{issue_number}",
                 "--description", pr_body,
                 "--yes"],
                capture_output=True, text=True, cwd=worktree_path, timeout=30,
            )
        else:
            pr_proc = subprocess.run(
                ["gh", "pr", "create",
                 "--repo", f"{owner}/{repo}",
                 "--head", f"sw/{branch_name}",
                 "--title", f"fix: resolve #{issue_number}",
                 "--body", pr_body],
                capture_output=True, text=True, cwd=worktree_path, timeout=30,
            )
        if pr_proc.returncode == 0:
            pr_url = pr_proc.stdout.strip()

        db_mod.finish_run(conn, run_id, status="ok")

        return {
            "run_id": run_id,
            "status": "ok",
            "spent_usd": spent_usd,
            "pr_url": pr_url,
            "wave_results": wave_results,
            "review": review_result,
        }

    except BudgetExceeded as exc:
        db_mod.update_spent(conn, run_id, spent_usd)
        db_mod.finish_run(conn, run_id, status="error", error=str(exc))
        return {"run_id": run_id, "status": "error", "error": str(exc),
                "spent_usd": spent_usd}

    except ValidationKill as exc:
        db_mod.finish_run(conn, run_id, status="error", error=str(exc))
        return {"run_id": run_id, "status": "error", "error": str(exc),
                "spent_usd": spent_usd}

    except Exception as exc:
        log.exception("pipeline failed")
        db_mod.finish_run(conn, run_id, status="error", error=str(exc))
        return {"run_id": run_id, "status": "error", "error": str(exc),
                "spent_usd": spent_usd}

    finally:
        if worktree_path:
            try:
                cleanup_worktree(worktree_path)
            except Exception:
                log.warning("worktree cleanup failed for %s", worktree_path)
        conn.close()


def _run_phase(
    phase: str,
    model: str,
    *,
    workflow_dir: Path,
    worktree_path: str,
    issue_number: int,
    issue_body: str,
    prior_phases: dict[str, Any],
    run_dir: str,
    phase_label: str = "",
) -> tuple[dict, AgentResult]:
    """Load prompt, inject context, call agent, parse JSON. Returns (parsed_dict, agent_result)."""
    label = phase_label or phase
    template = _load_prompt(workflow_dir, phase)
    prompt = inject_context(
        template,
        issue_number=issue_number, issue_body=issue_body,
        prior_phases=prior_phases, worktree_path=worktree_path,
        workflow_dir=workflow_dir, phase=phase,
    )

    print(f"  [{label}] running ({model})...")
    agent_result = run_agent(
        prompt,
        model=model,
        worktree_path=worktree_path,
        run_dir=run_dir,
        phase_label=label,
    )

    parsed = agent_result.parsed_json or {}
    print(f"  [{label}] done in {agent_result.duration_s:.1f}s (${agent_result.cost_usd:.4f})")
    return parsed, agent_result


def _find_target_repo(owner: str, repo: str) -> str:
    """Locate the target repo on disk. Checks common locations."""
    candidates = [
        Path.home() / "services" / repo,
        Path.home() / "repos" / repo,
        Path.cwd() / ".." / repo,
    ]
    # Also check personal-assistant-clones pattern
    pa_root = Path.home() / "Desktop" / "personal-assistant-clones"
    if pa_root.exists():
        for clone_dir in sorted(pa_root.iterdir()):
            candidate = clone_dir / "repos" / repo
            if candidate.exists():
                candidates.insert(0, candidate)
                break

    for path in candidates:
        resolved = path.resolve()
        if resolved.exists() and (resolved / ".git").exists():
            return str(resolved)

    raise FileNotFoundError(
        f"Cannot find local clone of {owner}/{repo}. "
        f"Checked: {[str(c) for c in candidates]}"
    )


def _create_worktree(target_repo: str, branch_name: str) -> str:
    """Create a worktree using the worktree module's create function."""
    from engine.worktree import create_worktree
    return create_worktree(target_repo, branch_name, base_branch="main")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="simple_workflow",
        description="GitHub issue -> tested PR pipeline",
    )
    parser.add_argument("issue", help="Issue ref: owner/repo#NNN")
    parser.add_argument("--budget", type=float, default=1.00,
                        help="Max spend in USD (default: 1.00)")
    parser.add_argument("--model", default="sonnet",
                        help="Default model (default: sonnet)")
    parser.add_argument("--workflow", default="issue-to-pr",
                        help="Workflow name (default: issue-to-pr)")
    parser.add_argument("--workflow-dir", default=None,
                        help="Path to workflow directory (overrides --workflow)")
    parser.add_argument("--gitlab", action="store_true", default=False,
                        help="Force GitLab mode (glab CLI). Auto-detected from remote URL if omitted.")
    args = parser.parse_args()

    owner, repo, issue_number = parse_issue_ref(args.issue)

    workflow_dir = None
    if args.workflow_dir:
        workflow_dir = Path(args.workflow_dir)

    platform = PLATFORM_GITLAB if args.gitlab else None  # None = auto-detect

    print(f"simple_workflow: {owner}/{repo}#{issue_number}")
    print(f"  budget: ${args.budget:.2f}  model: {args.model}  workflow: {args.workflow}")

    result = run_pipeline(
        owner, repo, issue_number,
        budget=args.budget,
        model=args.model,
        workflow_name=args.workflow,
        workflow_dir=workflow_dir,
        platform=platform,
    )

    print(f"\n{'='*60}")
    print(f"  Status:  {result['status']}")
    print(f"  Cost:    ${result.get('spent_usd', 0):.4f}")
    if result.get("pr_url"):
        print(f"  PR:      {result['pr_url']}")
    if result.get("error"):
        print(f"  Error:   {result['error']}")
    print(f"  Run ID:  {result['run_id']}")
    print(f"{'='*60}")

    sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
