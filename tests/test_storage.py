"""Tests for engines/github_claude/storage.py — DB CRUD operations."""
from __future__ import annotations

import json
import sqlite3

import pytest

from engines.github_claude import storage


# ---------------------------------------------------------------------------
# create_run_db
# ---------------------------------------------------------------------------

class TestCreateRunDb:
    def test_creates_db_file(self, tmp_runs_dir):
        db_path, conn = storage.create_run_db("org/repo", 1)
        conn.close()
        assert tmp_runs_dir.glob("*.db"), "expected at least one .db file"

    def test_db_path_contains_repo_and_issue(self, tmp_runs_dir):
        db_path, conn = storage.create_run_db("myorg/myrepo", 99)
        conn.close()
        assert "myorg-myrepo" in db_path
        assert "-99-" in db_path

    def test_run_row_inserted(self, run_db):
        _, conn = run_db
        row = conn.execute("SELECT * FROM run LIMIT 1").fetchone()
        assert row is not None
        assert row["repo"] == "testorg/testrepo"
        assert row["issue_number"] == 42
        assert row["status"] == "running"
        assert row["model"] == "haiku"

    def test_prompt_sha_stored(self, run_db):
        _, conn = run_db
        row = conn.execute("SELECT prompt_sha FROM run LIMIT 1").fetchone()
        assert row["prompt_sha"] == "abc123"

    def test_schema_tables_exist(self, run_db):
        _, conn = run_db
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {"run", "phase", "message", "tool_call", "event"} <= tables


# ---------------------------------------------------------------------------
# log_phase / finish_phase
# ---------------------------------------------------------------------------

class TestPhase:
    def test_log_phase_returns_id(self, run_db):
        _, conn = run_db
        pid = storage.log_phase(conn, "triage", status="running", model="haiku")
        assert isinstance(pid, int)
        assert pid > 0

    def test_log_phase_row_exists(self, run_db):
        _, conn = run_db
        pid = storage.log_phase(conn, "verify", status="running")
        row = conn.execute("SELECT * FROM phase WHERE id=?", (pid,)).fetchone()
        assert row is not None
        assert row["phase_name"] == "verify"

    def test_finish_phase_updates_status(self, run_db):
        _, conn = run_db
        pid = storage.log_phase(conn, "plan", status="running")
        storage.finish_phase(conn, pid, "success", cost=0.01, tokens_in=100, tokens_out=200)
        row = conn.execute("SELECT * FROM phase WHERE id=?", (pid,)).fetchone()
        assert row["status"] == "success"
        assert row["cost"] == pytest.approx(0.01)
        assert row["tokens_in"] == 100

    def test_finish_phase_sets_finished_at(self, run_db):
        _, conn = run_db
        pid = storage.log_phase(conn, "execute", status="running")
        storage.finish_phase(conn, pid, "success")
        row = conn.execute("SELECT finished_at FROM phase WHERE id=?", (pid,)).fetchone()
        assert row["finished_at"] is not None


# ---------------------------------------------------------------------------
# log_message
# ---------------------------------------------------------------------------

class TestMessage:
    def test_log_message_returns_id(self, run_db):
        _, conn = run_db
        pid = storage.log_phase(conn, "triage", status="running")
        mid = storage.log_message(conn, pid, 1, "assistant", "hello world")
        assert isinstance(mid, int)
        assert mid > 0

    def test_log_message_content_stored(self, run_db):
        _, conn = run_db
        pid = storage.log_phase(conn, "triage", status="running")
        mid = storage.log_message(conn, pid, 1, "user", "test content",
                                   tokens_in=10, tokens_out=20, cost=0.001)
        row = conn.execute("SELECT * FROM message WHERE id=?", (mid,)).fetchone()
        assert row["content"] == "test content"
        assert row["role"] == "user"
        assert row["turn_number"] == 1


# ---------------------------------------------------------------------------
# log_tool_call
# ---------------------------------------------------------------------------

class TestToolCall:
    def test_log_tool_call(self, run_db):
        _, conn = run_db
        pid = storage.log_phase(conn, "execute", status="running")
        mid = storage.log_message(conn, pid, 1, "assistant", "doing work")
        storage.log_tool_call(
            conn, mid, pid, "Bash", '{"cmd": "ls"}', "file1\nfile2", 42
        )
        row = conn.execute("SELECT * FROM tool_call WHERE message_id=?", (mid,)).fetchone()
        assert row is not None
        assert row["tool_name"] == "Bash"
        assert row["duration_ms"] == 42


# ---------------------------------------------------------------------------
# log_event
# ---------------------------------------------------------------------------

class TestEvent:
    def test_log_event_stored(self, run_db):
        _, conn = run_db
        storage.log_event(conn, "phase_start", {"phase": "triage"})
        row = conn.execute("SELECT * FROM event WHERE event_type='phase_start'").fetchone()
        assert row is not None
        details = json.loads(row["details"])
        assert details["phase"] == "triage"

    def test_log_event_with_phase_id(self, run_db):
        _, conn = run_db
        pid = storage.log_phase(conn, "verify", status="running")
        storage.log_event(conn, "gate_pass", {"gate": "validate_verify"}, phase_id=pid)
        row = conn.execute("SELECT * FROM event WHERE event_type='gate_pass'").fetchone()
        assert row["phase_id"] == pid


# ---------------------------------------------------------------------------
# finish_run
# ---------------------------------------------------------------------------

class TestFinishRun:
    def test_finish_run_updates_status(self, run_db):
        _, conn = run_db
        storage.finish_run(conn, "success", total_cost=0.05,
                           total_tokens_in=1000, total_tokens_out=500)
        row = conn.execute("SELECT * FROM run LIMIT 1").fetchone()
        assert row["status"] == "success"
        assert row["total_cost"] == pytest.approx(0.05)

    def test_finish_run_sets_review_fields(self, run_db):
        _, conn = run_db
        storage.finish_run(conn, "success",
                           review_verdict="APPROVE",
                           review_summary="looks good")
        row = conn.execute("SELECT review_verdict, review_summary FROM run LIMIT 1").fetchone()
        assert row["review_verdict"] == "APPROVE"
        assert row["review_summary"] == "looks good"


# ---------------------------------------------------------------------------
# find_prior_runs
# ---------------------------------------------------------------------------

class TestFindPriorRuns:
    def test_finds_created_run(self, tmp_runs_dir):
        db_path, conn = storage.create_run_db("myorg/myrepo", 7)
        conn.close()
        results = storage.find_prior_runs("myorg/myrepo", 7)
        assert len(results) >= 1
        assert results[0]["repo"] == "myorg/myrepo"
        assert results[0]["issue_number"] == 7

    def test_no_results_for_different_issue(self, tmp_runs_dir):
        db_path, conn = storage.create_run_db("myorg/myrepo", 7)
        conn.close()
        results = storage.find_prior_runs("myorg/myrepo", 999)
        assert results == []

    def test_multiple_runs_returned(self, tmp_runs_dir):
        # DB filenames use minute-resolution timestamps, so all 3 rapid calls
        # produce only one .db file (same filename, overwritten). Assert >= 1.
        for _ in range(3):
            db_path, conn = storage.create_run_db("myorg/myrepo", 55)
            conn.close()
        results = storage.find_prior_runs("myorg/myrepo", 55)
        assert len(results) >= 1
        assert results[0]["issue_number"] == 55
