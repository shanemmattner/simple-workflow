"""CLI entry point for the github_minimax engine.

Multi-turn tool-dispatch agent loop against MiniMax M3 / M2.7-highspeed
via the OpenAI-compatible endpoint at https://api.minimax.io/v1.

Usage:
    python -m engines.github_minimax owner/repo#123
    python -m engines.github_minimax owner/repo#123 --budget 1.00 --model m27hs
    python -m engines.github_minimax owner/repo#123 --phases triage --budget 0.50
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="engines.github_minimax",
        description=(
            "GitHub issue -> tested PR pipeline "
            "(github_minimax engine — MiniMax M3 / M2.7-highspeed via OpenAI-compat)"
        ),
    )
    parser.add_argument("issue", help="Issue ref: owner/repo#NNN")
    parser.add_argument("--budget", type=float, default=1.00,
                        help="Max spend in USD (default: 1.00)")
    parser.add_argument("--model", default="MiniMax-M3",
                        help="Default model: MiniMax-M3 (alias m3) or "
                             "MiniMax-M2.7-highspeed (alias m27hs). "
                             "Override with --model m27hs or full ID.")
    parser.add_argument("--repo-path", default=None,
                        help="Local filesystem path to the repo (default: auto-detect)")
    parser.add_argument("--phases", default=None,
                        help="Comma-separated list of phases to run "
                             "(e.g. triage,verify,plan). Default: all phases from workflow.yaml.")
    parser.add_argument("--workflow", default=None,
                        help="Path to workflow dir (default: workflows/issue-to-pr)")
    args = parser.parse_args()

    # Parse repo#issue format
    if "#" not in args.issue:
        parser.error("Issue ref must be owner/repo#NNN")
    repo_part, number_str = args.issue.rsplit("#", 1)
    try:
        issue_number = int(number_str)
    except ValueError:
        parser.error(f"Invalid issue number: {number_str}")

    parts = repo_part.split("/")
    if len(parts) != 2:
        parser.error(f"Invalid repo format: {repo_part} (expected owner/repo)")
    owner, repo = parts

    workflow_dir = Path(args.workflow) if args.workflow else None

    # Import here to avoid top-level side effects
    from engines.github_minimax.orchestrator import run_pipeline

    print(f"github_minimax: {owner}/{repo}#{issue_number}")
    print(f"  budget: ${args.budget:.2f}  model: {args.model}")
    if args.phases:
        print(f"  phases: {args.phases}")

    result = run_pipeline(
        f"{owner}/{repo}", issue_number,
        budget=args.budget,
        model_override=args.model,
        repo_path=args.repo_path,
        workflow_dir=workflow_dir,
        phases=args.phases,
    )

    print(f"\n{'=' * 60}")
    print(f"  Status:  {result['status']}")
    print(f"  Cost:    ${result.get('spent_usd', 0):.4f}")
    if result.get("pr_url"):
        print(f"  PR:      {result['pr_url']}")
    if result.get("error"):
        print(f"  Error:   {result['error']}")
    print(f"  Run ID:  {result['run_id']}")
    print(f"{'=' * 60}")

    sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
