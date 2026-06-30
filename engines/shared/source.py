"""Re-export of engine/source.py — the canonical GitHub issue read/write module.

This used to be a separate copy that diverged from engine/source.py (it was
actually the renamed engines/github_openhands/source.py, surviving the
deletion of that engine in 7276e6f). Now a thin shim so there is one source
of truth: engine/source.py.
"""

from __future__ import annotations

from engine.source import *  # noqa: F401,F403
from engine.source import (  # noqa: F401
    GitHubCLIError,
    create_issue,
    fetch_issue,
    find_open_prs_for_issue,
    post_comment,
    update_labels,
)
