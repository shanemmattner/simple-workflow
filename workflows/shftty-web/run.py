#!/usr/bin/env python3
"""shftty-web workflow: triage -> plan -> execute -> review -> improve.

Self-contained: no imports from engine/. Copy this folder to make a new
workflow; edit the prompts; run it.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).parent


def call_claude(prompt: str, cwd: str, model: str, max_turns: int) -> dict:
    """Call claude -p and return the parsed JSON response."""
    proc = subprocess.run(
        ["claude", "-p", "--model", model, "--output-format", "json",
         "--max-turns", str(max_turns)],
        input=prompt, capture_output=True, text=True, cwd=cwd,
    )
    if proc.returncode != 0:
        print(f"claude error (exit {proc.returncode}): {proc.stderr}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"could not parse claude output: {proc.stdout[:500]}", file=sys.stderr)
        sys.exit(1)


def load_prompt(name: str, **kwargs) -> str:
    """Load a prompt template and substitute {var} placeholders."""
    text = (DIR / "prompts" / f"{name}.md").read_text()
    for k, v in kwargs.items():
        text = text.replace(f"{{{k}}}", str(v))
    return text


def fetch_issue_body(repo_path: str, issue: int) -> str:
    proc = subprocess.run(
        ["gh", "issue", "view", str(issue), "--json", "title,body"],
        capture_output=True, text=True, cwd=repo_path,
    )
    if proc.returncode != 0:
        print(f"could not fetch issue #{issue}: {proc.stderr}", file=sys.stderr)
        return ""
    data = json.loads(proc.stdout)
    return f"{data.get('title', '')}\n\n{data.get('body', '')}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_path", help="Path to the git repo")
    parser.add_argument("git_ref", help="Git ref to start from")
    parser.add_argument("--issue", type=int, default=None, help="Issue number for context")
    parser.add_argument("--budget", type=float, default=10.0)
    parser.add_argument("--model", default="sonnet")
    args = parser.parse_args()

    repo_path = str(Path(args.repo_path).resolve())
    branch = f"shftty-web/{args.issue or 'run'}-{int(time.time())}"
    worktree_path = f"/tmp/shftty-web-{branch.replace('/', '-')}"

    print(f"creating worktree: {worktree_path} from {args.git_ref} (branch {branch})")
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, worktree_path, args.git_ref],
        cwd=repo_path, check=True,
    )

    issue_body = fetch_issue_body(repo_path, args.issue) if args.issue else "(no issue provided)"
    issue_number = args.issue or "N/A"
    repo_context = f"Repo: {repo_path}\nRef: {args.git_ref}\nBranch: {branch}"
    recent_learnings = "(none)"

    outputs = {}
    costs = {}

    def run_phase(name: str, max_turns: int, **extra_vars) -> str:
        prompt = load_prompt(
            name,
            repo_context=repo_context,
            issue_body=issue_body,
            issue_number=issue_number,
            recent_learnings=recent_learnings,
            prior_phases="\n\n".join(f"## {p}\n{o}" for p, o in outputs.items()),
            **extra_vars,
        )
        print(f"--- running phase: {name} ---")
        resp = call_claude(prompt, worktree_path, args.model, max_turns)
        result = resp.get("result", "")
        cost = resp.get("total_cost_usd", resp.get("cost_usd", 0.0))
        costs[name] = cost
        outputs[name] = result
        print(f"phase {name} done: cost=${cost:.4f}")
        if sum(costs.values()) > args.budget:
            print(f"budget ${args.budget} exceeded (spent ${sum(costs.values()):.2f}), stopping", file=sys.stderr)
            sys.exit(1)
        return result

    triage_out = run_phase("triage", 30)
    if "SKIP" in triage_out or "ESCALATE" in triage_out:
        print(f"triage decided to halt (SKIP/ESCALATE found). Branch: {branch}")
        print(triage_out[-1500:])
        sys.exit(0)

    run_phase("plan", 20)
    run_phase("execute", 50)

    diff = subprocess.run(
        ["git", "diff", f"{args.git_ref}...HEAD"],
        cwd=worktree_path, capture_output=True, text=True,
    ).stdout
    run_phase("review", 20, combined_diff=diff)
    run_phase("improve", 10)

    push = subprocess.run(["git", "push", "origin", branch], cwd=worktree_path, capture_output=True, text=True)
    if push.returncode != 0:
        print(f"push failed: {push.stderr}", file=sys.stderr)

    review_tail = outputs.get("review", "")[-300:]
    verdict = next((v for v in ("FAIL", "WARN", "PASS") if v in review_tail), "UNKNOWN")
    print("\n=== summary ===")
    for phase, cost in costs.items():
        print(f"  {phase}: ${cost:.4f}")
    print(f"  total: ${sum(costs.values()):.4f}")
    print(f"branch: {branch}")
    print(f"review verdict: {verdict}")


if __name__ == "__main__":
    main()
