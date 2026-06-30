"""Claude CLI subscription runtime for three-step pipeline.

Wraps the `claude` CLI with --output-format json, using the Claude Code
subscription (no API credits). Adapted from shftty/workflows/src/agent.py.
"""

import json
import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Subprocess watchdog constants (battle-tested values — do not change)
# ---------------------------------------------------------------------------
_STARTUP_GRACE_S = 300        # Phase 1: max seconds before first assistant token
_STALL_TIMEOUT_S = 120        # Phase 2: max idle stdout seconds post-token
_CONTENT_STALL_S = 240        # Phase 2b: stdout flowing but no assistant content
_RESULT_EXIT_S = 30           # Phase 3: result event seen but process won't exit
_WATCHDOG_POLL_S = 10         # Poll interval for the wait loop


def _run_with_watchdog(
    cmd: list[str],
    *,
    cwd: str,
    env: dict,
    timeout: int,
) -> tuple[str, int, bool]:
    """Run cmd with phase-aware stall detection watchdog.

    Returns (stdout_text, returncode, hit_timeout).
    hit_timeout is True when the process was killed (either by wall-clock
    timeout or by a watchdog phase threshold).
    stdout_text has a sentinel appended if killed by watchdog:
    ``\\n[STALL after <info>]``.
    """
    lines: list[str] = []
    stderr_lines: list[str] = []
    _now = time.monotonic()
    last_stdout_ts: list[float] = [_now]
    last_content_ts: list[float] = [_now]
    first_assistant_token = threading.Event()
    result_received = threading.Event()
    stall_info: list[str | None] = [None]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
        start_new_session=True,
    )

    def _read_stdout() -> None:
        try:
            assert proc.stdout
            for line in proc.stdout:
                ts = time.monotonic()
                lines.append(line)
                last_stdout_ts[0] = ts
                if '"type":"assistant"' in line or '"type":"thinking"' in line:
                    last_content_ts[0] = ts
                    first_assistant_token.set()
                if '"type":"result"' in line:
                    result_received.set()
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

        if elapsed >= timeout:
            _kill(f"phase=wall-clock elapsed={elapsed:.0f}s limit={timeout}s")
            break

        if not first_assistant_token.is_set():
            idle = now - last_stdout_ts[0]
            if idle >= _STARTUP_GRACE_S:
                log.warning(
                    "STALL phase=pre-token idle=%.0fs limit=%ds",
                    idle, _STARTUP_GRACE_S,
                )
                _kill(f"phase=pre-token idle={idle:.0f}s limit={_STARTUP_GRACE_S}s")
                break
        elif result_received.is_set():
            idle = now - last_stdout_ts[0]
            if idle >= _RESULT_EXIT_S:
                log.warning(
                    "STALL phase=post-result idle=%.0fs limit=%ds",
                    idle, _RESULT_EXIT_S,
                )
                _kill(f"phase=post-result idle={idle:.0f}s limit={_RESULT_EXIT_S}s")
                break
        else:
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
    returncode = proc.returncode if proc.returncode is not None else -1
    hit_timeout = stall_info[0] is not None

    if stall_info[0]:
        stdout += f"\n[STALL after {stall_info[0]}]"

    return stdout, returncode, hit_timeout


