"""SQLite schema and CRUD for simple_workflow pipeline runs."""

from __future__ import annotations

import sqlite3
import time
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id       TEXT PRIMARY KEY,
    issue        TEXT NOT NULL,
    workflow     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'running',
    model        TEXT,
    started_at   REAL NOT NULL,
    finished_at  REAL,
    error        TEXT,
    config_json  TEXT,
    budget_usd   REAL,
    spent_usd    REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS phase_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    phase            TEXT NOT NULL,
    model            TEXT,
    duration_s       REAL,
    tokens_in        INTEGER,
    tokens_out       INTEGER,
    cost_usd         REAL,
    num_turns        INTEGER,
    stop_reason      TEXT,
    hit_turn_limit   INTEGER DEFAULT 0,
    hit_timeout      INTEGER DEFAULT 0,
    prompt_hash      TEXT,
    output_hash      TEXT,
    output_path      TEXT,
    parse_success    INTEGER DEFAULT 1,
    validation_errors TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    phase           TEXT NOT NULL,
    reviewer_model  TEXT NOT NULL,
    score           REAL,
    verdict         TEXT,
    findings_json   TEXT NOT NULL,
    created_at      REAL NOT NULL
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def create_run(
    conn: sqlite3.Connection,
    run_id: str,
    issue: str,
    workflow: str,
    model: str,
    config_json: str,
    budget_usd: float,
) -> None:
    conn.execute(
        """INSERT INTO pipeline_runs
           (run_id, issue, workflow, model, started_at, config_json, budget_usd)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, issue, workflow, model, time.time(), config_json, budget_usd),
    )
    conn.commit()


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    error: str | None = None,
) -> None:
    conn.execute(
        """UPDATE pipeline_runs
           SET status = ?, finished_at = ?, error = ?
           WHERE run_id = ?""",
        (status, time.time(), error, run_id),
    )
    conn.commit()


def update_spent(conn: sqlite3.Connection, run_id: str, spent_usd: float) -> None:
    conn.execute(
        "UPDATE pipeline_runs SET spent_usd = ? WHERE run_id = ?",
        (spent_usd, run_id),
    )
    conn.commit()


def log_phase(conn: sqlite3.Connection, run_id: str, phase: str, **kwargs: Any) -> int:
    cols = ["run_id", "phase"] + list(kwargs.keys())
    placeholders = ", ".join("?" for _ in cols)
    vals = [run_id, phase] + list(kwargs.values())
    cur = conn.execute(
        f"INSERT INTO phase_logs ({', '.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def add_review(
    conn: sqlite3.Connection,
    run_id: str,
    phase: str,
    reviewer_model: str,
    score: float,
    verdict: str,
    findings_json: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO reviews
           (run_id, phase, reviewer_model, score, verdict, findings_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, phase, reviewer_model, score, verdict, findings_json, time.time()),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    return dict(row) if row else None


def get_phase_logs(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM phase_logs WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    return [dict(r) for r in rows]
