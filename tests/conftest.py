"""Shared fixtures for simple-workflow tests."""
from __future__ import annotations

import pytest

from engine import storage


@pytest.fixture()
def tmp_runs_dir(tmp_path, monkeypatch):
    """Redirect storage._RUNS_DIR to a temp dir so tests don't pollute runs/."""
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(storage, "_RUNS_DIR", runs)
    return runs


@pytest.fixture()
def run_db(tmp_runs_dir):
    """Return (db_path, conn) for a fresh per-test run DB."""
    db_path, conn = storage.create_run_db(
        repo="testorg/testrepo",
        issue_number=42,
        issue_url="https://github.com/testorg/testrepo/issues/42",
        model="haiku",
        prompt_sha="abc123",
    )
    yield db_path, conn
    conn.close()
