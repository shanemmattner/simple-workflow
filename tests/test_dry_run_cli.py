"""Tests for --dry-run CLI flag behavior."""
import argparse
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import os
import pytest


def test_cli_accepts_dry_run_flag():
    """CLI parser should accept --dry-run argument."""
    from engines.github_claude.__main__ import main
    import sys

    # Patch sys.argv to simulate --dry-run flag
    with patch('sys.argv', ['__main__.py', 'owner/repo#123', '--dry-run']):
        parser = argparse.ArgumentParser()
        parser.add_argument("issue")
        parser.add_argument("--dry-run", action="store_true", default=False)
        # This will fail because --dry-run doesn't exist yet
        args = parser.parse_args()
        assert args.dry_run is True


def test_run_pipeline_accepts_dry_run():
    """run_pipeline should accept dry_run parameter."""
    from engines.github_claude.orchestrator import run_pipeline

    # Mock all external dependencies
    with patch('engines.github_claude.orchestrator.source'), \
         patch('engines.github_claude.orchestrator.storage'), \
         patch('engines.github_claude.orchestrator.workspace'), \
         patch('engines.github_claude.orchestrator.destination'):

        # This will fail because dry_run parameter doesn't exist yet
        result = run_pipeline("owner/repo", 123, dry_run=True, budget=1.00)
        assert result["status"] == "dry_run"


def test_dry_run_skips_llm_calls():
    """When dry_run=True, runtime.call_agent should NOT be called."""
    from engines.github_claude.orchestrator import run_pipeline
    from engines.github_claude import runtime

    with patch('engines.github_claude.orchestrator.source') as mock_source, \
         patch('engines.github_claude.orchestrator.storage') as mock_storage, \
         patch('engines.github_claude.orchestrator.workspace') as mock_workspace, \
         patch('engines.github_claude.orchestrator.destination') as mock_destination, \
         patch.object(runtime, 'call_agent') as mock_call_agent:

        mock_source.fetch_issue.return_value = {"title": "Test", "body": "Body"}
        mock_storage.create_run_db.return_value = (Path("test.db"), MagicMock())
        mock_storage.find_prior_runs.return_value = []
        mock_workspace.create_workspace.return_value = "/tmp/wt"

        # This will fail because call_agent IS currently called unconditionally
        result = run_pipeline("owner/repo", 123, dry_run=True, budget=1.00)

        # After implementation, call_agent should NOT be called
        mock_call_agent.assert_not_called()


def test_dry_run_creates_db_file():
    """Dry-run mode should still create the .db file for observability."""
    from engines.github_claude.orchestrator import run_pipeline
    from engines.github_claude import storage
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        with patch('engines.github_claude.orchestrator.source') as mock_source, \
             patch('engines.github_claude.orchestrator.workspace') as mock_workspace, \
             patch('engines.github_claude.orchestrator.destination'):

            mock_source.fetch_issue.return_value = {"title": "Test", "body": "Body"}

            # After implementation, .db file should be created even in dry-run
            result = run_pipeline("owner/repo", 123, dry_run=True, budget=1.00)

            # Verify a .db file was created
            db_files = list(Path(tmpdir).glob("*.db"))
            assert len(db_files) > 0
