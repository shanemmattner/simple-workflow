"""Eval module — LLM-as-judge scoring, failure categorization, cross-run pattern
detection, and prompt improvement proposals.

Part of the closed-loop improvement cycle (PRD section 7):

    Run completes -> Judge (score + categorize) -> Propose prompt edit
    -> Human reviews -> Prompt updated -> Next run uses new prompt
    -> Compare: did the score improve?
"""

from __future__ import annotations

FAILURE_CATEGORIES: list[str] = [
    "NO_EDITS",
    "WRONG_FILES",
    "THIN_REPORT",
    "DEBUG_LEFTOVERS",
    "TYPE_ERROR",
    "TEST_FAILURE",
    "RE_INVESTIGATION",
    "INCOMPLETE_FIX",
    "OVERCOMPLICATED",
]


def judge_run(db_path: str) -> dict:
    """Score a completed run.

    Opens the run's .db file, reads phase outcomes and review, returns
    verdict dict with: passed (bool), score (0-100), failure_category
    (str or None), summary (str).
    """
    raise NotImplementedError("TODO: implement LLM-as-judge scoring")


def categorize_failure(db_path: str) -> str | None:
    """Categorize a failed run into one of: wrong_scope, incomplete_impl,
    test_failure, hallucination, timeout, format_error.

    Returns None if run passed.
    """
    raise NotImplementedError("TODO: implement failure categorization")


def find_patterns(runs_dir: str, min_occurrences: int = 3) -> list[dict]:
    """Scan all .db files in runs_dir, find recurring failure categories.

    Returns list of patterns: {category, count, example_run_ids,
    affected_phases}.
    """
    raise NotImplementedError("TODO: implement cross-run pattern detection")


def propose_prompt_edit(pattern: dict, prompt_path: str) -> dict | None:
    """Given a failure pattern and the prompt file that produced the failures,
    propose a minimal edit.

    Returns {prompt_path, current_text, suggested_text, rationale} or None
    if no suggestion.
    """
    raise NotImplementedError("TODO: implement prompt improvement suggestions")


def compare_prompt_versions(prompt_path: str, runs_dir: str) -> dict:
    """Compare success rates across different git SHAs of a prompt file.

    Returns {sha: {runs, passed, failed, avg_cost}} for each version seen.
    """
    raise NotImplementedError("TODO: implement prompt version comparison")
