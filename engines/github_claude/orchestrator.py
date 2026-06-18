"""Central sequencer for the github_claude engine.

Wires source -> storage -> workspace -> runtime -> destination.
Reads workflow.yaml for phase config (models, max_turns).

Usage:
    python -m engines.github_claude.orchestrator owner/repo 123 [--budget 2.00] [--model opus]
"""
from __future__ import annotations

import argparse, json, logging, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_EXCEPTION
from pathlib import Path
from typing import Any

import yaml
from . import source, runtime, storage, workspace, destination, gates

log = logging.getLogger(__name__)
WORKFLOW_DIR = Path(__file__).resolve().parent.parent.parent / "workflows" / "issue-to-pr"


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


def _load_workflow() -> dict:
    return yaml.safe_load((WORKFLOW_DIR / "workflow.yaml").read_text())

def _load_prompt(phase: str) -> str:
    return (WORKFLOW_DIR / "prompts" / f"{phase}.md").read_text()

def _phase_cfg(workflow: dict) -> dict[str, dict]:
    return {p["name"]: {"model": p.get("model", "sonnet"), "max_turns": p.get("max_turns", 10)}
            for p in workflow.get("phases", [])}

def _render(template: str, issue_number: int, issue_body: str,
            prior: dict, prior_review: str = "", repo_context: str = "") -> str:
    out = template.replace("{issue_number}", str(issue_number))
    out = out.replace("{issue_body}", issue_body)
    out = out.replace("{repo_context}", repo_context)
    out = out.replace("{prior_phases}", json.dumps(prior, indent=2, default=str) if prior else "")
    if prior_review:
        out += f"\n\n## Prior run review\n\n{prior_review}"
    return out

def _call(phase: str, cfg: dict, *, cwd: str, issue_number: int, issue_body: str,
          prior: dict, prior_review: str = "", model_ov: str | None = None,
          repo_context: str = "") -> dict:
    prompt = _render(_load_prompt(phase), issue_number, issue_body, prior, prior_review, repo_context)
    model = model_ov or cfg.get("model", "sonnet")
    log.info("[%s] model=%s max_turns=%d", phase, model, cfg.get("max_turns", 10))
    resp = runtime.call_agent(prompt, model=model, cwd=cwd, max_turns=cfg.get("max_turns", 10))
    log.info("[%s] %.1fs $%.4f", phase, resp["duration_s"], resp["cost"])
    return resp

def _content(resp: dict) -> str:
    """Extract content string from agent response (agents respond in prose)."""
    return resp["content"] if isinstance(resp["content"], str) else json.dumps(resp["content"])

def _extract_json(prose: str, schema_hint: str, cwd: str) -> dict:
    """Make a dedicated extraction call to pull structured data from prose."""
    prompt = f"""Extract structured data from the following text. Return ONLY valid JSON matching this schema (no markdown fences, no explanation):
{schema_hint}

Text to extract from:
{prose}"""
    resp = runtime.call_agent(prompt, model="haiku", cwd=cwd, max_turns=1)
    raw = _content(resp)
    # Strip markdown fences if present
    if raw.strip().startswith("```"):
        raw = raw.strip().split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)

def _start_phase(conn, name):
    """Create a phase record at the START of execution. Returns phase_id."""
    return storage.log_phase(conn, name)

def _finish_phase(conn, phase_id, resp, failed=False):
    """Update the phase record AFTER execution completes."""
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

def _post_failure(repo: str, num: int, err: str):
    try: source.post_comment(repo, num, f"Pipeline failed.\n\n```\n{err[:2000]}\n```")
    except Exception: log.warning("failed to post failure comment on %s#%d", repo, num)


