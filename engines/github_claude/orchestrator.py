"""Central sequencer for the github_claude engine.

Wires source -> storage -> workspace -> runtime -> destination.
Reads workflow.yaml for phase config (models, max_turns).

Usage:
    python -m engines.github_claude.orchestrator owner/repo 123 [--budget 2.00] [--model opus]
"""
from __future__ import annotations

import argparse, json, logging, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml
from . import source, runtime, storage, workspace, destination, gates

log = logging.getLogger(__name__)
WORKFLOW_DIR = Path(__file__).resolve().parent.parent.parent / "workflows" / "issue-to-pr"


def _load_workflow() -> dict:
    return yaml.safe_load((WORKFLOW_DIR / "workflow.yaml").read_text())

def _load_prompt(phase: str) -> str:
    return (WORKFLOW_DIR / "prompts" / f"{phase}.md").read_text()

def _phase_cfg(workflow: dict) -> dict[str, dict]:
    return {p["name"]: {"model": p.get("model", "sonnet"), "max_turns": p.get("max_turns", 10)}
            for p in workflow.get("phases", [])}

def _render(template: str, issue_number: int, issue_body: str,
            prior: dict, prior_review: str = "") -> str:
    out = template.replace("{issue_number}", str(issue_number))
    out = out.replace("{issue_body}", issue_body)
    out = out.replace("{prior_phases}", json.dumps(prior, indent=2, default=str) if prior else "")
    if prior_review:
        out += f"\n\n## Prior run review\n\n{prior_review}"
    return out

def _call(phase: str, cfg: dict, *, cwd: str, issue_number: int, issue_body: str,
          prior: dict, prior_review: str = "", model_ov: str | None = None) -> dict:
    prompt = _render(_load_prompt(phase), issue_number, issue_body, prior, prior_review)
    model = model_ov or cfg.get("model", "sonnet")
    log.info("[%s] model=%s max_turns=%d", phase, model, cfg.get("max_turns", 10))
    resp = runtime.call_agent(prompt, model=model, cwd=cwd, max_turns=cfg.get("max_turns", 10))
    log.info("[%s] %.1fs $%.4f", phase, resp["duration_s"], resp["cost"])
    return resp

def _parse(resp: dict) -> Any:
    c = resp["content"]
    return json.loads(c) if isinstance(c, str) else c

def _log(conn, name, resp):
    storage.log_phase(conn, name, cost=resp["cost"],
                      tokens_in=resp["tokens_in"], tokens_out=resp["tokens_out"])


class BudgetExceeded(RuntimeError): pass
class GateFailure(RuntimeError): pass

def _guard(spent: float, budget: float):
    if spent > budget:
        raise BudgetExceeded(f"${spent:.2f} > budget ${budget:.2f}")

