"""Re-export of engine/storage.py — the canonical per-run SQLite storage module.

This used to be a separate copy that diverged from engine/storage.py — it
was missing the A-02/A-03 cost-aggregation consistency fix in finish_run()
(recompute total_cost/total_tokens_in/total_tokens_out from the phase table
when the caller doesn't pass them explicitly, so the run row stays
consistent even when audit/extract_lessons phases add rows after the
terminal phase). Now a thin shim so there is one source of truth:
engine/storage.py.
"""

from __future__ import annotations

from engine.storage import *  # noqa: F401,F403
from engine.storage import (  # noqa: F401
    create_run_db,
    finish_phase,
    finish_run,
    find_prior_runs,
    log_event,
    log_message,
    log_phase,
    log_tool_call,
)
