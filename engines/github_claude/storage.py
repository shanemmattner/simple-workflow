"""Per-run SQLite storage for the github_claude engine.

One .db file per run. Filename: <repo>-<issue>-<YYYYMMDD-HHMM>.db
Location: engines/github_claude/runs/ (gitignored).
Self-contained: full replay of entire run from this file alone.
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_RUNS_DIR = Path(__file__).parent / "runs"

_SCHEMA = """\
CREATE TABLE run (
    id                  TEXT PRIMARY KEY,
    issue_url           TEXT,
    repo                TEXT NOT NULL,
    issue_number        INTEGER NOT NULL,
    branch              TEXT,
    status              TEXT NOT NULL DEFAULT 'running',
    model               TEXT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    total_cost          REAL DEFAULT 0,
    total_tokens_in     INTEGER DEFAULT 0,
    total_tokens_out    INTEGER DEFAULT 0,
    review_verdict      TEXT,
    review_summary      TEXT,
    prompt_sha          TEXT
);

CREATE TABLE phase (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL REFERENCES run(id),
    phase_name          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'running',
    model               TEXT,
    started_at          TEXT,
    finished_at         TEXT,
    cost                REAL DEFAULT 0,
    tokens_in           INTEGER DEFAULT 0,
    tokens_out          INTEGER DEFAULT 0,
    failure_category    TEXT
);

CREATE TABLE message (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_id            INTEGER NOT NULL REFERENCES phase(id),
    turn_number         INTEGER NOT NULL,
    role                TEXT NOT NULL,
    content             TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    tokens_in           INTEGER DEFAULT 0,
    tokens_out          INTEGER DEFAULT 0,
    cost                REAL DEFAULT 0
);

CREATE TABLE tool_call (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id          INTEGER NOT NULL REFERENCES message(id),
    phase_id            INTEGER NOT NULL REFERENCES phase(id),
    tool_name           TEXT NOT NULL,
    tool_input          TEXT,
    tool_result         TEXT,
    duration_ms         INTEGER
);

CREATE TABLE event (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL REFERENCES run(id),
    phase_id            INTEGER REFERENCES phase(id),
    event_type          TEXT NOT NULL,
    details             TEXT,
    timestamp           TEXT NOT NULL
);

CREATE INDEX idx_phase_name ON phase(phase_name);
CREATE INDEX idx_message_phase ON message(phase_id);
CREATE INDEX idx_tool_call_phase ON tool_call(phase_id);
CREATE INDEX idx_event_type ON event(event_type);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA page_size = 8192")


def _db_filename(repo: str, issue_number: int) -> str:
    """Build <repo>-<issue>-<YYYYMMDD-HHMM>.db filename."""
    safe_repo = repo.replace("/", "-")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"{safe_repo}-{issue_number}-{ts}.db"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_run_db(
    repo: str,
    issue_number: int,
    *,
    issue_url: str | None = None,
    model: str | None = None,
    prompt_sha: str | None = None,
) -> tuple[str, sqlite3.Connection]:
    """Create a new per-run .db file with the full schema.

    Returns (db_path, connection).  The caller owns the connection.
    """
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    filename = _db_filename(repo, issue_number)
    db_path = str(_RUNS_DIR / filename)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _set_pragmas(conn)
    conn.executescript(_SCHEMA)

    run_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO run (id, repo, issue_number, issue_url, model, started_at, prompt_sha)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (run_id, repo, issue_number, issue_url, model, _now_iso(), prompt_sha),
    )
    conn.commit()
    return db_path, conn