def _run_gates(conn, phase_name: str, output: dict, **gate_kw) -> None:
    """Run phase gates, log each result, raise GateFailure on first failure."""
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
                 budget: float = 1.00, model_override: str | None = None) -> dict:
    wf = _load_workflow()
    budget = budget or wf.get("budget", {}).get("max_per_run_usd", 1.00)
    max_par = wf.get("max_parallel_workers", 5)
    pcfg = _phase_cfg(wf)

    issue = source.fetch_issue(repo, issue_number)
    issue_body = f"# {issue['title']}\n\n{issue['body']}"

    db_path, conn = storage.create_run_db(repo, issue_number, model=model_override)
    prior_review = ""
    for r in storage.find_prior_runs(repo, issue_number):
        if r.get("review_summary"):
            prior_review = r["review_summary"]

    branch = f"sw/issue-{issue_number}"
    wt = workspace.create_workspace(repo, branch)
    prior: dict[str, Any] = {}
    spent = 0.0
    kw = dict(cwd=wt, issue_number=issue_number, issue_body=issue_body, model_ov=model_override)

    try:
        # -- Triage --
        resp = _call("triage", pcfg.get("triage", {}), prior=prior, prior_review=prior_review, **kw)
        _log(conn, "triage", resp); spent += resp["cost"]; _guard(spent, budget)
        triage = _parse(resp); prior["triage"] = triage
        _run_gates(conn, "triage", triage, worktree_path=wt)
        tasks = triage.get("tasks", [])

        # -- Plan + Test-Plan (parallel per task) --
        plan_r: dict[int, dict] = {}; tp_r: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=min(len(tasks) * 2, max_par)) as pool:
            futs: dict[Any, tuple[str, int]] = {}
            for t in tasks:
                tp = {**prior, "_current_task": t}
                for ph in ("plan", "test-plan"):
                    futs[pool.submit(_call, ph, pcfg.get(ph, {}), prior=tp, **kw)] = (ph, t["id"])
            for f in as_completed(futs):
                ph, tid = futs[f]; resp = f.result()
                _log(conn, f"{ph}-task-{tid}", resp); spent += resp["cost"]
                parsed = _parse(resp)
                (plan_r if ph == "plan" else tp_r)[tid] = parsed
        _guard(spent, budget)
        for tid, plan_out in plan_r.items():
            _run_gates(conn, "plan", plan_out, worktree_path=wt)
        for tid, tp_out in tp_r.items():
            _run_gates(conn, "test-plan", tp_out)
        prior["plans"] = plan_r; prior["test_plans"] = tp_r

        # -- Wave Planner --
        resp = _call("wave-planner", pcfg.get("wave-planner", {}), prior=prior, **kw)
        _log(conn, "wave-planner", resp); spent += resp["cost"]; _guard(spent, budget)
        wave_plan = _parse(resp); prior["wave_plan"] = wave_plan
        task_ids = [t["id"] for t in tasks]
        _run_gates(conn, "wave-planner", wave_plan,
                   task_ids=task_ids, max_parallel=max_par)

        # -- Execute (parallel within wave, serial across waves) --
        exec_results: list[dict] = []
        for wi, wave in enumerate(wave_plan.get("waves", [])):
            log.info("wave %d: %s", wi, wave.get("tasks", []))
            with ThreadPoolExecutor(max_workers=max_par) as pool:
                efuts = {}
                for tid in wave.get("tasks", []):
                    t = next((t for t in tasks if t["id"] == tid), None)
                    if not t: continue
                    ep = {**prior, "_current_task": t,
                          "_current_plan": plan_r.get(tid, {}),
                          "_current_test_plan": tp_r.get(tid, {})}
                    efuts[pool.submit(_call, "execute", pcfg.get("execute", {}), prior=ep, **kw)] = tid
                for f in as_completed(efuts):
                    tid = efuts[f]; resp = f.result()
                    _log(conn, f"execute-task-{tid}", resp); spent += resp["cost"]
                    exec_out = _parse(resp)
                    test_cmd = tp_r.get(tid, {}).get("test_command", "")
                    _run_gates(conn, "execute", exec_out,
                               worktree_path=wt, test_command=test_cmd,
                               base_branch=branch)
                    exec_results.append({"task_id": tid, "response": resp})
            _guard(spent, budget)
        prior["execute"] = exec_results

        # -- Review --
        diff = workspace.get_diff(wt)[:50_000]
        resp = _call("review", pcfg.get("review", {}),
                      prior={**prior, "_combined_diff": diff}, **kw)
        _log(conn, "review", resp); spent += resp["cost"]
        prior["review"] = resp["content"]

        # -- Push + PR --
        destination.push_branch(wt, branch)
        body = destination.format_pr_body(issue_number, resp["content"], db_path, [])
        pr = destination.create_pr(repo, branch, f"fix: resolve #{issue_number}", body)
        storage.finish_run(conn, "ok", total_cost=spent, branch=branch)
        return {"status": "ok", "pr_url": pr["url"], "spent_usd": spent}

    except GateFailure as e:
        log.error("gate failure: %s", e)
        storage.finish_run(conn, "gate_failed", total_cost=spent)
        _post_failure(repo, issue_number, str(e))
        return {"status": "error", "error": str(e), "spent_usd": spent}
    except BudgetExceeded as e:
        storage.finish_run(conn, "budget_exceeded", total_cost=spent)
        _post_failure(repo, issue_number, str(e))
        return {"status": "error", "error": str(e), "spent_usd": spent}
    except Exception as e:
        log.exception("pipeline failed")
        storage.finish_run(conn, "error", total_cost=spent)
        storage.log_event(conn, "pipeline_error", {"error": str(e)})
        _post_failure(repo, issue_number, str(e))
        return {"status": "error", "error": str(e), "spent_usd": spent}
    finally:
        workspace.cleanup_workspace(wt)
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="github_claude: issue -> PR pipeline")
    ap.add_argument("repo", help="owner/repo")
    ap.add_argument("issue", type=int, help="Issue number")
    ap.add_argument("--budget", type=float, default=1.00, help="Max spend USD")
    ap.add_argument("--model", default=None, help="Override model for all phases")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(f"github_claude: {args.repo}#{args.issue}  budget=${args.budget:.2f}")
    result = run_pipeline(args.repo, args.issue, budget=args.budget, model_override=args.model)

    print(f"\n{'='*50}")
    for k, label in [("status","Status"), ("spent_usd","Cost"), ("pr_url","PR"), ("error","Error")]:
        v = result.get(k)
        if v is not None:
            print(f"  {label}: {'${:.4f}'.format(v) if k == 'spent_usd' else v}")
    print(f"{'='*50}")
    sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
