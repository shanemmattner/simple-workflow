#!/usr/bin/env python3
"""shftty-web workflow: triage -> plan -> execute -> review -> improve.

Self-contained: no imports from engine/. Copy this folder to make a new
workflow; edit the prompts; run it.

Each phase writes a markdown file to .workflow-outputs/ in the worktree.
The file is the phase's notes — partial progress survives crashes. Each
agent gets the previous phase's output file as context.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).parent


def call_claude(prompt: str, cwd: str, model: str, max_turns: int) -> dict:
    proc = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "json",
         "--max-turns", str(max_turns)],
        input=prompt, capture_output=True, text=True, cwd=cwd,
    )
    if proc.returncode != 0:
        print(f"claude error (exit {proc.returncode}): {proc.stderr}", file=sys.stderr)
        sys.exit(1)
    try:
        events = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"could not parse claude output: {proc.stdout[:500]}", file=sys.stderr)
        sys.exit(1)
    for event in events:
        if event.get("type") == "result":
            return event
    print("no result event in claude output", file=sys.stderr)
    sys.exit(1)


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
    for k, v in kwargs.items():
        text = text.replace(f"${k}", str(v))
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_path", help="Path to the git repo")
    parser.add_argument("git_ref", help="Git ref to start from")
    parser.add_argument("--issue", type=int, default=None)
    parser.add_argument("--budget", type=float, default=10.0)
    parser.add_argument("--model", default="sonnet")
    args = parser.parse_args()

    repo = str(Path(args.repo_path).resolve())
    branch = f"sw/shftty-web-{args.issue or 'run'}-{int(time.time())}"
    wt = f"/tmp/sw-{branch.replace('/', '-')}"
    out_dir = Path(wt) / ".workflow-outputs"

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
        prompt += f"\n\nIMPORTANT: Write your full output to {out_file} as you work. This file is your notes — write findings as you discover them, not just at the end."

        print(f"--- {name} ---")
        resp = call_claude(prompt, wt, model, max_turns)
        result = resp.get("result", "")
        cost = resp.get("total_cost_usd", resp.get("cost_usd", 0.0))
        costs[name] = cost
        spent += cost

        if not out_file.exists():
            out_file.write_text(result)

        print(f"  done: ${cost:.4f}  (total: ${spent:.4f})")
        if spent > args.budget:
            print(f"budget exceeded (${spent:.2f} > ${args.budget})", file=sys.stderr)
            sys.exit(1)
        return result

    # --- triage ---
    triage = run_phase("1-triage", "triage")
    if "SKIP" in triage or "ESCALATE" in triage:
        triage_file = out_dir / "1-triage.md"
        print(f"triage halted. notes: {triage_file}")
        sys.exit(0)

    # --- plan ---
    plan_out = run_phase("2-plan", "plan")
    plan_file = out_dir / "2-plan.md"
    plan_text = plan_file.read_text() if plan_file.exists() else plan_out

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
        task_prompt = f"{exec_prompt_template}\n\n---\n\n## Your task (task {i} of {len(tasks)})\n\n{task}\n\nFocus ONLY on this task. Make commits when done. Write progress to {out_dir}/3-execute-task-{i}.md"
        print(f"  task {i}/{len(tasks)}: {task[:80]}...")
        resp = call_claude(task_prompt, wt, exec_model, exec_max_turns)
        cost = resp.get("total_cost_usd", resp.get("cost_usd", 0.0))
        costs[f"execute-{i}"] = cost
        spent += cost
        result = resp.get("result", "")
        task_file = out_dir / f"3-execute-task-{i}.md"
        if not task_file.exists():
            task_file.write_text(result)
        print(f"  task {i} done: ${cost:.4f}  (total: ${spent:.4f})")
        if spent > args.budget:
            print(f"budget exceeded at task {i}", file=sys.stderr)
            break

    # --- review ---
    diff = subprocess.run(["git", "diff", f"{args.git_ref}...HEAD"],
                         cwd=wt, capture_output=True, text=True).stdout
    run_phase("4-review", "review", combined_diff=diff[:50_000])

    # --- improve ---
    run_phase("5-improve", "improve")

    # --- push ---
    push = subprocess.run(["git", "push", "origin", branch],
                         cwd=wt, capture_output=True, text=True)
    if push.returncode != 0:
        print(f"push failed: {push.stderr}", file=sys.stderr)

    # --- summary ---
    review_file = out_dir / "4-review.md"
    review_tail = review_file.read_text()[-300:] if review_file.exists() else ""
    verdict = next((v for v in ("FAIL", "WARN", "PASS") if v in review_tail), "UNKNOWN")

    print(f"\n=== summary ===")
    for phase, cost in costs.items():
        print(f"  {phase}: ${cost:.4f}")
    print(f"  total: ${spent:.4f}")
    print(f"  branch: {branch}")
    print(f"  verdict: {verdict}")
    print(f"  outputs: {out_dir}")


if __name__ == "__main__":
    main()