def log_phase(conn: sqlite3.Connection, phase_name: str, **kwargs) -> int:
    """Insert a new phase row. Returns the phase id.

    Accepted kwargs: status, model, started_at, cost, tokens_in, tokens_out,
    failure_category.  started_at defaults to now if omitted.
    """
    run_id = _get_run_id(conn)
    kwargs.setdefault("started_at", _now_iso())

    cols = ["run_id", "phase_name"] + list(kwargs.keys())
    placeholders = ", ".join("?" for _ in cols)
    vals = [run_id, phase_name] + list(kwargs.values())

    cur = conn.execute(
        f"INSERT INTO phase ({', '.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def log_message(
    conn: sqlite3.Connection,
    phase_id: int,
    turn_number: int,
    role: str,
    content: str,
    **kwargs,
) -> int:
    """Insert a message row. Returns the message id.

    Accepted kwargs: tokens_in, tokens_out, cost, timestamp.
    """
    kwargs.setdefault("timestamp", _now_iso())

    cols = ["phase_id", "turn_number", "role", "content"] + list(kwargs.keys())
    placeholders = ", ".join("?" for _ in cols)
    vals = [phase_id, turn_number, role, content] + list(kwargs.values())

    cur = conn.execute(
        f"INSERT INTO message ({', '.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def log_tool_call(
    conn: sqlite3.Connection,
    message_id: int,
    phase_id: int,
    tool_name: str,
    tool_input: str,
    tool_result: str,
    duration_ms: int,
) -> None:
    """Insert a tool_call row."""
    conn.execute(
        """INSERT INTO tool_call (message_id, phase_id, tool_name, tool_input, tool_result, duration_ms)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (message_id, phase_id, tool_name, tool_input, tool_result, duration_ms),
    )
    conn.commit()


def log_event(
    conn: sqlite3.Connection,
    event_type: str,
    details: dict,
    phase_id: int | None = None,
) -> None:
    """Insert an event row. details is serialised to JSON text."""
    run_id = _get_run_id(conn)
    conn.execute(
        """INSERT INTO event (run_id, phase_id, event_type, details, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        (run_id, phase_id, event_type, json.dumps(details), _now_iso()),
    )
    conn.commit()


def finish_phase(conn: sqlite3.Connection, phase_id: int, status: str, **kwargs) -> None:
    """Mark a phase as finished.

    Accepted kwargs: finished_at, cost, tokens_in, tokens_out, failure_category.
    """
    kwargs.setdefault("finished_at", _now_iso())
    sets = ["status = ?"] + [f"{k} = ?" for k in kwargs]
    vals = [status] + list(kwargs.values()) + [phase_id]

    conn.execute(
        f"UPDATE phase SET {', '.join(sets)} WHERE id = ?",
        vals,
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, status: str, **kwargs) -> None:
    """Mark the run as finished.

    Accepted kwargs: finished_at, total_cost, total_tokens_in, total_tokens_out,
    review_verdict, review_summary, branch.
    """
    run_id = _get_run_id(conn)
    kwargs.setdefault("finished_at", _now_iso())
    sets = ["status = ?"] + [f"{k} = ?" for k in kwargs]
    vals = [status] + list(kwargs.values()) + [run_id]

    conn.execute(
        f"UPDATE run SET {', '.join(sets)} WHERE id = ?",
        vals,
    )
    conn.commit()


def find_prior_runs(repo: str, issue_number: int) -> list[dict]:
    """Scan runs/ for .db files matching repo + issue, return summaries.

    Each dict contains the run table row as a plain dict.
    Thread-safe: opens and closes its own connections.
    """
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    safe_repo = repo.replace("/", "-")
    pattern = str(_RUNS_DIR / f"{safe_repo}-{issue_number}-*.db")
    results: list[dict] = []

    for db_path in sorted(glob.glob(pattern)):
        try:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM run LIMIT 1").fetchone()
            if row:
                d = dict(row)
                d["db_path"] = db_path
                results.append(d)
            conn.close()
        except sqlite3.Error:
            continue

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_run_id(conn: sqlite3.Connection) -> str:
    """Return the single run id from this per-run database."""
    row = conn.execute("SELECT id FROM run LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError("No run row found — was create_run_db called?")
    return row[0]
