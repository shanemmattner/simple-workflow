"""CLI entry point for the github_openhands engine.

Usage:
    python -m engines.github_openhands owner/repo#123
    python -m engines.github_openhands owner/repo#123 --budget 2.00 --model deepseek/deepseek-v4-flash
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="engines.github_openhands",
        description="GitHub issue -> tested PR pipeline (OpenHands engine)",
    )
    parser.add_argument("issue", help="Issue ref: owner/repo#NNN")
    parser.add_argument("--budget", type=float, default=1.00,
                        help="Max spend in USD (default: 1.00)")
    parser.add_argument("--model", default="openrouter/deepseek/deepseek-v4-flash",
                        help="Default model (default: openrouter/deepseek/deepseek-v4-flash)")
    parser.add_argument("--repo-path", default=None,
                        help="Local filesystem path to the repo (default: cwd)")
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
    from engines.github_openhands.orchestrator import run_pipeline

    print(f"github_openhands: {owner}/{repo}#{issue_number}")
    print(f"  budget: ${args.budget:.2f}  model: {args.model}")

    result = run_pipeline(
        f"{owner}/{repo}", issue_number,
        budget=args.budget,
        model_override=args.model,
        repo_path=args.repo_path,
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
