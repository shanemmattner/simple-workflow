"""Test that --dry-run flag is accepted and forwarded to run_pipeline."""
import sys
from unittest.mock import patch, MagicMock

import pytest


def _invoke_main(extra_args=None):
    """Call engines.github_claude.__main__.main() with controlled argv."""
    argv = ["engines.github_claude", "owner/repo#1"] + (extra_args or [])
    stub_result = {
        "status": "ok",
        "spent_usd": 0.0,
        "pr_url": None,
        "error": None,
        "run_id": "test-run-id",
    }
    with patch("engines.github_claude.orchestrator.run_pipeline", return_value=stub_result) as mock_rp:
        with patch.object(sys, "argv", argv):
            from engines.github_claude import __main__
            import importlib
            importlib.reload(__main__)  # re-parse argv each call
            try:
                __main__.main()
            except SystemExit as e:
                if e.code not in (0, None):
                    raise
    return mock_rp


def test_dry_run_flag_passed_to_run_pipeline(capsys):
    """--dry-run must reach run_pipeline as dry_run=True."""
    mock_rp = _invoke_main(["--dry-run"])
    call_kwargs = mock_rp.call_args.kwargs
    assert call_kwargs.get("dry_run") is True, (
        f"run_pipeline not called with dry_run=True; kwargs={call_kwargs}"
    )
    captured = capsys.readouterr()
    assert "[DRY RUN]" in captured.out, "Expected [DRY RUN] banner in stdout"
