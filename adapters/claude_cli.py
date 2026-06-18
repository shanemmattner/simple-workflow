"""Claude CLI adapter — wraps `claude -p` subprocess, not an API."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time

log = logging.getLogger(__name__)

AVAILABLE_MODELS: set[str] = {"sonnet", "opus", "haiku"}

_SHORT_NAMES: dict[str, str] = {
    "claude": "sonnet",
    "claude-sonnet": "sonnet",
    "claude-opus": "opus",
    "claude-haiku": "haiku",
}

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2
_RETRYABLE_PATTERNS = re.compile(
    r"rate limit|429|500|503|overloaded", re.IGNORECASE
)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent. Focus on the task in your prompt. "
    "Do not delegate work. Work in the directory specified in the prompt."
)


def resolve_model(model: str) -> str:
    """Map short names to CLI model names."""
    return _SHORT_NAMES.get(model, model)


def validate_model(model: str) -> None:
    """Raise ValueError if *model* is not a known Claude CLI model."""
    resolved = resolve_model(model)
    if resolved not in AVAILABLE_MODELS:
        raise ValueError(
            f"Model {model!r} (resolved: {resolved!r}) not available via Claude CLI. "
            f"Available: {sorted(AVAILABLE_MODELS)}"
        )


def get_config(model: str = "sonnet") -> dict:
    """Return config dict — different shape since this is a CLI, not an API.

    Callers should check config["type"] == "cli" to distinguish from API adapters.
    """
    resolved = resolve_model(model)
    validate_model(resolved)
    return {
        "type": "cli",
        "command": "claude",
        "model": resolved,
        "flags": ["-p", "--dangerously-skip-permissions"],
    }


def call(
    prompt: str,
    *,
    model: str = "sonnet",
    cwd: str | None = None,
    timeout: int | None = None,
    system_prompt: str | None = None,
) -> dict:
    """Run ``claude -p`` and return a standard response dict.

    Returns dict with keys: content, tokens_in, tokens_out, cost, duration_s,
    finish_reason.  Never raises on CLI failures.
    """
    resolved = resolve_model(model)
    validate_model(resolved)

    effective_system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format", "json",
        "--model", resolved,
        "--dangerously-skip-permissions",
        "--system-prompt", effective_system_prompt,
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
                if attempt < _MAX_RETRIES - 1 and _RETRYABLE_PATTERNS.search(stderr):
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning(
                        "claude CLI retryable error (attempt %d/%d), "
                        "retrying in %.0fs: %s",
                        attempt + 1, _MAX_RETRIES, delay, stderr[:200],
                    )
                    time.sleep(delay)
                    continue
                return _error_response(stderr, elapsed)

            result = _parse_response(proc.stdout, elapsed)
            if proc.stderr.strip():
                log.warning("claude stderr: %s", proc.stderr.strip()[:500])
            if result.get("cost", 0) == 0 and result.get("finish_reason") != "error":
                log.warning(
                    "zero-cost response (possible auth/CLI issue): content=%s",
                    str(result.get("content", ""))[:300],
                )
            return result

        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            return _error_response("timeout", elapsed, finish_reason="timeout")

        except FileNotFoundError:
            elapsed = time.monotonic() - start
            return _error_response("claude CLI not found in PATH", elapsed)

        except Exception as exc:
            elapsed = time.monotonic() - start
            return _error_response(str(exc), elapsed)

    elapsed = time.monotonic() - start
    return _error_response("all retries exhausted", elapsed)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_response(raw_json: str, elapsed: float) -> dict:
    """Parse Claude CLI JSON output into a flat response dict."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return _error_response(
            f"malformed JSON from CLI: {str(raw_json)[:300]}", elapsed
        )

    if isinstance(data, list):
        result_event = None
        for event in reversed(data):
            if isinstance(event, dict) and event.get("type") == "result":
                result_event = event
                break
        if result_event is None:
            return _error_response(
                f"no result event in CLI output ({len(data)} events)", elapsed
            )
        data = result_event

    if data.get("is_error"):
        return _error_response(data.get("result", ""), elapsed)

    usage = data.get("usage", {})
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_creation = usage.get("cache_creation_input_tokens", 0)
    tokens_in = usage.get("input_tokens", 0) + cache_read + cache_creation
    tokens_out = usage.get("output_tokens", 0)
    cost = data.get("total_cost_usd", 0.0)
    content = data.get("result", "")

    return {
        "content": content,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": cost,
        "duration_s": elapsed,
        "finish_reason": "end_turn",
    }


def _error_response(
    message: str, elapsed: float, *, finish_reason: str = "error"
) -> dict:
    return {
        "content": message,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": 0.0,
        "duration_s": elapsed,
        "finish_reason": finish_reason,
    }