def run_pipeline(repo: str, issue_number: int, *,
                 budget: float = 1.00, model_override: str | None = None,
                 repo_path: str | None = None) -> dict:
    wf = _load_workflow()
    budget = budget or wf.get("budget", {}).get("max_per_run_usd", 1.00)
    max_par = wf.get("max_parallel_workers", 5)
    pcfg = _phase_cfg(wf)

    issue = source.fetch_issue(repo, issue_number)
    issue_body = f"# {issue['title']}\n\n{issue['body']}"

    db_path, conn = storage.create_run_db(repo, issue_number, model=model_override)
    run_id = db_path.stem if hasattr(db_path, 'stem') else str(db_path)
    prior_review = ""
    for r in storage.find_prior_runs(repo, issue_number):
        if r.get("review_summary"):
            prior_review = r["review_summary"]

    branch = f"sw/issue-{issue_number}"
    wt = workspace.create_workspace(repo_path or os.getcwd(), branch)
    repo_context = _load_repo_context(wt)
    if repo_context:
        log.info("loaded .workflows/ context (%d chars)", len(repo_context))
    prior: dict[str, Any] = {}
    spent = 0.0
    kw = dict(cwd=wt, issue_number=issue_number, issue_body=issue_body,
              model_ov=model_override, repo_context=repo_context)

    try:
        # -- Triage (prose -> extract task list) --
        pid = _start_phase(conn, "triage")
        resp = _call("triage", pcfg.get("triage", {}), prior=prior, prior_review=prior_review, **kw)
        _finish_phase(conn, pid, resp); spent += resp["cost"]; _guard(spent, budget)
        triage_text = _content(resp)
        prior["triage"] = triage_text

        triage = _extract_json(triage_text, (
            '{"tasks": [{"id": 1, "title": "...", "target_files": [...], "depends_on": []}], '
            '"proof_type": "test_passes", "escalate": false}'
        ), cwd=wt)
        _run_gates(conn, "triage", triage, worktree_path=wt)
        tasks = triage.get("tasks", [])

        # -- Plan + Test-Plan (parallel per task, prose pass-through) --
        plan_r: dict[int, str] = {}; tp_r: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=min(len(tasks) * 2, max_par)) as pool:
            futs: dict[Any, tuple[str, int, int]] = {}
            for t in tasks:
                tp = {**prior, "_current_task": t}
                for ph in ("plan", "test-plan"):
                    pid = _start_phase(conn, f"{ph}-task-{t['id']}")
                    futs[pool.submit(_call, ph, pcfg.get(ph, {}), prior=tp, **kw)] = (ph, t["id"], pid)
            done, _ = wait(futs, return_when=FIRST_EXCEPTION)
            for f in done:
                ph, tid, pid = futs[f]
                try:
                    resp = f.result()
                    _finish_phase(conn, pid, resp)
                except Exception:
                    _finish_phase(conn, pid, None, failed=True)
                    raise
                spent += resp["cost"]
                text = _content(resp)
                (plan_r if ph == "plan" else tp_r)[tid] = text
        _guard(spent, budget)
        prior["plans"] = plan_r; prior["test_plans"] = tp_r

        # -- Wave Planner (prose -> extract wave assignments) --
        pid = _start_phase(conn, "wave-planner")
        resp = _call("wave-planner", pcfg.get("wave-planner", {}), prior=prior, **kw)
        _finish_phase(conn, pid, resp); spent += resp["cost"]; _guard(spent, budget)
        wave_text = _content(resp)
        prior["wave_plan"] = wave_text

        wave_plan = _extract_json(wave_text, (
            '{"waves": [[1, 2], [3, 4]]}  -- list of lists, each inner list is task IDs in that wave'
        ), cwd=wt)
        task_ids = [t["id"] for t in tasks]
        _run_gates(conn, "wave-planner", wave_plan,
                   task_ids=task_ids, max_parallel=max_par)

        # -- Execute (parallel within wave, serial across waves) --
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
                    pid = _start_phase(conn, f"execute-task-{tid}")
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
            _guard(spent, budget)
        prior["execute"] = exec_results

        # -- Review (prose pass-through) --
        diff = workspace.get_diff(wt)[:50_000]
        pid = _start_phase(conn, "review")
        resp = _call("review", pcfg.get("review", {}),
                      prior={**prior, "_combined_diff": diff}, **kw)
        _finish_phase(conn, pid, resp); spent += resp["cost"]
        prior["review"] = _content(resp)

        # -- Push + PR --
        destination.push_branch(wt, branch)
        body = destination.format_pr_body(issue_number, prior["review"], db_path, [])
        pr = destination.create_pr(repo, branch, f"fix: resolve #{issue_number}", body)
        storage.finish_run(conn, "ok", total_cost=spent, branch=branch)
        return {"status": "ok", "pr_url": pr["url"], "spent_usd": spent, "run_id": run_id}

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
        workspace.cleanup_workspace(wt)
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="github_claude: issue -> PR pipeline")
    ap.add_argument("repo", help="owner/repo")
    ap.add_argument("issue", type=int, help="Issue number")
    ap.add_argument("--budget", type=float, default=1.00, help="Max spend USD")
    ap.add_argument("--model", default=None, help="Override model for all phases")
    ap.add_argument("--repo-path", default=None, help="Local filesystem path to the repo (default: cwd)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(f"github_claude: {args.repo}#{args.issue}  budget=${args.budget:.2f}")
    result = run_pipeline(args.repo, args.issue, budget=args.budget,
                          model_override=args.model, repo_path=args.repo_path)

    print(f"\n{'='*50}")
    for k, label in [("status","Status"), ("spent_usd","Cost"), ("pr_url","PR"), ("error","Error")]:
        v = result.get(k)
        if v is not None:
            print(f"  {label}: {'${:.4f}'.format(v) if k == 'spent_usd' else v}")
    print(f"{'='*50}")
    sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
