"""Re-export of engine/destination.py — the canonical GitHub PR/push module.

This used to be a separate copy that diverged from engine/destination.py
(it was actually the renamed engines/github_openhands/destination.py,
surviving the deletion of that engine in 7276e6f, so it never picked up the
push_branch fixes — retry-with-force-with-lease-on-rejection, removal of the
fragile rev-parse pre-check — applied to engine/destination.py). Now a thin
shim so there is one source of truth: engine/destination.py.
"""

from __future__ import annotations

from engine.destination import *  # noqa: F401,F403
from engine.destination import (  # noqa: F401
    BranchNotFound,
    PRAlreadyExists,
    PushFailed,
    create_pr,
    format_pr_body,
    push_branch,
)
