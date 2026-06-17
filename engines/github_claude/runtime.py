"""Claude CLI runtime — subprocess wrapper for subscription-mode calls.

Runs `claude` CLI with --output-format json, parses the response envelope,
and returns plain dicts.  No API key needed (subscription only).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time

log = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds; exponential: 2, 4, 8
_RETRYABLE_PATTERNS = re.compile(
    r"rate limit|429|500|503|overloaded", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def call_agent(
    prompt: str,
    *,
    model: str,
    cwd: str,
    max_turns: int = 30,
    timeout: int | None = None,
) -> dict:
    """Run Claude CLI and return a parsed response dict.

    Returns a dict with keys:
        content       - text body of the assistant response
        tokens_in     - total input tokens (incl. cache read/creation)
        tokens_out    - output tokens
        cost          - total cost in USD (float)
        duration_s    - wall-clock seconds
        finish_reason - "end_turn" | "timeout" | "error"

    Never raises on CLI / parse failures — returns a dict with
    finish_reason="error" and content set to the error description.
    """
    cmd = [
        "claude",
        prompt,
        "--output-format", "json",
        "--model", model,
        "--permission-mode", "auto",
        "--max-turns", str(max_turns),
    ]

    effective_timeout = timeout if timeout is not None else 600
    start = time.monotonic()

    for attempt in range(_MAX_RETRIES):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                cwd=cwd or None,
            )
            elapsed = time.monotonic() - start

            if proc.returncode != 0:
                stderr = proc.stderr.strip()
                if (
                    attempt < _MAX_RETRIES - 1
                    and _RETRYABLE_PATTERNS.search(stderr)
                ):
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning(
                        "call_agent retryable error (attempt %d/%d), "
                        "retrying in %.0fs: %s",
                        attempt + 1, _MAX_RETRIES, delay, stderr[:200],
                    )
                    time.sleep(delay)
                    continue

                return _error_response(stderr, elapsed)

            return parse_response(proc.stdout, _elapsed=elapsed)

        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            return {
                "content": "",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": 0.0,
                "duration_s": elapsed,
                "finish_reason": "timeout",
            }

        except FileNotFoundError:
            elapsed = time.monotonic() - start
            return _error_response(
                "claude CLI not found in PATH", elapsed
            )

        except Exception as exc:
            elapsed = time.monotonic() - start
            return _error_response(str(exc), elapsed)

    # All retries exhausted (defensive — loop normally returns earlier).
    elapsed = time.monotonic() - start
    return _error_response("all retries exhausted", elapsed)


def parse_response(raw_json: str, *, _elapsed: float = 0.0) -> dict:
    """Parse Claude CLI JSON output into a flat response dict.

    The CLI emits either a single JSON object or a JSON array of events.
    When it's an array, the last event with ``"type": "result"`` is the
    response envelope.

    Returns the same dict shape as ``call_agent``.
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return _error_response(
            f"malformed JSON from CLI: {str(raw_json)[:300]}", _elapsed
        )

    # Array of streaming events — find the result envelope.
    if isinstance(data, list):
        result_event = None
        for event in reversed(data):
            if isinstance(event, dict) and event.get("type") == "result":
                result_event = event
                break
        if result_event is None:
            return _error_response(
                f"no result event in CLI output ({len(data)} events)",
                _elapsed,
            )
        data = result_event

    # Application-level error from Claude.
    if data.get("is_error"):
        return _error_response(data.get("result", ""), _elapsed)

    usage = data.get("usage", {})
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_creation = usage.get("cache_creation_input_tokens", 0)
    tokens_in = (
        usage.get("input_tokens", 0) + cache_read + cache_creation
    )
    tokens_out = usage.get("output_tokens", 0)
    cost = data.get("total_cost_usd", 0.0)
    content = data.get("result", "")

    return {
        "content": content,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": cost,
        "duration_s": _elapsed,
        "finish_reason": "end_turn",
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _error_response(message: str, elapsed: float) -> dict:
    return {
        "content": message,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": 0.0,
        "duration_s": elapsed,
        "finish_reason": "error",
    }