def parse_json(text: str) -> dict:
    """
    Extract the first valid JSON object from agent output.
    The agent may emit prose + JSON; scan for the last '{...}' block.
    """
    if not text:
        return {}

    stripped = text.strip()

    # Try whole string first
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # Find first JSON object (scan for outermost {...} using brace depth)
    first_brace = stripped.find("{")
    if first_brace != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(first_brace, len(stripped)):
            c = stripped[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(stripped[first_brace:i + 1])
                    except json.JSONDecodeError:
                        break

    # Try stripping markdown fences
    for fence in ("```json", "```"):
        if fence in stripped:
            inner = stripped.split(fence, 1)[1]
            if "```" in inner:
                inner = inner.split("```", 1)[0]
            try:
                return json.loads(inner.strip())
            except json.JSONDecodeError:
                pass

    return {}


def run_phase_agent(
    worktree: str,
    prompt: str,
    phase: str,
    *,
    run_dir: str | None = None,
    timeout: int = 1800,
    max_turns: int = 30,
    model: str = "sonnet",
) -> dict:
    """
    Run a claude agent for a pipeline phase.

    Passes the prompt directly as a positional argument:
      claude "{prompt}" --output-format json --model {model}
        --max-turns {max_turns} --permission-mode bypassPermissions

    Args:
        worktree:  path the agent cwd
        prompt:    fully-rendered prompt string
        phase:     phase name (investigate, implement, review)
        run_dir:   directory to save prompts/ and responses/ artifacts
        timeout:   subprocess timeout in seconds
        max_turns: --max-turns passed to claude CLI
        model:     model shortname (sonnet, haiku, opus)

    Returns dict with keys:
        result, session_id, num_turns, duration_ms, cost_usd,
        tokens_in, tokens_out, cache_read, cache_creation,
        stop_reason, hit_turn_limit, hit_timeout
    """
    # Save prompt artifact
    if run_dir:
        prompts_dir = Path(run_dir) / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        (prompts_dir / f"{phase}.md").write_text(prompt, encoding="utf-8")

    cmd = [
        "claude",
        prompt,
        "--output-format", "json",
        "--model", model,
        "--max-turns", str(max_turns),
        "--permission-mode", "bypassPermissions",
        "--bare",  # skip hooks, LSP, plugins, CLAUDE.md auto-discovery, auto-memory
    ]

    # Hide target repo's CLAUDE.md and .claude/ to prevent them from
    # silently overriding pipeline agent behavior (lesson: shftty's CLAUDE.md
    # tells agents "You NEVER write code", causing execute agents to no-op).
    wt = Path(worktree)
    claude_md = wt / "CLAUDE.md"
    claude_md_hidden = wt / "CLAUDE.md.pipeline-hidden"
    claude_dir = wt / ".claude"
    claude_dir_hidden = wt / ".claude.pipeline-hidden"
    hid_claude_md = False
    hid_claude_dir = False

    if claude_md.exists():
        log.info("Hiding %s → %s", claude_md, claude_md_hidden)
        claude_md.rename(claude_md_hidden)
        hid_claude_md = True
    if claude_dir.exists():
        log.info("Hiding %s → %s", claude_dir, claude_dir_hidden)
        claude_dir.rename(claude_dir_hidden)
        hid_claude_dir = True

    start_ms = time.time() * 1000
    hit_timeout = False
    raw_output = ""
    returncode = -1

    # Layer 1: env vars — Claude's own byte-level and request-level timeouts.
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # prevent nested-session deadlock when running inside Claude Code
    env["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] = "600000"   # 10 min byte-level watchdog
    env["API_TIMEOUT_MS"] = "1200000"                   # 20 min per-request timeout

    try:
        raw_output, returncode, hit_timeout = _run_with_watchdog(
            cmd, cwd=worktree, env=env, timeout=timeout
        )
    except Exception as exc:
        raw_output = str(exc)
    finally:
        # Always restore hidden CLAUDE.md and .claude/
        if hid_claude_md and claude_md_hidden.exists():
            log.info("Restoring %s → %s", claude_md_hidden, claude_md)
            claude_md_hidden.rename(claude_md)
        if hid_claude_dir and claude_dir_hidden.exists():
            log.info("Restoring %s → %s", claude_dir_hidden, claude_dir)
            claude_dir_hidden.rename(claude_dir)

    duration_ms = int(time.time() * 1000 - start_ms)

    # Parse the claude --output-format json envelope
    # Claude emits either a list of events or a single result object
    envelope: dict = {}
    result_text = ""
    payload: list | dict | None = None

    if raw_output.strip():
        try:
            payload = json.loads(raw_output.strip())
            if isinstance(payload, list):
                # Event stream: find last result event
                result_event = next(
                    (e for e in reversed(payload) if e.get("type") == "result"),
                    {}
                )
                envelope = result_event
            elif isinstance(payload, dict):
                envelope = payload
        except json.JSONDecodeError:
            result_text = raw_output[:5000]

    if envelope:
        result_text = envelope.get("result", "")

        # Bug 2 fix: fallback content extraction when result is empty
        # but assistant text exists in the event stream
        if not result_text and isinstance(payload, list):
            text_parts = []
            for event in payload:
                if event.get("type") == "assistant":
                    message = event.get("message", {})
                    for block in message.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block["text"])
            if text_parts:
                result_text = "\n\n".join(text_parts)

        usage = envelope.get("usage", {})
        tokens_in = usage.get("input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_creation = usage.get("cache_creation_input_tokens", 0)
        cost_usd = envelope.get("total_cost_usd") or envelope.get("cost_usd") or 0.0
        session_id = envelope.get("session_id", "")
        num_turns = envelope.get("num_turns", 0)
        stop_reason = envelope.get("stop_reason", "")

        # Bug 1 fix: detect max_turns hit via subtype for mid-tool-call stops
        subtype = envelope.get("subtype", "")
        hit_turn_limit = stop_reason == "max_turns" or subtype == "error_max_turns"

        # Bug 3 fix: detect error condition from is_error flag
        is_error = envelope.get("is_error", False)
        if is_error and stop_reason not in ("error",):
            log.warning("Claude CLI returned is_error=True with stop_reason=%r", stop_reason)
            stop_reason = "error"
    else:
        tokens_in = 0
        tokens_out = 0
        cache_read = 0
        cache_creation = 0
        cost_usd = 0.0
        session_id = ""
        num_turns = 0
        stop_reason = "error" if (returncode != 0 and not hit_timeout) else (
            "timeout" if hit_timeout else "unknown"
        )
        hit_turn_limit = False

    # Save response artifact
    if run_dir:
        responses_dir = Path(run_dir) / "responses"
        responses_dir.mkdir(parents=True, exist_ok=True)
        artifact = {
            "phase": phase,
            "duration_ms": duration_ms,
            "returncode": returncode,
            "envelope": envelope,
            "raw_truncated": raw_output[:5000] if not envelope else None,
        }
        (responses_dir / f"{phase}.json").write_text(
            json.dumps(artifact, indent=2, default=str), encoding="utf-8"
        )

    return {
        "result": result_text,
        "session_id": session_id,
        "num_turns": num_turns,
        "duration_ms": duration_ms,
        "cost_usd": cost_usd,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read": cache_read,
        "cache_creation": cache_creation,
        "stop_reason": stop_reason,
        "hit_turn_limit": hit_turn_limit,
        "hit_timeout": hit_timeout,
    }
