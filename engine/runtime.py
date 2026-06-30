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
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Path to the PA-root minimax.py CLI (parent of repos/simple-workflow/ is the
# PA repo root; scripts/minimax.py lives there). Overridable via env.
_PA_ROOT = Path(__file__).resolve().parents[3]  # runtime.py -> PA root (3 levels up)
_MINIMAX_SCRIPT = Path(
    os.environ.get("MINIMAX_SCRIPT_PATH", _PA_ROOT / "scripts" / "minimax.py")
)

_MINIMAX_MODEL_PREFIXES = ("MiniMax-",)
_MINIMAX_SHORT_ALIASES = {"m3", "m27hs", "minimax", "minimax-m3",
                          "minimax-m2.7-highspeed"}

_MINIMAX_ANTHROPIC_BASE_URL = "https://api.minimax.io/anthropic"

_THINK_TAG_RE = re.compile(r"\s*<\s*think\s*>.*?<\s*/\s*think\s*>", re.DOTALL | re.IGNORECASE)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds; exponential: 2, 4, 8
_RETRYABLE_PATTERNS = re.compile(
    r"rate limit|429|500|503|overloaded", re.IGNORECASE
)

# Stall retry: how many times to re-run a CLI invocation that the watchdog
# killed for stalling (not the same as the request-level retry above, which
# only fires on a clean process exit with a retryable stderr message).
_STALL_MAX_RETRIES = 2
_STALL_RETRY_DELAYS = (30, 90)  # seconds, exponential-ish backoff
_CLI_HEALTH_TIMEOUT_S = 5

# ---------------------------------------------------------------------------
# Subprocess watchdog constants (battle-tested values — do not change)
# ---------------------------------------------------------------------------
_STARTUP_GRACE_S = 300        # Phase 1: max seconds before first assistant token
_STALL_TIMEOUT_S = 120        # Phase 2: max idle stdout seconds post-token
_CONTENT_STALL_S = 240        # Phase 2b: stdout flowing but no assistant content
_RESULT_EXIT_S = 30           # Phase 3: result event seen but process won't exit
_WATCHDOG_POLL_S = 10         # Poll interval for the wait loop


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent. Focus on the task in your prompt. "
    "Do not delegate work. Work in the directory specified in the prompt."
)


