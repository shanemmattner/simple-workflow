"""Runtime for the github_claude engine.

Two backends behind the same `call_agent` signature:

- Claude CLI subscription (`claude -p`) — default. No API key needed.
- MiniMax via scripts/minimax.py — auto-routed when the model resolves to
  a MiniMax name (MiniMax-M3, MiniMax-M2.7-highspeed, m3, m27hs).

Both return the same dict shape: content, tokens_in, tokens_out, cost,
duration_s, finish_reason.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Path to the PA-root minimax.py CLI (parent of repos/simple-workflow/ is the
# PA repo root; scripts/minimax.py lives there). Overridable via env.
_PA_ROOT = Path(__file__).resolve().parents[4]  # runtime.py -> PA root (4 levels up)
_MINIMAX_SCRIPT = Path(
    os.environ.get("MINIMAX_SCRIPT_PATH", _PA_ROOT / "scripts" / "minimax.py")
)

_MINIMAX_MODEL_PREFIXES = ("MiniMax-",)
_MINIMAX_SHORT_ALIASES = {"m3", "m27hs", "minimax", "minimax-m3",
                          "minimax-m2.7-highspeed"}

_THINK_TAG_RE = re.compile(r"\s*<\s*think\s*>.*?<\s*/\s*think\s*>", re.DOTALL | re.IGNORECASE)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds; exponential: 2, 4, 8
_RETRYABLE_PATTERNS = re.compile(
    r"rate limit|429|500|503|overloaded", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent. Focus on the task in your prompt. "
    "Do not delegate work. Work in the directory specified in the prompt."
)


def call_agent(
    prompt: str,
    *,
    model: str,
    cwd: str,
    max_turns: int = 30,
    timeout: int | None = None,
    system_prompt: str | None = None,
) -> dict:
    """Run Claude CLI in non-interactive mode and return a parsed response dict.

    Uses ``claude -p`` (print mode) with ``--dangerously-skip-permissions``
    so the agent can read/write files and run terminal commands in a
    multi-turn tool-use loop without prompting.

    Args:
        max_turns: Retained for API compatibility; the CLI has no
            ``--max-turns`` flag.  Use *timeout* to bound execution.

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
    effective_system_prompt = (
        system_prompt if system_prompt is not None else _DEFAULT_SYSTEM_PROMPT
    )

    if model.lower() == "minimax":
        # The github_claude engine never resolves "minimax" to a concrete
        # model itself — the orchestrator's `_phase_cfg()` owns phase-aware
        # routing via `resolve_auto(phase_name)`. If we get here with the
        # bare wildcard, the caller is the legacy Claude-CLI path that
        # ignores phase context; fall back to the cheap default (m27hs).
        from adapters.minimax import resolve_auto
        model = resolve_auto("search")

    if _is_minimax_model(model):
        return _call_minimax(
            prompt,
            model=model,
            cwd=cwd,
            timeout=timeout,
            system_prompt=effective_system_prompt,
        )

    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format", "json",
        "--model", model,
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

            result = parse_response(proc.stdout, _elapsed=elapsed)
            # Log stderr and empty responses for debugging
            if proc.stderr.strip():
                log.warning("claude stderr: %s", proc.stderr.strip()[:500])
            if result.get("cost", 0) == 0 and result.get("finish_reason") != "error":
                log.warning("zero-cost response (possible auth/CLI issue): content=%s",
                           str(result.get("content", ""))[:300])
            return result

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



# ---------------------------------------------------------------------------
# MiniMax backend (scripts/minimax.py)
# ---------------------------------------------------------------------------

def _is_minimax_model(model: str) -> bool:
    m = model.lower()
    if any(model.startswith(p) for p in _MINIMAX_MODEL_PREFIXES):
        return True
    return m in _MINIMAX_SHORT_ALIASES


