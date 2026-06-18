"""Tests for destination module dry-run behavior."""
from unittest.mock import patch, MagicMock
import pytest


def test_push_branch_skips_git_when_dry_run():
    """push_branch(dry_run=True) must skip git push and return early."""
    from engines.github_claude.destination import push_branch

    with patch("subprocess.run") as mock_run:
        push_branch("/fake/path", "test-branch", dry_run=True)
        # Should not call git push
        for call in mock_run.call_args_list:
            args = call[0][0] if call[0] else []
            assert not ("git", "push") in tuple(args), "git push should not be called in dry-run"


def test_push_branch_calls_git_when_not_dry_run():
    """push_branch(dry_run=False) must call git push normally."""
    from engines.github_claude.destination import push_branch

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        push_branch("/fake/path", "test-branch", dry_run=False)
        # Should call git push
        push_calls = [call for call in mock_run.call_args_list
                     if call[0] and "push" in call[0][0]]
        assert len(push_calls) > 0, "git push should be called when dry_run=False"


def test_create_pr_skips_gh_when_dry_run():
    """create_pr(dry_run=True) must skip gh pr create and return early."""
    from engines.github_claude.destination import create_pr

    with patch("subprocess.run") as mock_run:
        result = create_pr("owner/repo", "test-branch", "Title", "Body", dry_run=True)
        # Should not call gh pr create
        for call in mock_run.call_args_list:
            args = call[0][0] if call[0] else []
            assert not ("gh", "pr", "create") in tuple(args), "gh pr create should not be called in dry-run"
        # Should return a dry-run status dict
        assert "skipped" in result or "dry_run" in result


def test_create_pr_calls_gh_when_not_dry_run():
    """create_pr(dry_run=False) must call gh pr create normally."""
    from engines.github_claude.destination import create_pr

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="https://github.com/owner/repo/pull/123",
            stderr=""
        )
        result = create_pr("owner/repo", "test-branch", "Title", "Body", dry_run=False)
        # Should call gh pr create
        pr_calls = [call for call in mock_run.call_args_list
                   if call[0] and "gh" in call[0][0] and "pr" in call[0][0]]
        assert len(pr_calls) > 0, "gh pr create should be called when dry_run=False"
        assert result["number"] == 123
