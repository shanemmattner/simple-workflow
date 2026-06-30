"""comms_claude engine — comms triage pipeline.

Phases: scan -> prioritize -> draft-replies -> digest
No git worktree. No GitHub source or destination. Output is a markdown digest file.

Usage:
    python -m engines.comms_claude [--hours 24] [--budget 0.50] [--model sonnet]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Reuse the Claude CLI runtime from github_claude — it's a pure subprocess wrapper.
from engines.github_claude import runtime

# Reuse shared storage for SQLite run logging.
from engines.shared import storage

log = logging.getLogger(__name__)

WORKFLOW_DIR = Path(__file__).resolve().parent.parent.parent / "workflows" / "comms-triage"
# cwd for agent calls: PA home so scripts can resolve ~/... paths correctly.
_PA_HOME = Path.home()


# ---------------------------------------------------------------------------
# Workflow loading
# ---------------------------------------------------------------------------

def _load_workflow() -> dict:
    return yaml.safe_load((WORKFLOW_DIR / "workflow.yaml").read_text())


def _load_prompt(phase: str) -> str:
    return (WORKFLOW_DIR / "prompts" / f"{phase}.md").read_text()


def _phase_cfg(workflow: dict) -> dict[str, dict]:
    return {
        p["name"]: {"model": p.get("model", "sonnet"), "max_turns": p.get("max_turns", 5)}
        for p in workflow.get("phases", [])
    }


def _render(template: str, *, run_date: str, hours: int, prior: dict) -> str:
    out = template.replace("{run_date}", run_date)
    out = out.replace("{hours}", str(hours))
    out = out.replace("{prior_phases}", json.dumps(prior, indent=2, default=str) if prior else "")
    # Slug for filenames: YYYY-MM-DD-HH
    run_date_slug = datetime.now().strftime("%Y-%m-%d-%H")
    out = out.replace("{run_date_slug}", run_date_slug)
    return out


# ---------------------------------------------------------------------------
# Agent call helpers
# ---------------------------------------------------------------------------

def _call(phase: str, cfg: dict, *, run_date: str, hours: int, prior: dict,
          model_ov: str | None = None) -> dict:
    prompt = _render(_load_prompt(phase), run_date=run_date, hours=hours, prior=prior)
    model = model_ov or cfg.get("model", "sonnet")
    log.info("[%s] model=%s max_turns=%d prompt_len=%d",
             phase, model, cfg.get("max_turns", 5), len(prompt))
    resp = runtime.call_agent(
        prompt, model=model, cwd=str(_PA_HOME), max_turns=cfg.get("max_turns", 5)
    )
    finish = resp.get("finish_reason", "unknown")
    log.info("[%s] %.1fs $%.4f finish=%s content_len=%d",
             phase, resp["duration_s"], resp["cost"], finish, len(str(resp.get("content", ""))))
    if finish == "error":
        log.error("[%s] phase returned error: %s", phase, str(resp.get("content", ""))[:500])
    elif finish == "timeout":
        log.warning("[%s] phase timed out", phase)
    resp["_prompt"] = prompt
    return resp


def _check_resp(phase: str, resp: dict) -> None:
    finish = resp.get("finish_reason", "unknown")
    if finish == "error":
        raise RuntimeError(f"Phase {phase} failed: {str(resp.get('content', ''))[:300]}")
    if finish == "timeout":
        raise RuntimeError(f"Phase {phase} timed out")


def _content(resp: dict) -> str:
    c = resp["content"]
    return c if isinstance(c, str) else json.dumps(c)


def _extract_json(prose: str, schema_hint: str) -> dict:
    """Pull structured JSON from prose using a haiku extraction call."""
    if not prose or not prose.strip():
        raise ValueError(f"_extract_json: empty prose. schema_hint={schema_hint[:80]}")
    prompt = (
        f"Extract structured data from the following text. "
        f"Return ONLY valid JSON matching this schema (no markdown fences, no explanation):\n"
        f"{schema_hint}\n\nText:\n{prose}"
    )
    resp = runtime.call_agent(prompt, model="haiku", cwd=str(_PA_HOME), max_turns=1)
    raw = _content(resp)
    if raw.strip().startswith("```"):
        raw = raw.strip().split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"_extract_json: JSON parse failed ({exc}). Raw: {raw[:400]}") from exc


def _log_phase(conn, phase_id, resp, failed: bool = False) -> None:
    prompt = resp.get("_prompt", "") if resp else ""
    content = resp.get("content", "") if resp else ""
    if prompt:
        storage.log_message(conn, phase_id, turn_number=1, role="user", content=prompt)
    if content:
        storage.log_message(
            conn, phase_id, turn_number=1, role="assistant",
            content=content if isinstance(content, str) else json.dumps(content),
            tokens_in=resp.get("tokens_in", 0),
            tokens_out=resp.get("tokens_out", 0),
            cost=resp.get("cost", 0),
        )
    storage.finish_phase(
        conn, phase_id,
        status="failed" if failed else "completed",
        cost=resp.get("cost", 0) if resp else 0,
        tokens_in=resp.get("tokens_in", 0) if resp else 0,
        tokens_out=resp.get("tokens_out", 0) if resp else 0,
    )


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def _gate_scan(output: dict) -> None:
    if "messages" not in output:
        raise RuntimeError("scan gate failed: output missing 'messages' array")
    log.info("[gate] scan/messages_array_present: PASS (%d messages)", len(output["messages"]))


def _gate_prioritize(output: dict) -> None:
    if "buckets" not in output:
        raise RuntimeError("prioritize gate failed: output missing 'buckets'")
    log.info("[gate] prioritize/buckets_present: PASS")


def _gate_digest(output: dict) -> None:
    if not output.get("digest_path"):
        raise RuntimeError("digest gate failed: output missing 'digest_path'")
    log.info("[gate] digest/digest_path_present: PASS → %s", output["digest_path"])


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

class BudgetExceeded(RuntimeError):
    pass


def run_pipeline(*, hours: int = 24, budget: float = 0.50,
                 model_override: str | None = None) -> dict:
    wf = _load_workflow()
    budget = budget or wf.get("budget", {}).get("max_per_run_usd", 0.50)
    pcfg = _phase_cfg(wf)

    run_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    run_date_slug = datetime.now().strftime("%Y-%m-%d-%H")

    # Storage: use "comms-triage" as the pseudo-repo, 0 as pseudo-issue-number.
    db_path, conn = storage.create_run_db("comms-triage", 0, model=model_override)
    run_id = db_path.stem if hasattr(db_path, "stem") else str(db_path)
    log.info("comms_claude run_id=%s hours=%d budget=$%.2f", run_id, hours, budget)

    prior: dict = {}
    spent: float = 0.0
    kw = dict(run_date=run_date, hours=hours, model_ov=model_override)

    def _guard():
        if spent > budget:
            raise BudgetExceeded(f"${spent:.2f} > budget ${budget:.2f}")

    try:
        # ── Scan ──────────────────────────────────────────────────────────
        pid = storage.log_phase(conn, "scan", model=model_override or pcfg["scan"]["model"])
        resp = _call("scan", pcfg["scan"], prior=prior, **kw)
        _check_resp("scan", resp)
        _log_phase(conn, pid, resp)
        spent += resp["cost"]
        _guard()
        scan_text = _content(resp)
        prior["scan"] = scan_text

        scan_data = _extract_json(scan_text,
            '{"messages": [{"id": "...", "channel": "...", "from": "...", '
            '"subject": "...", "body": "...", "received_at": "...", "thread_id": "..."}], '
            '"scan_summary": {"gmail_unread": 0, "imessage_scanned": 0, "errors": []}}')
        _gate_scan(scan_data)
        storage.log_event(conn, "scan_summary", scan_data.get("scan_summary", {}))

        # ── Prioritize ────────────────────────────────────────────────────
        pid = storage.log_phase(conn, "prioritize",
                                model=model_override or pcfg["prioritize"]["model"])
        resp = _call("prioritize", pcfg["prioritize"], prior=prior, **kw)
        _check_resp("prioritize", resp)
        _log_phase(conn, pid, resp)
        spent += resp["cost"]
        _guard()
        prioritize_text = _content(resp)
        prior["prioritize"] = prioritize_text

        prio_data = _extract_json(prioritize_text,
            '{"buckets": {"urgent": [], "action_needed": [], "fyi": [], "skip": []}, '
            '"stats": {"urgent_count": 0, "action_count": 0, "fyi_count": 0, "skip_count": 0, "total": 0}}')
        _gate_prioritize(prio_data)
        storage.log_event(conn, "prioritize_stats", prio_data.get("stats", {}))

        urgent_count = prio_data.get("stats", {}).get("urgent_count", 0)
        action_count = prio_data.get("stats", {}).get("action_count", 0)
        log.info("prioritize: %d urgent, %d action_needed", urgent_count, action_count)

        # ── Draft Replies ─────────────────────────────────────────────────
        # Skip draft-replies if nothing needs a reply.
        if urgent_count + action_count == 0:
            log.info("no urgent/action items — skipping draft-replies")
            prior["draft-replies"] = '{"drafts": []}'
        else:
            pid = storage.log_phase(conn, "draft-replies",
                                    model=model_override or pcfg["draft-replies"]["model"])
            resp = _call("draft-replies", pcfg["draft-replies"], prior=prior, **kw)
            _check_resp("draft-replies", resp)
            _log_phase(conn, pid, resp)
            spent += resp["cost"]
            _guard()
            drafts_text = _content(resp)
            prior["draft-replies"] = drafts_text
            storage.log_event(conn, "draft_replies_raw", {"len": len(drafts_text)})

        # ── Digest ────────────────────────────────────────────────────────
        pid = storage.log_phase(conn, "digest",
                                model=model_override or pcfg["digest"]["model"])
        # Include cost info in prior so the digest footer can show it.
        prior["_cost_so_far"] = round(spent, 4)
        resp = _call("digest", pcfg["digest"], prior=prior, **kw)
        _check_resp("digest", resp)
        _log_phase(conn, pid, resp)
        spent += resp["cost"]
        digest_text = _content(resp)
        prior["digest"] = digest_text

        digest_data = _extract_json(digest_text,
            '{"digest_path": "...", "stats": {"urgent": 0, "action_needed": 0, '
            '"fyi": 0, "skip": 0, "drafts_ready": 0}, "top_action": "..."}')
        _gate_digest(digest_data)
        storage.log_event(conn, "digest_result", digest_data)

        digest_path = digest_data.get("digest_path", f"/tmp/comms-digest-{run_date_slug}.md")
        top_action = digest_data.get("top_action")

        storage.finish_run(conn, "ok", total_cost=spent)
        log.info("comms_claude complete — digest at %s  $%.4f total", digest_path, spent)

        return {
            "status": "ok",
            "digest_path": digest_path,
            "top_action": top_action,
            "stats": digest_data.get("stats", {}),
            "spent_usd": spent,
            "run_id": run_id,
        }

    except BudgetExceeded as e:
        storage.finish_run(conn, "budget_exceeded", total_cost=spent)
        log.error("budget exceeded: %s", e)
        return {"status": "error", "error": str(e), "spent_usd": spent, "run_id": run_id}
    except Exception as e:
        log.exception("comms_claude pipeline failed")
        storage.finish_run(conn, "error", total_cost=spent)
        storage.log_event(conn, "pipeline_error", {"error": str(e)})
        return {"status": "error", "error": str(e), "spent_usd": spent, "run_id": run_id}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="comms_claude: scan Gmail + iMessage, draft replies, produce digest")
    ap.add_argument("--hours", type=int, default=24,
                    help="Look-back window in hours (default: 24)")
    ap.add_argument("--budget", type=float, default=0.50,
                    help="Max spend USD (default: 0.50)")
    ap.add_argument("--model", default=None,
                    help="Override model for all phases (e.g. sonnet, haiku)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(f"comms_claude: hours={args.hours}  budget=${args.budget:.2f}")

    result = run_pipeline(hours=args.hours, budget=args.budget, model_override=args.model)

    print(f"\n{'='*50}")
    for k, label in [
        ("status", "Status"),
        ("spent_usd", "Cost"),
        ("digest_path", "Digest"),
        ("top_action", "Top action"),
        ("error", "Error"),
    ]:
        v = result.get(k)
        if v is not None:
            print(f"  {label}: {'${:.4f}'.format(v) if k == 'spent_usd' else v}")
    stats = result.get("stats", {})
    if stats:
        print(f"  Stats: {stats.get('urgent', 0)} urgent · "
              f"{stats.get('action_needed', 0)} action · "
              f"{stats.get('fyi', 0)} fyi · "
              f"{stats.get('drafts_ready', 0)} drafts ready")
    print(f"{'='*50}")
    sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