def _call_minimax(
    prompt: str,
    *,
    model: str,
    cwd: str,
    timeout: int | None = None,
    system_prompt: str | None = None,
) -> dict:
    """Delegate to scripts/minimax.py and parse its --json output.

    Returns the same dict shape as the Claude CLI path. Think-tag stripping
    happens here because scripts/minimax.py only strips in non-JSON mode.
    """
    effective_timeout = timeout if timeout is not None else 600
    start = time.monotonic()

    # Pre-flight: verify minimax script and cwd exist before launching subprocess.
    # Gives a clear error instead of a generic FileNotFoundError.
    if not _MINIMAX_SCRIPT.exists():
        msg = f"minimax.py not found at {_MINIMAX_SCRIPT} (set MINIMAX_SCRIPT_PATH to override)"
        log.error("_call_minimax: %s", msg)
        return _error_response(msg, 0)
    if cwd and not Path(cwd).exists():
        msg = f"minimax cwd does not exist: {cwd}"
        log.error("_call_minimax: %s", msg)
        return _error_response(msg, 0)

    # Write system prompt to a temp file for --system-file. Empty file is fine.
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            if system_prompt:
                f.write(system_prompt)
            system_file = f.name
    except OSError as exc:
        return _error_response(f"failed to write system prompt: {exc}", 0)

    cmd = [
        sys.executable, str(_MINIMAX_SCRIPT),
        "--json", "--no-stream",
        "--model", model,
        "--system-file", system_file,
        prompt,
    ]

    log.debug("_call_minimax: model=%s cwd=%s prompt_len=%d", model, cwd, len(prompt))

    try:
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
                    log.warning(
                        "_call_minimax returncode=%d stderr=%s",
                        proc.returncode, stderr[:300],
                    )
                    if (
                        attempt < _MAX_RETRIES - 1
                        and _RETRYABLE_PATTERNS.search(stderr)
                    ):
                        delay = _RETRY_BASE_DELAY * (2 ** attempt)
                        log.warning(
                            "minimax retryable error (attempt %d/%d), "
                            "retrying in %.0fs: %s",
                            attempt + 1, _MAX_RETRIES, delay, stderr[:200],
                        )
                        time.sleep(delay)
                        continue
                    return _error_response(stderr or f"minimax.py exited {proc.returncode}", elapsed)

                result = _parse_minimax_response(proc.stdout, _elapsed=elapsed)
                if result.get("finish_reason") == "error":
                    log.warning(
                        "_call_minimax parse error: content=%s",
                        str(result.get("content", ""))[:300],
                    )
                return result

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
            except FileNotFoundError as exc:
                elapsed = time.monotonic() - start
                # subprocess raises FileNotFoundError for both missing executable
                # AND missing cwd — log the actual OS message for clarity.
                log.error("_call_minimax FileNotFoundError: %s", exc)
                return _error_response(f"subprocess launch failed: {exc}", elapsed)
            except Exception as exc:
                elapsed = time.monotonic() - start
                log.error("_call_minimax unexpected error: %s", exc)
                return _error_response(str(exc), elapsed)

        elapsed = time.monotonic() - start
        return _error_response("minimax: all retries exhausted", elapsed)
    finally:
        try:
            os.unlink(system_file)
        except OSError:
            pass


def _parse_minimax_response(raw_json: str, *, _elapsed: float = 0.0) -> dict:
    """Parse minimax.py --json output into the standard response dict."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return _error_response(
            f"malformed JSON from minimax: {str(raw_json)[:300]}", _elapsed
        )

    content = ""
    for choice in data.get("choices", []):
        msg = choice.get("message") or {}
        content += msg.get("content", "") or ""
    # Strip  think blocks — --json mode bypasses MINIMAX_STRIP_THINK_TAGS
    content = _THINK_TAG_RE.sub("", content).strip()

    usage = data.get("usage", {}) or {}
    tokens_in = int(usage.get("prompt_tokens", 0) or 0)
    tokens_out = int(usage.get("completion_tokens", 0) or 0)
    # Cost: scripts/minimax.py writes a [stats] line to stderr; we don't have
    # it here. Use the model table from adapters/minimax.py when available.
    cost = 0.0
    try:
        from adapters.minimax import MODELS as _ADAPTER_MODELS  # type: ignore
        for name, info in _ADAPTER_MODELS.items():
            if name == data.get("model"):
                cost = (
                    tokens_in / 1_000_000 * info["cost_in"]
                    + tokens_out / 1_000_000 * info["cost_out"]
                )
                break
    except Exception:
        pass

    finish_reason = "end_turn"
    if data.get("choices"):
        fr = data["choices"][0].get("finish_reason")
        if fr and fr != "stop":
            finish_reason = fr

    return {
        "content": content,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": cost,
        "duration_s": _elapsed,
        "finish_reason": finish_reason,
    }
