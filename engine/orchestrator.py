"""Central sequencer for the github_claude engine.

Wires source -> storage -> workspace -> runtime -> destination.
Reads workflow.yaml for phase config (models, max_turns).

Usage:
    python -m engine owner/repo 123 [--budget 2.00] [--model opus]
"""
from __future__ import annotations

import argparse, glob, json, logging, os, re, sqlite3, subprocess, sys
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
    # Strategy 1: structured header match — "## Decision\nPROCEED"
    header_match = re.search(
        r"^##\s*decision\s*\n+\s*(PROCEED|SKIP|ESCALATE)\s*$",
        text, re.IGNORECASE | re.MULTILINE,
    )
    if header_match:
        signal = header_match.group(1).upper()
        log.info("_parse_triage_signal: header match signal=%s", signal)
        return signal

    # Strategy 2: signal as sole word on its own line (priority: ESCALATE > SKIP > PROCEED)
    for signal in ("ESCALATE", "SKIP", "PROCEED"):
        if re.search(rf"^\s*{signal}\s*$", text, re.IGNORECASE | re.MULTILINE):
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
    Defaults to PASS if none found.
    """
    # Strategy 1: structured header match — "## Verdict\nPASS"
    header_match = re.search(
        r"^##\s*verdict\s*\n+\s*(PASS|WARN|FAIL)\s*$",
        text, re.IGNORECASE | re.MULTILINE,
    )
    if header_match:
        signal = header_match.group(1).upper()
        log.info("_parse_review_signal: header match signal=%s", signal)
        return signal

    # Strategy 2: signal as sole word on its own line (priority: FAIL > WARN > PASS)
    for signal in ("FAIL", "WARN", "PASS"):
        if re.search(rf"^\s*{signal}\s*$", text, re.IGNORECASE | re.MULTILINE):
            log.info("_parse_review_signal: standalone-line match signal=%s", signal)
            return signal

    log.info("_parse_review_signal: no explicit signal found — defaulting to PASS")
    return "PASS"


def _load_workflow() -> dict:
    return yaml.safe_load((WORKFLOW_DIR / "workflow.yaml").read_text())

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


def _post_failure(repo: str, num: int, err: str):
    try: source.post_comment(repo, num, f"Pipeline failed.\n\n```\n{err[:2000]}\n```")
    except Exception: log.warning("failed to post failure comment on %s#%d", repo, num)


def run_pipeline(repo: str, issue_number: int, *,
                 budget: float = 1.00, model_override: str | None = None,
                 repo_path: str | None = None,
                 resume_from: str | None = None) -> dict:
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
    if resume_state:
        wt = workspace.reuse_or_create_workspace(repo_path or os.getcwd(), branch)
    else:
        wt = workspace.create_workspace(repo_path or os.getcwd(), branch)
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


def run_domain_pipeline(repo: str, issue_number: int, *,
                        workflow: str,
                        budget: float = 10.00,
                        model_override: str | None = None,
                        repo_path: str | None = None) -> dict:
    """Simplified 3-phase pipeline for domain-specific workflows (shftty-web, shftty-ios, etc).

    Unlike run_pipeline(), this function:
    - Loads prompts from a domain workflow dir (e.g. workflows/shftty-web/prompts/)
    - Runs only 3 core phases: triage → execute → review (plus optional validate + improve)
    - Passes phase outputs as formatted markdown text via {prior_phases} (no JSON extraction)
    - Exits early on SKIP/ESCALATE triage signal
    - Creates PR even on review FAIL, but marks it as needing fixes
    - No wave planning, no parallel task execution, no JSON gates
    """
    wf_dir = _resolve_workflow_dir(workflow)
    log.info("run_domain_pipeline start repo=%s issue=%d workflow=%s wf_dir=%s budget=%.2f",
             repo, issue_number, workflow, wf_dir, budget)

    # Load workflow config from domain dir (falls back gracefully if missing)
    wf_yaml = wf_dir / "workflow.yaml"
    if wf_yaml.is_file():
        wf = yaml.safe_load(wf_yaml.read_text())
        log.info("loaded workflow.yaml from %s", wf_yaml)
    else:
        log.warning("no workflow.yaml in %s — using empty config", wf_dir)
        wf = {}
    budget = budget or wf.get("budget", {}).get("max_per_run_usd", 10.00)
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
        out = template.replace("{issue_number}", str(issue_number))
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

    issue = source.fetch_issue(repo, issue_number)
    issue_body = f"# {issue['title']}\n\n{issue['body']}"

    db_path, conn = storage.create_run_db(repo, issue_number, model=model_override)
    run_id = db_path.stem if hasattr(db_path, "stem") else str(db_path)
    log.info("run_domain_pipeline run_id=%s", run_id)

    branch = f"sw/{workflow}-{issue_number}"
    wt = workspace.create_workspace(repo_path or os.getcwd(), branch)
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
            source.post_comment(repo, issue_number, msg)
            storage.finish_run(conn, f"triage_{triage_signal.lower()}", total_cost=spent)
            log.info("[domain/triage] early exit signal=%s", triage_signal)
            return {"status": f"triage_{triage_signal.lower()}", "spent_usd": spent, "run_id": run_id}

        _guard(spent, budget)

        # ---- Execute phase -------------------------------------------------------
        execute_cfg = pcfg.get("execute", {"model": "sonnet", "max_turns": 30})
        execute_model = _resolve_model(execute_cfg, model_override)
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
        execute_text = _content(execute_resp)
        prior["execute"] = execute_text
        storage.log_event(conn, "execute_complete", {
            "cost": execute_resp["cost"], "content_len": len(execute_text),
        })
        log.info("[domain/execute] complete spent=%.4f content_len=%d", spent, len(execute_text))
        _guard(spent, budget)

        # ---- Safety-net commit ---------------------------------------------------
        _porcelain = subprocess.run(
            ["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True,
        ).stdout.strip()
        _commits = subprocess.run(
            ["git", "log", "origin/main..HEAD", "--oneline"], cwd=wt, capture_output=True, text=True,
        ).stdout.strip()
        if _porcelain:
            log.warning("[domain] execute phase left uncommitted changes — safety-net commit")
            subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"feat: resolve #{issue_number} ({workflow})"],
                cwd=wt, check=True,
            )
            storage.log_event(conn, "safety_net_commit", {"files": _porcelain})
        elif not _commits:
            raise RuntimeError(
                "Execute phase produced no changes — no commits on branch and no uncommitted files"
            )

        # ---- Review phase -------------------------------------------------------
        diff = workspace.get_diff(wt)[:50_000]
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

        # ---- Push + PR ----------------------------------------------------------
        destination.push_branch(wt, branch)
        pr_title = f"fix: resolve #{issue_number} ({workflow})"
        if review_signal == "FAIL":
            pr_title = f"[NEEDS FIXES] fix: resolve #{issue_number} ({workflow})"
            log.warning("[domain/review] FAIL signal — PR will be marked as needing fixes")
        pr_body = destination.format_pr_body(issue_number, review_text, db_path, [])
        if review_signal == "FAIL":
            pr_body = "**Review flagged issues that need addressing before merge.**\n\n" + pr_body
        pr = destination.create_pr(repo, branch, pr_title, pr_body)
        log.info("[domain] PR created url=%s review_signal=%s", pr.get("url"), review_signal)
        storage.log_event(conn, "pr_created", {"url": pr.get("url"), "review_signal": review_signal})

        # ---- Validate phase (optional) ------------------------------------------
        validate_result = None
        validate_prompt_path = wf_dir / "prompts" / "validate.md"
        if validate_prompt_path.is_file() and validate.check_has_ui_changes(prior.get("triage", ""), issue_body):
            try:
                pr_number = pr.get("number", issue_number)
                log.info("[domain/validate] UI changes detected — polling for preview URL")
                preview_url = validate.get_preview_url(repo, pr_number)
                if preview_url:
                    log.info("[domain/validate] preview ready: %s", preview_url)
                    validate_cfg = pcfg.get("validate", {"model": "sonnet", "max_turns": 10})
                    validate_model = _resolve_model(validate_cfg, model_override)
                    pid = _start_phase(conn, "validate", model=validate_model)
                    validate_prompt = render_domain(
                        validate_prompt_path.read_text(), prior,
                        repo_context=repo_context, recent_learnings=recent_learnings_text,
                        extra={"pr_number": str(pr_number), "repo": repo, "preview_url": preview_url},
                    )
                    validate_resp = runtime.call_agent(
                        validate_prompt, model=validate_model, cwd=wt,
                        max_turns=validate_cfg.get("max_turns", 10),
                    )
                    validate_resp["_prompt"] = validate_prompt
                    _finish_phase(conn, pid, validate_resp)
                    spent += validate_resp["cost"]
                    validate_text = _content(validate_resp)
                    prior["validate"] = validate_text
                    validate_result = validate_text
                    storage.log_event(conn, "validate_result", {
                        "preview_url": preview_url, "raw_len": len(validate_text),
                    })
                    log.info("[domain/validate] complete chars=%d", len(validate_text))
                else:
                    log.warning("[domain/validate] preview URL not available — skipping")
                    storage.log_event(conn, "validate_skipped", {"reason": "preview_url_timeout"})
            except Exception as ve:
                log.warning("[domain/validate] phase failed (non-blocking): %s", ve)
                storage.log_event(conn, "validate_error", {"error": str(ve)})
        else:
            log.info("[domain/validate] skipped — no validate.md or no UI changes")
            storage.log_event(conn, "validate_skipped", {"reason": "no_ui_changes_or_no_prompt"})

        # ---- Improve phase (optional) -------------------------------------------
        improve_prompt_path = wf_dir / "prompts" / "improve.md"
        if improve_prompt_path.is_file():
            try:
                cost_summary = json.dumps({
                    "total_spent_usd": round(spent, 4),
                    "budget_usd": budget,
                    "utilization_pct": round(spent / budget * 100, 1) if budget else 0,
                })
                improve_cfg = pcfg.get("improve", {"model": "opus", "max_turns": 10})
                improve_model = model_override or improve_cfg.get("model", "opus")
                pid = _start_phase(conn, "improve", model=_resolve_model(improve_cfg, model_override))
                log.info("[domain/improve] start model=%s", improve_model)
                improve_prompt = render_domain(
                    improve_prompt_path.read_text(), prior,
                    repo_context=repo_context, recent_learnings=recent_learnings_text,
                    extra={"cost_summary": cost_summary, "combined_diff": diff[:30_000]},
                )
                improve_resp = runtime.call_agent(
                    improve_prompt, model=improve_model, cwd=wt,
                    max_turns=improve_cfg.get("max_turns", 10),
                )
                improve_resp["_prompt"] = improve_prompt
                _finish_phase(conn, pid, improve_resp)
                spent += improve_resp["cost"]
                improve_text = _content(improve_resp)
                prior["improve"] = improve_text
                storage.log_event(conn, "improve_complete", {"content_len": len(improve_text)})
                log.info("[domain/improve] complete chars=%d", len(improve_text))
                try:
                    improve_data = (
                        json.loads(improve_text)
                        if improve_text.strip().startswith("{")
                        else {"raw": improve_text}
                    )
                    storage.log_event(conn, "improvement_suggestions", improve_data)
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
                except Exception as je:
                    log.warning("[domain/improve] could not parse improve output: %s", je)
            except Exception as ie:
                log.warning("[domain/improve] phase failed (non-blocking): %s", ie)
        else:
            log.info("[domain/improve] skipped — no improve.md in workflow dir")

        storage.finish_run(conn, "ok", total_cost=spent, branch=branch)
        result: dict = {
            "status": "ok",
            "pr_url": pr["url"],
            "spent_usd": spent,
            "run_id": run_id,
            "review_signal": review_signal,
        }
        if validate_result:
            result["validate"] = validate_result
        log.info("run_domain_pipeline complete status=ok pr=%s spent=%.4f review_signal=%s",
                 pr.get("url"), spent, review_signal)
        return result

    except BudgetExceeded as e:
        log.error("[domain] budget exceeded: %s", e)
        storage.finish_run(conn, "budget_exceeded", total_cost=spent)
        _post_failure(repo, issue_number, str(e))
        return {"status": "error", "error": str(e), "spent_usd": spent, "run_id": run_id}
    except Exception as e:
        log.exception("[domain] pipeline failed")
        storage.finish_run(conn, "error", total_cost=spent)
        storage.log_event(conn, "pipeline_error", {"error": str(e)})
        _post_failure(repo, issue_number, str(e))
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
    wf_dir = _resolve_workflow_dir(workflow)
    pa_root = Path(__file__).resolve().parents[2]
    log.info("run_ops_pipeline start workflow=%s wf_dir=%s task=%r pa_root=%s",
             workflow, wf_dir, task_description[:80], pa_root)

    wf_yaml = wf_dir / "workflow.yaml"
    if wf_yaml.is_file():
        wf = yaml.safe_load(wf_yaml.read_text())
        log.info("loaded workflow.yaml from %s", wf_yaml)
    else:
        log.warning("no workflow.yaml in %s — using empty config", wf_dir)
        wf = {}
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
        storage.log_event(conn, "execute_complete", {
            "cost": execute_resp["cost"], "content_len": len(execute_text),
        })
        log.info("[ops/execute] complete spent=%.4f content_len=%d", spent, len(execute_text))

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

        # ---- Write output to file -----------------------------------------------
        out_dir = pa_root / "work" / "outputs" / workflow
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{today}-{task_slug}.md"
        out_path.write_text(execute_text)
        log.info("[ops] output written to %s", out_path)
        storage.log_event(conn, "output_written", {"path": str(out_path), "bytes": len(execute_text)})

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
        wf_yaml = _resolve_workflow_dir(args.workflow) / "workflow.yaml"
        wf_type = None
        if wf_yaml.is_file():
            wf_cfg = yaml.safe_load(wf_yaml.read_text()) or {}
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