def _check_cli_health() -> tuple[bool, str]:
    """Run `claude --version` with a short timeout to confirm the CLI is alive.

    Call this once before the first phase of a pipeline run so a dead/hung
    CLI fails fast (≤5s) instead of burning a full watchdog cycle (≤300s)
    on the first real call.

    Returns (healthy, detail) where detail is the version string on success
    or an error description on failure.
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=_CLI_HEALTH_TIMEOUT_S,
        )
        elapsed = time.monotonic() - start
        if proc.returncode == 0:
            detail = proc.stdout.strip() or "ok"
            log.info("CLI health check passed in %.2fs: %s", elapsed, detail)
            return True, detail
        detail = f"exit={proc.returncode} stderr={proc.stderr.strip()[:200]}"
        log.error("CLI health check failed in %.2fs: %s", elapsed, detail)
        return False, detail
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        detail = f"timed out after {elapsed:.1f}s (limit={_CLI_HEALTH_TIMEOUT_S}s)"
        log.error("CLI health check timed out: %s", detail)
        return False, detail
    except FileNotFoundError:
        log.error("CLI health check failed: claude not found in PATH")
        return False, "claude CLI not found in PATH"
    except Exception as exc:
        elapsed = time.monotonic() - start
        detail = f"unexpected error after {elapsed:.2f}s: {exc}"
        log.error("CLI health check failed: %s", detail)
        return False, detail


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
        max_turns: Forwarded to the CLI as ``--max-turns N`` — a circuit
            breaker that caps how many agentic tool-use turns a single
            invocation may take before the CLI itself stops it.

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
        # Resolve short aliases to full model name before dispatching.
        resolved_model = _resolve_minimax_model(model)
        api_key = _get_minimax_api_key()
        if api_key:
            # Route MiniMax through claude -p with Anthropic-compat endpoint.
            # This gives full tool use (file read/write, bash) — same as native Claude.
            log.debug(
                "_call_agent: MiniMax via claude -p (ANTHROPIC_BASE_URL=%s model=%s)",
                _MINIMAX_ANTHROPIC_BASE_URL, resolved_model,
            )
            return _call_minimax_via_claude_cli(
                prompt,
                model=resolved_model,
                api_key=api_key,
                cwd=cwd,
                max_turns=max_turns,
                timeout=timeout,
                system_prompt=effective_system_prompt,
            )
        else:
            # MINIMAX_API_KEY missing: the scripts/minimax.py text-only fallback
            # ALSO requires this key (and the script itself isn't shipped — see
            # _MINIMAX_SCRIPT), so it cannot rescue this call. Fall back to
            # sonnet with full tool use rather than crashing the phase outright.
            log.warning(
                "call_agent: MINIMAX_API_KEY not set — model %r requires it and "
                "has no working fallback. Falling back to sonnet for this call. "
                "Set MINIMAX_API_KEY to enable MiniMax routing.",
                model,
            )
            model = "sonnet"

    cmd = [
        "claude",
        "-p",
        "--output-format", "stream-json",
        "--model", model,
        "--max-turns", str(max_turns),
        "--dangerously-skip-permissions",
        "--system-prompt", effective_system_prompt,
        "--no-session-persistence",
        "--strict-mcp-config",  # no MCP servers — pipeline doesn't need them
    ]

    effective_timeout = timeout if timeout is not None else 600
    start = time.monotonic()

    for attempt in range(_MAX_RETRIES):
        try:
            returncode, stdout, stderr, stall_info = _run_claude_with_watchdog_retry(
                cmd, cwd or None, effective_timeout, stdin_text=prompt
            )
            elapsed = time.monotonic() - start

            if stall_info:
                # Stall retries (with backoff) already exhausted inside
                # _run_claude_with_watchdog_retry — return as timeout, no
                # further request-level retry.
                log.warning(
                    "call_agent: subprocess killed by watchdog after stall "
                    "retries exhausted: %s", stall_info
                )
                return {
                    "content": stdout,  # partial output with sentinel appended
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost": 0.0,
                    "duration_s": elapsed,
                    "finish_reason": "timeout",
                }

            if returncode != 0:
                stderr_str = stderr.strip()
                if (
                    attempt < _MAX_RETRIES - 1
                    and _RETRYABLE_PATTERNS.search(stderr_str)
                ):
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning(
                        "call_agent retryable error (attempt %d/%d), "
                        "retrying in %.0fs: %s",
                        attempt + 1, _MAX_RETRIES, delay, stderr_str[:200],
                    )
                    time.sleep(delay)
                    continue

                return _error_response(stderr_str, elapsed)

            result = parse_response(stdout, _elapsed=elapsed)
            # Log stderr and empty responses for debugging
            if stderr.strip():
                log.warning("claude stderr: %s", stderr.strip()[:500])
            if result.get("cost", 0) == 0 and result.get("finish_reason") != "error":
                log.warning("zero-cost response (possible auth/CLI issue): content=%s",
                           str(result.get("content", ""))[:300])
            return result

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
    """Parse Claude CLI output into a flat response dict.

    Handles both JSON (single array) and stream-json (NDJSON) formats.
    """
    if not raw_json or not raw_json.strip():
        return _error_response("empty CLI output", _elapsed)

    # Try parsing as single JSON first (json format)
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        # Try NDJSON (stream-json format): one JSON object per line
        events = []
        for line in raw_json.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
        if not events:
            return _error_response(
                f"malformed JSON from CLI: {str(raw_json)[:300]}", _elapsed
            )
        data = events

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

def _log_retry_event(line: str) -> None:
    """Parse a single stream-json line and log it if it's an api_retry event.

    The CLI emits ``{"type":"system","subtype":"api_retry",...}`` when it
    internally retries a request (rate limit, transient 5xx, etc). These are
    invisible unless we parse stream-json — surface them so retries show up
    in the run log instead of looking like silent idle time.
    """
    if '"subtype":"api_retry"' not in line and '"subtype": "api_retry"' not in line:
        return
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        log.info("api_retry event (unparsed): %s", line.strip()[:300])
        return
    attempt = event.get("attempt", event.get("retry_count", "?"))
    error = event.get("error", event.get("error_type", "?"))
    delay = event.get("delay_ms", event.get("delay", "?"))
    log.warning(
        "api_retry: attempt=%s error=%s delay=%s", attempt, error, delay
    )


def _run_claude_with_watchdog_retry(
    cmd: list[str],
    cwd: str | None,
    timeout: int,
    stdin_text: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str, str | None]:
    """Wrap _run_claude_with_watchdog with stall-retry + exponential backoff.

    A "stall" (watchdog killed the process for going idle) is distinct from
    a clean process exit with a retryable stderr message — that's handled
    by the caller's own _MAX_RETRIES loop. A stall usually means the CLI
    itself hung (not an API-level error), so we retry the whole invocation
    up to _STALL_MAX_RETRIES times with backoff, running a quick health
    check first to fail fast if the CLI binary itself is broken.

    Returns the same (returncode, stdout, stderr, stall_info) tuple as
    _run_claude_with_watchdog. stall_info is non-None only once all
    retries are exhausted.
    """
    last_result: tuple[int, str, str, str | None] | None = None

    for stall_attempt in range(_STALL_MAX_RETRIES + 1):
        result = _run_claude_with_watchdog(
            cmd, cwd, timeout, stdin_text=stdin_text, extra_env=extra_env
        )
        returncode, stdout, stderr, stall_info = result
        last_result = result

        if not stall_info:
            return result

        log.warning(
            "stall detected (retry %d/%d): %s",
            stall_attempt, _STALL_MAX_RETRIES, stall_info,
        )

        if stall_attempt >= _STALL_MAX_RETRIES:
            log.error(
                "stall retries exhausted (%d attempts) — giving up: %s",
                _STALL_MAX_RETRIES, stall_info,
            )
            break

        healthy, detail = _check_cli_health()
        if not healthy:
            log.error(
                "CLI health check failed after stall — not retrying "
                "(CLI appears broken): %s", detail,
            )
            break

        delay = _STALL_RETRY_DELAYS[min(stall_attempt, len(_STALL_RETRY_DELAYS) - 1)]
        log.warning(
            "CLI healthy (%s) — retrying stalled call in %ds "
            "(attempt %d/%d)",
            detail, delay, stall_attempt + 1, _STALL_MAX_RETRIES,
        )
        time.sleep(delay)

    return last_result


def _run_claude_with_watchdog(
    cmd: list[str],
    cwd: str | None,
    timeout: int,
    stdin_text: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str, str | None]:
    """Run a claude -p subprocess with three-phase stall detection.

    Layer 1 env vars bake Claude's own byte-level idle timeout into the env.
    Layer 3 watchdog thread monitors stdout events and kills on phase-specific
    thresholds, handling the case where Claude's self-timeout fails.

    Args:
        extra_env: Optional additional env vars to set (or override) in the
            subprocess environment. Used by MiniMax routing to inject
            ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY without modifying the
            calling process's environment.

    Returns:
        (returncode, stdout, stderr, stall_info)
        stall_info is None on clean exit, or a description string when killed
        by the watchdog. stdout will have a sentinel appended in that case:
        ``\\n[STALL after <info>]``.
    """
    # Layer 1: env vars — Claude's own byte-level and request-level timeouts.
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # prevent nested-session deadlock when running inside Claude Code
    env["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = "600000"   # 10 min byte-level watchdog
    env["API_TIMEOUT_MS"] = "1200000"                   # 20 min per-request timeout
    if extra_env:
        env.update(extra_env)

    lines: list[str] = []
    stderr_lines: list[str] = []
    _now = time.monotonic()
    last_stdout_ts: list[float] = [_now]
    last_content_ts: list[float] = [_now]
    first_assistant_token = threading.Event()
    result_received = threading.Event()
    stall_info: list[str | None] = [None]

    # start_new_session=True creates a new process group so os.killpg can
    # kill claude AND any child processes it spawns.
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if stdin_text else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
        start_new_session=True,
    )

    if stdin_text:
        proc.stdin.write(stdin_text)
        proc.stdin.close()

    def _read_stdout() -> None:
        try:
            assert proc.stdout
            for line in proc.stdout:
                ts = time.monotonic()
                lines.append(line)
                last_stdout_ts[0] = ts
                # Detect content events to distinguish real-work from preamble.
                # assistant/thinking/user(tool-result) events all indicate the
                # CLI is actively producing turns — any of them resets the
                # content-stall idle timer for the watchdog.
                if (
                    '"type":"assistant"' in line
                    or '"type":"thinking"' in line
                    or '"type":"user"' in line
                ):
                    last_content_ts[0] = ts
                    first_assistant_token.set()
                if '"type":"result"' in line:
                    result_received.set()
                _log_retry_event(line)
        except Exception:
            pass

    def _read_stderr() -> None:
        try:
            assert proc.stderr
            for line in proc.stderr:
                stderr_lines.append(line)
        except Exception:
            pass

    t_out = threading.Thread(target=_read_stdout, daemon=True)
    t_err = threading.Thread(target=_read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    def _kill(reason: str) -> None:
        stall_info[0] = reason
        log.warning("STALL %s — killing pid=%d", reason, proc.pid)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.kill()
        except OSError:
            pass

    wall_start = time.monotonic()

    while True:
        if proc.poll() is not None:
            break

        now = time.monotonic()
        elapsed = now - wall_start

        # Hard wall-clock cap (matches effective_timeout from call_agent)
        if elapsed >= timeout:
            _kill(f"phase=wall-clock elapsed={elapsed:.0f}s limit={timeout}s")
            break

        if not first_assistant_token.is_set():
            # Phase 1: waiting for first assistant/thinking event
            idle = now - last_stdout_ts[0]
            if idle >= _STARTUP_GRACE_S:
                log.warning(
                    "STALL phase=pre-token idle=%.0fs limit=%ds",
                    idle, _STARTUP_GRACE_S,
                )
                _kill(f"phase=pre-token idle={idle:.0f}s limit={_STARTUP_GRACE_S}s")
                break
        elif result_received.is_set():
            # Phase 3: result event emitted — process should exit promptly
            idle = now - last_stdout_ts[0]
            if idle >= _RESULT_EXIT_S:
                log.warning(
                    "STALL phase=post-result idle=%.0fs limit=%ds",
                    idle, _RESULT_EXIT_S,
                )
                _kill(f"phase=post-result idle={idle:.0f}s limit={_RESULT_EXIT_S}s")
                break
        else:
            # Phase 2/2b: token received, result not yet — check two stall axes
            idle_stdout = now - last_stdout_ts[0]
            idle_content = now - last_content_ts[0]
            if idle_stdout >= _STALL_TIMEOUT_S:
                log.warning(
                    "STALL phase=post-token idle_stdout=%.0fs limit=%ds",
                    idle_stdout, _STALL_TIMEOUT_S,
                )
                _kill(
                    f"phase=post-token idle_stdout={idle_stdout:.0f}s"
                    f" limit={_STALL_TIMEOUT_S}s"
                )
                break
            elif idle_content >= _CONTENT_STALL_S:
                log.warning(
                    "STALL phase=content-stall idle_content=%.0fs limit=%ds",
                    idle_content, _CONTENT_STALL_S,
                )
                _kill(
                    f"phase=content-stall idle_content={idle_content:.0f}s"
                    f" limit={_CONTENT_STALL_S}s"
                )
                break

        time.sleep(_WATCHDOG_POLL_S)

    t_out.join(timeout=5)
    t_err.join(timeout=5)

    stdout = "".join(lines)
    stderr = "".join(stderr_lines)
    returncode = proc.returncode if proc.returncode is not None else -1

    if stall_info[0]:
        stdout += f"\n[STALL after {stall_info[0]}]"

    return returncode, stdout, stderr, stall_info[0]


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
# MiniMax backend
#
# Primary path: claude -p with ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic
#   → full tool use (Bash, Read, Write, etc.), same as native Claude
#   → requires MINIMAX_API_KEY env var
#
# Fallback path: scripts/minimax.py (text-only, no tool use)
#   → used when MINIMAX_API_KEY is not in env
# ---------------------------------------------------------------------------

_RC_VAR_RE = re.compile(
    r"""^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*['"]?([^'"\s#]+)['"]?\s*(?:#.*)?$""",
    re.MULTILINE,
)

