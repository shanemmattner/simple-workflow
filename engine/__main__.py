"""CLI entry point for the github_claude engine.

Usage:
    # Domain workflows (run.sh primary path): repo path + git ref, issue optional.
    python -m engine --repo-path /path/to/repo --base abc123f --workflow shftty-web [--issue 896]
    python -m engine --repo-path /path/to/repo --base main --workflow shftty-web --budget 5.00

    # Legacy issue-to-pr pipeline (no --workflow): still requires owner/repo + --issue.
    python -m engine owner/repo --issue 123 --repo-path /path/to/repo
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="engine",
        description="Repo path + git ref -> tested branch (or issue -> PR) pipeline (Claude engine)",
    )
    parser.add_argument("repo", nargs="?", default=None,
                        help="owner/repo slug (derived from git remote by run.sh; "
                             "used for issue comments/context when --issue is set)")
    parser.add_argument("--issue", type=int, default=None,
                        help="Issue number for context (optional). When omitted, "
                             "the pipeline runs without issue context.")
    parser.add_argument("--base", default="main",
                        help="Git ref (branch/tag/commit) to start the worktree from (default: main)")
    parser.add_argument("--budget", type=float, default=1.00,
                        help="Max spend in USD (default: 1.00)")
    parser.add_argument("--model", default=None,
                        help="Default model override (default: None → use workflow.yaml per-phase routing)")
    parser.add_argument("--repo-path", default=None,
                        help="Local filesystem path to the repo (default: cwd)")
    parser.add_argument("--workflow", default=None,
                        help="Path to workflow dir (default: workflows/issue-to-pr)")
    parser.add_argument("--resume", default=None,
                        help="Resume from a prior run DB path or run ID")
    args = parser.parse_args()

    # Configure logging so gate decisions and phase transitions appear on stdout
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    repo = args.repo
    issue_number = args.issue

    # Import here to avoid top-level side effects
    from engine.orchestrator import run_pipeline, run_domain_pipeline

    print(f"github_claude: repo={repo or '(none)'} issue={issue_number if issue_number is not None else '(none)'} base={args.base}")
    print(f"  budget: ${args.budget:.2f}  model: {args.model}")

    if args.workflow:
        print(f"  workflow: {args.workflow}  (domain pipeline)")
        result = run_domain_pipeline(
            repo, issue_number,
            workflow=args.workflow,
            base_ref=args.base,
            budget=args.budget,
            model_override=args.model,
            repo_path=args.repo_path,
        )
    else:
        if not repo or issue_number is None:
            parser.error(
                "the legacy issue-to-pr pipeline (no --workflow) requires both "
                "a repo positional arg (owner/repo) and --issue NNN"
            )
        result = run_pipeline(
            repo, issue_number,
            budget=args.budget,
            model_override=args.model,
            repo_path=args.repo_path,
            resume_from=args.resume,
        )

    print(f"\n{'=' * 60}")
    print(f"  Status:  {result['status']}")
    print(f"  Cost:    ${result.get('spent_usd', 0):.4f}")
    if result.get("pr_url"):
        print(f"  PR:      {result['pr_url']}")
    if result.get("branch"):
        print(f"  Branch:  {result['branch']}")
        print(f"  Review:  {result.get('review_signal', 'n/a')}")
    if result.get("error"):
        print(f"  Error:   {result['error']}")
    print(f"  Run ID:  {result['run_id']}")
    print(f"{'=' * 60}")

    sys.exit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
