"""Tests for engines/github_claude/destination.py"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from engines.github_claude.destination import (
    BranchNotFound,
    PRAlreadyExists,
    create_pr,
    push_branch,
)


def _make_result(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


class TestCreatePrDefaultBase:
    """create_pr must target 'main' by default (regression for issue #68)."""

    def test_default_base_is_main(self):
        """No explicit base → gh pr create receives --base main."""
        url = "https://github.com/owner/repo/pull/42"
        with patch("subprocess.run", return_value=_make_result(stdout=url)) as mock_run:
            result = create_pr(repo="owner/repo", branch="feat/x", title="T", body="B")

        cmd = mock_run.call_args[0][0]
        assert "--base" in cmd
        base_idx = cmd.index("--base")
        assert cmd[base_idx + 1] == "main", f"expected 'main', got {cmd[base_idx + 1]!r}"
        assert result["number"] == 42
        assert result["url"] == url

    def test_explicit_base_is_honoured(self):
        """Explicit base overrides the default."""
        url = "https://github.com/owner/repo/pull/7"
        with patch("subprocess.run", return_value=_make_result(stdout=url)) as mock_run:
            create_pr(repo="owner/repo", branch="feat/y", title="T", body="B", base="release")

        cmd = mock_run.call_args[0][0]
        base_idx = cmd.index("--base")
        assert cmd[base_idx + 1] == "release"

    def test_pr_already_exists_raises(self):
        with patch(
            "subprocess.run",
            return_value=_make_result(returncode=1, stderr="a pr already exists for branch 'feat/x'"),
        ):
            with pytest.raises(PRAlreadyExists):
                create_pr(repo="owner/repo", branch="feat/x", title="T", body="B")

    def test_gh_failure_raises_runtime_error(self):
        with patch(
            "subprocess.run",
            return_value=_make_result(returncode=1, stderr="some other error"),
        ):
            with pytest.raises(RuntimeError):
                create_pr(repo="owner/repo", branch="feat/x", title="T", body="B")


class TestPushBranch:
    def test_branch_not_found_raises(self):
        with patch(
            "subprocess.run",
            return_value=_make_result(returncode=1, stderr="unknown revision"),
        ):
            with pytest.raises(BranchNotFound):
                push_branch("/tmp/repo", "feat/x")