_MINIMAX_SHORT_TO_FULL: dict[str, str] = {
    "m3": "MiniMax-M3",
    "minimax": "MiniMax-M3",
    "minimax-m3": "MiniMax-M3",
    "m27hs": "MiniMax-M2.7-highspeed",
    "minimax-m2.7-highspeed": "MiniMax-M2.7-highspeed",
}


def _resolve_minimax_model(model: str) -> str:
    """Map short aliases (m3, m27hs, minimax) to full MiniMax model IDs."""
    return _MINIMAX_SHORT_TO_FULL.get(model.lower(), model)


def _get_minimax_api_key() -> str | None:
    """Return MINIMAX_API_KEY from env, with ~/.zshrc fallback."""
    key = os.environ.get("MINIMAX_API_KEY")
    if key:
        return key
    # Fallback: parse ~/.zshrc for uncommented export MINIMAX_API_KEY=...
    rcfile = Path.home() / ".zshrc"
    if rcfile.is_file():
        try:
            text = rcfile.read_text()
            for m in _RC_VAR_RE.finditer(text):
                if m.group(1) == "MINIMAX_API_KEY":
                    return m.group(2)
        except OSError:
            pass
    return None


def _call_minimax_via_claude_cli(
    prompt: str,
    *,
    model: str,
    api_key: str,
    cwd: str,
    max_turns: int = 30,
    timeout: int | None = None,
    system_prompt: str | None = None,
) -> dict:
    """Route MiniMax through claude -p using Anthropic-compat endpoint.

    Sets ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY env overrides so the Claude
    CLI hits api.minimax.io/anthropic instead of Anthropic's servers. The
    model name (MiniMax-M3, MiniMax-M2.7-highspeed) is passed as-is — MiniMax
    accepts these names on their Anthropic-compat endpoint.

    Returns the same dict shape as call_agent().
    """
    effective_timeout = timeout if timeout is not None else 600
    effective_system = system_prompt or _DEFAULT_SYSTEM_PROMPT

    cmd = [
        "claude",
        "-p",
        "--output-format", "stream-json",
        "--model", model,
        "--max-turns", str(max_turns),
        "--dangerously-skip-permissions",
        "--system-prompt", effective_system,
        "--no-session-persistence",
        "--strict-mcp-config",
    ]

    start = time.monotonic()

    for attempt in range(_MAX_RETRIES):
        try:
            returncode, stdout, stderr, stall_info = _run_claude_with_watchdog_retry(
                cmd, cwd or None, effective_timeout,
                stdin_text=prompt,
                extra_env={
                    "ANTHROPIC_BASE_URL": _MINIMAX_ANTHROPIC_BASE_URL,
                    "ANTHROPIC_API_KEY": api_key,
                },
            )
            elapsed = time.monotonic() - start

            if stall_info:
                log.warning(
                    "_call_minimax_via_claude_cli: watchdog killed after stall "
                    "retries exhausted: %s", stall_info
                )
                return {
                    "content": stdout,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost": 0.0,
                    "duration_s": elapsed,
                    "finish_reason": "timeout",
                }

            if returncode != 0:
                stderr_str = stderr.strip()
                if (
                    attempt < _MAX_RETRIES - 1
                    and _RETRYABLE_PATTERNS.search(stderr_str)
                ):
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning(
                        "_call_minimax_via_claude_cli retryable error "
                        "(attempt %d/%d), retrying in %.0fs: %s",
                        attempt + 1, _MAX_RETRIES, delay, stderr_str[:200],
                    )
                    time.sleep(delay)
                    continue
                return _error_response(stderr_str, elapsed)

            result = parse_response(stdout, _elapsed=elapsed)
            if stderr.strip():
                log.warning("minimax claude-cli stderr: %s", stderr.strip()[:500])
            if result.get("cost", 0) == 0 and result.get("finish_reason") != "error":
                log.warning(
                    "_call_minimax_via_claude_cli: zero-cost response "
                    "(possible auth/endpoint issue): content=%s",
                    str(result.get("content", ""))[:300],
                )
            return result

        except FileNotFoundError:
            elapsed = time.monotonic() - start
            return _error_response("claude CLI not found in PATH", elapsed)
        except Exception as exc:
            elapsed = time.monotonic() - start
            return _error_response(str(exc), elapsed)

    elapsed = time.monotonic() - start
    return _error_response("_call_minimax_via_claude_cli: all retries exhausted", elapsed)


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
