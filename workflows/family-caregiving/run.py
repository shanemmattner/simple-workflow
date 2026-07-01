#!/usr/bin/env python3
"""Simple workflow: triage -> plan -> execute -> review -> improve.

Self-contained. Copy this folder to make a new workflow; edit the prompts;
run it. Workflow name and branch prefix are auto-derived from the directory
name — no edits needed after copying.

Plan and improve phases are optional — if the prompt file doesn't exist,
that phase is skipped.
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Template

DIR = Path(__file__).parent


class ClaudeError(Exception):
    pass


def init_db(path: Path) -> sqlite3.Connection:
    """Create the SQLite database and tables if they don't exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS run (
            id TEXT PRIMARY KEY,
            workflow TEXT,
            repo TEXT,
            git_ref TEXT,
            branch TEXT,
            issue INTEGER,
            budget REAL,
            model TEXT,
            started_at TEXT,
            finished_at TEXT,
            total_cost REAL,
            verdict TEXT
        );
        CREATE TABLE IF NOT EXISTS phase (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT REFERENCES run(id),
            name TEXT,
            prompt_name TEXT,
            model TEXT,
            max_turns INTEGER,
            started_at TEXT,
            finished_at TEXT,
            cost REAL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            prompt_text TEXT,
            result_text TEXT
        );
        CREATE TABLE IF NOT EXISTS event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phase_id INTEGER REFERENCES phase(id),
            event_json TEXT
        );
    """)
    conn.commit()
    return conn


def call_claude(prompt: str, cwd: str, model: str, max_turns: int) -> tuple[dict, list]:
    proc = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "json",
         "--max-turns", str(max_turns), "--dangerously-skip-permissions"],
        input=prompt, capture_output=True, text=True, cwd=cwd,
    )
    if proc.returncode != 0:
        raise ClaudeError(f"claude exit {proc.returncode}: {proc.stderr[:500]}")
    try:
        events = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ClaudeError(f"could not parse claude output: {proc.stdout[:500]}")
    for event in events:
        if event.get("type") == "result":
            return event, events
    raise ClaudeError("no result event in claude output")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse simple key: value frontmatter between --- delimiters."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].strip().splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            meta[k.strip()] = v.strip()
    return meta, parts[2]


def load_prompt(name: str, **kwargs) -> tuple[dict, str]:
    raw = (DIR / "prompts" / f"{name}.md").read_text()
    meta, text = parse_frontmatter(raw)
    text = Template(text).render(**kwargs)
    return meta, text


def fetch_issue_body(repo_path: str, issue: int) -> str:
    proc = subprocess.run(
        ["gh", "issue", "view", str(issue), "--json", "title,body"],
        capture_output=True, text=True, cwd=repo_path,
    )
    if proc.returncode != 0:
        return ""
    data = json.loads(proc.stdout)
    return f"{data.get('title', '')}\n\n{data.get('body', '')}"


def parse_tasks(plan_text: str) -> list[str]:
    """Extract task blocks from plan output. Splits on ### Step N or ### Task N headers."""
    blocks = re.split(r'(?=^### (?:Step|Task) \d+)', plan_text, flags=re.MULTILINE)
    return [b.strip() for b in blocks if b.strip() and re.match(r'^### (?:Step|Task) \d+', b.strip())]


def _log_phase(db: sqlite3.Connection, run_id: str, name: str,
               prompt_name: str, model: str, max_turns: int,
               started_at: str, finished_at: str, cost: float,
               input_tokens: int, output_tokens: int,
               prompt_text: str, result_text: str,
               events: list) -> None:
    """Insert phase and event rows. Silently ignores DB errors."""
    try:
        cur = db.execute(
            "INSERT INTO phase (run_id, name, prompt_name, model, max_turns, "
            "started_at, finished_at, cost, input_tokens, output_tokens, "
            "prompt_text, result_text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, name, prompt_name, model, max_turns,
             started_at, finished_at, cost, input_tokens, output_tokens,
             prompt_text, result_text),
        )
        phase_id = cur.lastrowid
        for ev in events:
            db.execute("INSERT INTO event (phase_id, event_json) VALUES (?,?)",
                       (phase_id, json.dumps(ev, default=str)))
        db.commit()
    except Exception:
        pass


