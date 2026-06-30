"""Re-export of engine/workspace.py — the canonical worktree management module.

This used to be a separate copy that diverged from engine/workspace.py — it
was missing neutralize_claude_md() and reuse_or_create_workspace(), both
added later to engine/workspace.py only. Now a thin shim so there is one
source of truth: engine/workspace.py.
"""

from __future__ import annotations

from engine.workspace import *  # noqa: F401,F403
from engine.workspace import (  # noqa: F401
    cleanup_workspace,
    create_workspace,
    get_changed_files,
    get_diff,
    neutralize_claude_md,
    reuse_or_create_workspace,
)