def _extract_tokens(resp: dict) -> tuple[int, int]:
    """Extract input/output tokens from the result event."""
    usage = resp.get("usage", {})
    inp = usage.get("input_tokens", resp.get("input_tokens", 0)) or 0
    out = usage.get("output_tokens", resp.get("output_tokens", 0)) or 0
    return int(inp), int(out)


def main():
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser()
    parser.add_argument("repo_path", help="Path to the git repo")
    parser.add_argument("git_ref", help="Git ref to start from")
    parser.add_argument("--issue", type=int, default=None)
    parser.add_argument("--budget", type=float, default=10.0)
    parser.add_argument("--model", default="sonnet")
    args = parser.parse_args()

    repo = str(Path(args.repo_path).resolve())
    wf_name = DIR.name
    run_id = uuid.uuid4().hex[:8]
    branch = f"sw/{wf_name}-{args.issue or 'run'}-{int(time.time())}"
    wt = f"/tmp/sw-{branch.replace('/', '-')}"
    out_dir = Path(wt) / ".workflow-outputs"

    # --- SQLite logging ---
    db_path = DIR.parent.parent / "runs" / f"{wf_name}-{run_id}.db"
    try:
        db = init_db(db_path)
    except Exception as exc:
        print(f"warning: could not init run DB: {exc}", file=sys.stderr)
        db = None
    run_started = datetime.now(timezone.utc).isoformat()
    if db:
        try:
            db.execute(
                "INSERT INTO run (id, workflow, repo, git_ref, branch, issue, "
                "budget, model, started_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, wf_name, repo, args.git_ref, branch, args.issue,
                 args.budget, args.model, run_started),
            )
            db.commit()
        except Exception:
            pass

    print(f"worktree: {wt}  branch: {branch}  ref: {args.git_ref}")
    subprocess.run(["git", "worktree", "add", "-b", branch, wt, args.git_ref],
                   cwd=repo, check=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    issue_body = fetch_issue_body(repo, args.issue) if args.issue else "(no issue)"
    costs = {}
    spent = 0.0

    def run_phase(name: str, prompt_name: str, **extra) -> str:
        nonlocal spent
        prior = ""
        for f in sorted(out_dir.glob("*.md")):
            prior += f"\n\n---\n## {f.stem}\n\n{f.read_text()}"

        meta, prompt = load_prompt(prompt_name,
            repo_context=f"Repo: {repo}\nRef: {args.git_ref}\nBranch: {branch}",
            issue_body=issue_body,
            issue_number=str(args.issue or "N/A"),
            recent_learnings="(none)",
            prior_phases=prior,
            **extra,
        )
        model = meta.get("model", args.model)
        max_turns = int(meta.get("max_turns", 30))
        out_file = out_dir / f"{name}.md"

        print(f"--- {name} ---")
        phase_started = datetime.now(timezone.utc).isoformat()
        try:
            resp, events = call_claude(prompt, wt, model, max_turns)
        except ClaudeError as e:
            phase_finished = datetime.now(timezone.utc).isoformat()
            if db:
                _log_phase(db, run_id, name, prompt_name, model, max_turns,
                           phase_started, phase_finished, 0.0, 0, 0,
                           prompt, str(e), [])
            print(f"  FAILED: {e}")
            return None
        phase_finished = datetime.now(timezone.utc).isoformat()
        result = resp.get("result", "")
        cost = resp.get("total_cost_usd", resp.get("cost_usd", 0.0))
        input_tokens, output_tokens = _extract_tokens(resp)
        costs[name] = cost
        spent += cost

        if db:
            _log_phase(db, run_id, name, prompt_name, model, max_turns,
                       phase_started, phase_finished, cost,
                       input_tokens, output_tokens, prompt, result, events)

        if not out_file.exists():
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file.write_text(result)

        print(f"  done: ${cost:.4f}  (total: ${spent:.4f})")
        if spent > args.budget:
            print(f"budget exceeded (${spent:.2f} > ${args.budget})", file=sys.stderr)
            sys.exit(1)
        return result

    def print_summary():
        review_file = out_dir / "4-review.md"
        review_tail = review_file.read_text()[-300:] if review_file.exists() else ""
        verdict = next((v for v in ("FAIL", "WARN", "PASS") if v in review_tail), "UNKNOWN")

        # --- finalize run row ---
        if db:
            try:
                db.execute(
                    "UPDATE run SET finished_at=?, total_cost=?, verdict=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), spent, verdict, run_id),
                )
                db.commit()
            except Exception:
                pass

        print(f"\n=== summary ===")
        for phase, cost in costs.items():
            print(f"  {phase}: ${cost:.4f}")
        print(f"  total: ${spent:.4f}")
        print(f"  branch: {branch}")
        print(f"  verdict: {verdict}")
        print(f"  outputs: {out_dir}")
        print(f"  run db:  {db_path}")

    try:
        # --- triage ---
        triage = run_phase("1-triage", "triage")
        if triage is None:
            print("triage failed — nothing to execute, aborting.")
            sys.exit(1)
        if "SKIP" in triage or "ESCALATE" in triage:
            triage_file = out_dir / "1-triage.md"
            print(f"triage halted. notes: {triage_file}")
            return

        # --- plan (optional — skipped if prompts/plan.md doesn't exist) ---
        if (DIR / "prompts" / "plan.md").exists():
            plan_out = run_phase("2-plan", "plan")
            if plan_out is None:
                print("plan failed — nothing to execute, aborting.")
                sys.exit(1)
            plan_file = out_dir / "2-plan.md"
            plan_text = plan_file.read_text() if plan_file.exists() else plan_out
        else:
            plan_text = triage

        # --- execute: dispatch one sub-agent per task ---
        tasks = parse_tasks(plan_text)
        if not tasks:
            tasks = [plan_text]
        print(f"  execute: {len(tasks)} tasks from plan")

        exec_meta, exec_prompt_template = load_prompt("execute",
            repo_context=f"Repo: {repo}\nRef: {args.git_ref}\nBranch: {branch}",
            issue_body=issue_body,
            issue_number=str(args.issue or "N/A"),
            recent_learnings="(none)",
            prior_phases=(out_dir / "1-triage.md").read_text() + "\n\n" + plan_text,
        )
        exec_model = exec_meta.get("model", args.model)
        exec_max_turns = int(exec_meta.get("max_turns", 30))

        for i, task in enumerate(tasks, 1):
            task_prompt = f"{exec_prompt_template}\n\n---\n\n## Your task (task {i} of {len(tasks)})\n\n{task}\n\nFocus ONLY on this task. Make commits when done."
            print(f"  task {i}/{len(tasks)}: {task[:80]}...")
            phase_started = datetime.now(timezone.utc).isoformat()
            try:
                resp, events = call_claude(task_prompt, wt, exec_model, exec_max_turns)
            except ClaudeError as e:
                phase_finished = datetime.now(timezone.utc).isoformat()
                if db:
                    _log_phase(db, run_id, f"execute-{i}", "execute",
                               exec_model, exec_max_turns,
                               phase_started, phase_finished,
                               0.0, 0, 0, task_prompt, str(e), [])
                print(f"  task {i} FAILED: {e}")
                costs[f"execute-{i}"] = 0
                continue
            phase_finished = datetime.now(timezone.utc).isoformat()
            cost = resp.get("total_cost_usd", resp.get("cost_usd", 0.0))
            input_tokens, output_tokens = _extract_tokens(resp)
            costs[f"execute-{i}"] = cost
            spent += cost
            result = resp.get("result", "")

            if db:
                _log_phase(db, run_id, f"execute-{i}", "execute",
                           exec_model, exec_max_turns,
                           phase_started, phase_finished, cost,
                           input_tokens, output_tokens,
                           task_prompt, result, events)

            task_file = out_dir / f"3-execute-task-{i}.md"
            if not task_file.exists():
                out_dir.mkdir(parents=True, exist_ok=True)
                task_file.write_text(result)
            print(f"  task {i} done: ${cost:.4f}  (total: ${spent:.4f})")
            if spent > args.budget:
                print(f"budget exceeded at task {i}", file=sys.stderr)
                break

        # --- review ---
        diff = subprocess.run(["git", "diff", f"{args.git_ref}...HEAD"],
                             cwd=wt, capture_output=True, text=True).stdout
        run_phase("4-review", "review", combined_diff=diff[:50_000])

        # --- improve (optional) ---
        if (DIR / "prompts" / "improve.md").exists():
            run_phase("5-improve", "improve")

        # --- push ---
        push = subprocess.run(["git", "push", "origin", branch],
                             cwd=wt, capture_output=True, text=True)
        if push.returncode != 0:
            print(f"push failed: {push.stderr}", file=sys.stderr)
    finally:
        print_summary()


if __name__ == "__main__":
    main()
