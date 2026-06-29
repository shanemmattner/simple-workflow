"""Direct OpenAI SDK runtime for MiniMax's OpenAI-compatible endpoint.

Bypasses OpenHands SDK entirely — just a simple tool-calling agent loop
using the openai Python package against https://api.minimax.io/v1.

Copied from engines/three_step/runtime.py (Z.ai) and re-flavored for MiniMax
M3 / M2.7-highspeed. Tool names follow the Claude CLI convention
(Read/Edit/Write/Bash/Glob/Grep) so prompts written for the Claude engine
work unchanged.

Requirements:
    pip install openai
    Python >= 3.12

Env:
    MINIMAX_API_KEY   (required, falls back to ~/.zshrc)
    MINIMAX_BASE_URL  (default https://api.minimax.io/v1)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent working in a repository. You have tools to run "
    "commands, read files, write files, edit files, glob for paths, and grep "
    "for patterns. Make progress every turn; read only what you need, then act."
)

# Endpoint fix: the second research report (2026-06-29-minimax-prompt-templates)
# documented that the typo'd `api.minimaxi.chat/v1` host matches NO official
# MiniMax endpoint. The canonical global OpenAI-compat endpoint is
# `api.minimax.io/v1`. adapters/minimax.py and scripts/minimax.py still
# default to the typo'd host — DO NOT mirror that bug. This engine is the
# canonical MiniMax-flavored loop in the pipeline; correctness starts here.
_DEFAULT_BASE_URL = "https://api.minimax.io/v1"
_DEFAULT_MODEL = "MiniMax-M3"


def _lookup_rc_env(var_names: list[str]) -> str | None:
    """Return the first matching var value from ~/.zshrc, or None.

    Mirrors scripts/minimax.py lines 67-86 — some users store API keys in
    ~/.zshrc rather than exporting them in their shell init. We parse that
    file as a fallback when the env var is unset.

    Hardening (ENG-04): shell command-substitution markers (`$`, backticks,
    parens) are rejected outright. See adapters/minimax.py for the full
    rationale; the two regexes are intentionally identical.
    """
    rcfile = Path.home() / ".zshrc"
    if not rcfile.is_file():
        return None
    try:
        text = rcfile.read_text()
    except OSError:
        return None
    # Match `export NAME=...` or bare `NAME=...` in a shell rcfile. Captures
    # the name in group 1 and the value in group 2 (strips surrounding quotes).
    # Value class explicitly excludes `$`, backticks, and parens.
    _RC_VAR_RE = re.compile(
        r"""^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*['"]?([^'"\s$`()#]+)['"]?\s*(?:#.*)?$""",
        re.MULTILINE,
    )
    matches: dict[str, str] = {}
    for m in _RC_VAR_RE.finditer(text):
        name, value = m.group(1), m.group(2)
        if any(ch in value for ch in ("$", "`", "(", ")")):
            continue
        matches[name] = value
    for name in var_names:
        if name in matches:
            return matches[name]
    return None


def _resolve_api_key() -> str | None:
    """Return the MiniMax API key from env, with ~/.zshrc fallback."""
    key = os.environ.get("MINIMAX_API_KEY")
    if key:
        return key
    return _lookup_rc_env(["MINIMAX_API_KEY"])


def _price_for_model(model: str) -> tuple[float, float]:
    """Look up (price_in, price_out) for a MiniMax model from the adapter.

    Falls back to the M3 standard-tier default if the adapter is unavailable
    or the model isn't in the table.
    """
    try:
        from adapters.minimax import MODELS, resolve_model
    except ImportError:
        return (0.30, 1.20)

    resolved = resolve_model(model) if model else "MiniMax-M3"
    info = MODELS.get(resolved)
    if info:
        return (float(info.get("cost_in", 0.30)), float(info.get("cost_out", 1.20)))
    return (0.30, 1.20)  # M3 default


# Pricing per million tokens (USD) — overwritten per call from the adapter.
_PRICE_IN = 0.30
_PRICE_OUT = 1.20

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format) — Claude-style names
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": "Execute a shell command and return stdout+stderr. Timeout 120s. Output capped at 30k chars.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Read",
            "description": "Read a file and return its contents with line numbers. Optionally specify a line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-based, optional)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (1-based, inclusive, optional)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Write",
            "description": "Create or overwrite a file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Edit",
            "description": "Replace the first occurrence of old_string with new_string in a file. Exact string match required.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact string to find and replace",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement string",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Glob",
            "description": "Find files matching a glob pattern. Supports `**` for recursive matching. Returns newline-separated paths relative to the search root, truncated to 30k chars.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (e.g. '*.py', 'src/**/*.ts')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search (defaults to cwd)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Grep",
            "description": "Search for a regex pattern in files. Uses `git grep` when run inside a git repo (honors .gitignore), falls back to a Python re walk otherwise. Returns matching lines as `path:lineno:line`, truncated to 30k chars.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search (defaults to cwd)",
                    },
                    "include": {
                        "type": "string",
                        "description": "File glob to restrict the search (e.g. '*.py')",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _resolve_path(path: str, cwd: str) -> str:
    """Resolve a path relative to cwd if not absolute."""
    if os.path.isabs(path):
        return path
    return os.path.join(cwd, path)


def _exec_bash(args: dict, cwd: str) -> str:
    command = args.get("command", "")
    if not command:
        return "Error: empty command"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n--- stderr ---\n" if output else "") + result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        if not output:
            output = "[no output]"
        return output[:30_000]
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 120 seconds"
    except Exception as e:
        return f"Error: {e}"


def _exec_read(args: dict, cwd: str) -> str:
    path = _resolve_path(args.get("path", ""), cwd)
    start = args.get("start_line")
    end = args.get("end_line")
    try:
        with open(path, "r") as f:
            lines = f.readlines()
        if start is not None or end is not None:
            s = (start or 1) - 1
            e = end or len(lines)
            lines = lines[s:e]
            offset = s
        else:
            offset = 0
        numbered = [f"{i + offset + 1}\t{line}" for i, line in enumerate(lines)]
        content = "".join(numbered)
        if not content:
            return "[empty file]"
        return content[:30_000]
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except Exception as e:
        return f"Error: {e}"


def _exec_write(args: dict, cwd: str) -> str:
    path = _resolve_path(args.get("path", ""), cwd)
    content = args.get("content", "")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"OK: wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"Error: {e}"


def _exec_edit(args: dict, cwd: str) -> str:
    path = _resolve_path(args.get("path", ""), cwd)
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    if not old_string:
        return "Error: old_string is empty"
    try:
        with open(path, "r") as f:
            content = f.read()
        if old_string not in content:
            return f"Error: old_string not found in {path}"
        # Replace first occurrence only
        new_content = content.replace(old_string, new_string, 1)
        with open(path, "w") as f:
            f.write(new_content)
        return f"OK: replaced in {path}"
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except Exception as e:
        return f"Error: {e}"


def _exec_glob(args: dict, cwd: str) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "Error: pattern is required"
    search_root = args.get("path") or cwd
    search_root = _resolve_path(search_root, cwd)
    try:
        root = Path(search_root)
        if not root.exists():
            return f"Error: path not found: {search_root}"
        if "**" in pattern:
            matches = [str(p) for p in root.rglob(pattern)]
        else:
            import glob as _glob
            matches = [str(p) for p in _glob.glob(str(root / pattern), recursive=True)]
        if not matches:
            return f"[no matches for {pattern!r} under {search_root}]"
        return "\n".join(matches)[:30_000]
    except Exception as e:
        return f"Error: {e}"


def _exec_grep(args: dict, cwd: str) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "Error: pattern is required"
    search_path = _resolve_path(args.get("path") or cwd, cwd)
    include = args.get("include")

    # Prefer git grep when inside a git repo (honors .gitignore, fast).
    # Fall back to a Python re walk otherwise.
    try:
        cmd = ["git", "grep", "-n", "--", pattern]
        if include:
            cmd += ["--", include]
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return (result.stdout or "[no matches]")[:30_000]
        if result.returncode == 1:
            return "[no matches]"
        # returncode 2+ -> error; fall through to Python fallback below
        log.debug("git grep failed (rc=%d): %s — falling back to Python re",
                  result.returncode, result.stderr.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Python fallback (works outside git repos)
    try:
        regex = re.compile(pattern)
        root = Path(search_path)
        if root.is_file():
            files = [root]
        else:
            if include:
                files = [p for p in root.rglob(include) if p.is_file()]
            else:
                files = [p for p in root.rglob("*") if p.is_file()]
        matches: list[str] = []
        for f in files:
            try:
                for i, line in enumerate(f.read_text(errors="replace").splitlines(), start=1):
                    if regex.search(line):
                        matches.append(f"{f}:{i}:{line}")
            except (OSError, UnicodeDecodeError):
                continue
        if not matches:
            return "[no matches]"
        return "\n".join(matches)[:30_000]
    except re.error as e:
        return f"Error: invalid regex {pattern!r}: {e}"
    except Exception as e:
        return f"Error: {e}"


_TOOL_EXECUTORS = {
    "Bash": _exec_bash,
    "Read": _exec_read,
    "Write": _exec_write,
    "Edit": _exec_edit,
    "Glob": _exec_glob,
    "Grep": _exec_grep,
}


def _execute_tool(name: str, args: dict, cwd: str) -> str:
    """Execute a tool call and return the result as a plain string."""
    executor = _TOOL_EXECUTORS.get(name)
    if not executor:
        return f"Error: unknown tool '{name}'"
    try:
        return executor(args, cwd)
    except Exception as e:
        return f"Error executing {name}: {e}"


def _parse_arguments(arguments: Any) -> dict:
    """Parse function arguments — may be a dict or a JSON string."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {"_raw": arguments}
    return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def call_agent(
    prompt: str,
    *,
    model: str = _DEFAULT_MODEL,
    cwd: str = ".",
    max_turns: int = 30,
    timeout: int | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model_config: dict | None = None,
) -> dict:
    """Run an agent loop using the OpenAI SDK and return a response dict.

    Returns a dict with keys:
        content       - text body of the final assistant message
        tokens_in     - total input tokens
        tokens_out    - total output tokens
        cost          - total cost in USD (float)
        duration_s    - wall-clock seconds
        finish_reason - "end_turn" | "max_iterations" | "error"

    Never raises — returns a dict with finish_reason="error" on failures.
    """
    start = time.monotonic()

    # Resolve API key
    if not api_key:
        api_key = _resolve_api_key()
    if not api_key:
        return _error_response(
            "No API key: set MINIMAX_API_KEY (or add it to ~/.zshrc)",
            time.monotonic() - start,
        )

    if not base_url:
        base_url = os.environ.get("MINIMAX_BASE_URL") or _DEFAULT_BASE_URL

    # Resolve model alias (m3 -> MiniMax-M3, etc.)
    try:
        from adapters.minimax import resolve_model
        model = resolve_model(model)
    except ImportError:
        pass  # adapter not importable; use the literal model string

    try:
        import openai
    except ImportError:
        return _error_response(
            "openai package not installed. Run: pip install openai",
            time.monotonic() - start,
        )

    try:
        client = openai.OpenAI(api_key=api_key, base_url=base_url)
    except Exception as e:
        return _error_response(f"Failed to create OpenAI client: {e}", time.monotonic() - start)

    # Load model config: prefer the per-model defaults from models/minimax.py
    # (M3 sampling: temperature=0.0, top_p=0.95, max_tokens=16384, think-tag
    # strip policy). Caller-supplied model_config wins on conflict.
    cfg: dict = {}
    try:
        from models import get_model_config
        cfg = dict(get_model_config(model) or {})
    except Exception:
        pass
    if model_config:
        cfg.update(model_config)

    # Apply system prompt suffix from model config
    effective_system = system_prompt or _DEFAULT_SYSTEM_PROMPT
    suffix = cfg.get("system_prompt_suffix", "")
    if suffix:
        effective_system += suffix

    # Sampling params from model config
    temperature = cfg.get("temperature", 0.0)
    sampling_kwargs: dict[str, Any] = {"temperature": temperature}
    if cfg.get("top_p") is not None:
        sampling_kwargs["top_p"] = cfg["top_p"]
    if cfg.get("max_tokens") is not None:
        sampling_kwargs["max_tokens"] = cfg["max_tokens"]

    # Pricing: per-model lookup from adapters/minimax.py MODELS table.
    # Caller can still override via model_config for tests / custom rates.
    price_in_default, price_out_default = _price_for_model(model)
    price_in = cfg.get("price_in", price_in_default)
    price_out = cfg.get("price_out", price_out_default)

    # Checkpoint/nudge config (defaults match models/minimax.py CONFIG)
    checkpoint_interval = cfg.get("checkpoint_interval", 4)
    budget_warning_turns = cfg.get("budget_warning_turns", 8)
    budget_critical_turns = cfg.get("budget_critical_turns", 4)

    # Build initial messages
    messages: list[dict] = [
        {"role": "system", "content": effective_system},
        {"role": "user", "content": prompt},
    ]

    total_in = 0
    total_out = 0
    has_edited = False
    tool_stats = {"Bash": 0, "Read": 0, "Write": 0, "Edit": 0, "Glob": 0, "Grep": 0}

    budget_warnings_sent: set[str] = set()

    try:
        for turn in range(max_turns):
            log.info("[turn %d/%d] calling %s", turn + 1, max_turns, model)

            # Inject turn-budget warnings so the model knows when to wrap up
            remaining = max_turns - turn
            if remaining <= budget_critical_turns and "critical" not in budget_warnings_sent:
                budget_warnings_sent.add("critical")
                messages.append({
                    "role": "system",
                    "content": (
                        f"CRITICAL: Only {remaining} turns left. Your NEXT message "
                        "must be your final text response with NO tool calls. "
                        "Write your answer NOW. Do NOT call any more tools."
                    ),
                })
            elif remaining <= budget_warning_turns and "warning" not in budget_warnings_sent:
                budget_warnings_sent.add("warning")
                messages.append({
                    "role": "system",
                    "content": (
                        f"TURN BUDGET WARNING: You have {remaining} turns remaining. "
                        "Stop exploring and write your final response NOW. "
                        "If you keep calling tools, your work will be lost."
                    ),
                })

            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    stream=False,
                    parallel_tool_calls=False,
                    **sampling_kwargs,
                )
            except Exception as e:
                # Retry transient errors with exponential backoff (1s, 2s, 4s).
                # Matches the pattern in engines/github_claude/runtime.py so
                # the two engines behave consistently under 429/5xx storms.
                error_str = str(e).lower()
                transient = any(x in error_str for x in (
                    "429", "rate limit", "timeout", "502", "503", "overloaded",
                ))
                if not transient:
                    raise
                delay = 1.0
                response = None
                for attempt in range(3):
                    log.warning(
                        "transient error on turn %d (attempt %d/3), sleeping %.1fs: %s",
                        turn + 1, attempt + 1, delay, e,
                    )
                    time.sleep(delay)
                    try:
                        response = client.chat.completions.create(
                            model=model,
                            messages=messages,
                            tools=TOOLS,
                            tool_choice="auto",
                            stream=False,
                            parallel_tool_calls=False,
                            **sampling_kwargs,
                        )
                        break
                    except Exception as e2:
                        last_err = e2
                        delay *= 2
                if response is None:
                    log.error("retry exhausted: %s", last_err)
                    return _error_response(
                        f"API call failed after retries: {last_err}",
                        time.monotonic() - start,
                    )

            # Track tokens
            if response.usage:
                total_in += response.usage.prompt_tokens or 0
                total_out += response.usage.completion_tokens or 0

            if not response.choices:
                log.error("API returned no choices: %s", response.model_dump_json()[:500])
                return _error_response("API returned no choices", time.monotonic() - start)

            choice = response.choices[0]
            message = choice.message
            finish_reason = (choice.finish_reason or "").strip().lower() or None

            # Append assistant message to history (clean think tags from context)
            serialized = _serialize_message(message)
            if cfg and cfg.get("strip_think_tags") and serialized.get("content"):
                from models import clean_output
                serialized["content"] = clean_output(serialized["content"], cfg)

            # Check if model wants to call tools
            if not message.tool_calls:
                # Done — model returned a regular message. Surface the API's
                # `finish_reason` so the orchestrator can distinguish a clean
                # stop from a length-truncation or content-filter hit.
                elapsed = time.monotonic() - start
                content = message.content or ""
                # Clean output (strip <think> tags etc.) per model config
                if cfg:
                    from models import clean_output
                    content = clean_output(content, cfg)
                cost = (total_in * price_in + total_out * price_out) / 1_000_000
                # Map OpenAI finish_reason values to our internal vocabulary.
                # "length" → "truncated" (partial output, may have been cut).
                # "content_filter" → "error" (output was filtered, treat as failure).
                # anything else (incl. None / "stop" / "tool_calls") → "end_turn".
                if finish_reason == "length":
                    effective_finish = "truncated"
                    log.warning(
                        "[done] %d turns, finish_reason=length (output truncated), "
                        "%d in / %d out tokens, $%.4f, %.1fs",
                        turn + 1, total_in, total_out, cost, elapsed,
                    )
                elif finish_reason == "content_filter":
                    effective_finish = "error"
                    log.error(
                        "[done] %d turns, finish_reason=content_filter, "
                        "%d in / %d out tokens, $%.4f, %.1fs",
                        turn + 1, total_in, total_out, cost, elapsed,
                    )
                else:
                    effective_finish = "end_turn"
                    log.info(
                        "[done] %d turns, %d in / %d out tokens, $%.4f, %.1fs",
                        turn + 1, total_in, total_out, cost, elapsed,
                    )
                return {
                    "content": content,
                    "tokens_in": total_in,
                    "tokens_out": total_out,
                    "cost": cost,
                    "duration_s": elapsed,
                    "finish_reason": effective_finish,
                    "tool_stats": tool_stats,
                    "num_turns": turn + 1,
                }

            # Execute tool calls
            for tc in message.tool_calls:
                func_name = tc.function.name
                func_args = _parse_arguments(tc.function.arguments)
                log.info("[tool] %s(%s)", func_name, _truncate_args(func_args))

                result = _execute_tool(func_name, func_args, cwd)

                # Track tool call stats
                if func_name in tool_stats:
                    tool_stats[func_name] += 1

                # Track whether agent has made any file modifications
                if func_name in ("Edit", "Write") and result.startswith("OK"):
                    has_edited = True

                # Append tool result as plain string
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            # Execute checkpoint gate: nudge if no edits after N turns
            turn_num = turn + 1  # 1-based
            if turn_num % checkpoint_interval == 0 and not has_edited:
                messages.append({
                    "role": "system",
                    "content": (
                        f"CHECKPOINT: You have used {turn_num} turns without writing "
                        "or editing any files. If your task requires code changes, "
                        "start making them NOW. Reading and exploring is important "
                        "but you must act on what you've learned."
                    ),
                })

        # Max turns exceeded
        elapsed = time.monotonic() - start
        cost = (total_in * price_in + total_out * price_out) / 1_000_000
        # Try to extract any content from the last assistant message
        content = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                content = msg["content"]
                break
        log.warning("[max_iterations] %d turns exhausted, $%.4f", max_turns, cost)
        return {
            "content": content or f"Agent used all {max_turns} turns without finishing.",
            "tokens_in": total_in,
            "tokens_out": total_out,
            "cost": cost,
            "duration_s": elapsed,
            "finish_reason": "max_iterations",
            "tool_stats": tool_stats,
            "num_turns": max_turns,
        }

    except Exception as e:
        elapsed = time.monotonic() - start
        cost = (total_in * price_in + total_out * price_out) / 1_000_000
        log.exception("agent loop failed")
        resp = _error_response(str(e), elapsed)
        resp["tokens_in"] = total_in
        resp["tokens_out"] = total_out
        resp["cost"] = cost
        resp["tool_stats"] = tool_stats
        resp["num_turns"] = turn if "turn" in locals() else 0
        return resp


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _serialize_message(message) -> dict:
    """Convert an OpenAI ChatCompletionMessage to a plain dict for the messages list.

    Strict-gateway conformance: when the message has tool_calls but no text
    content, set `content` to None explicitly. Some OpenAI-compat gateways
    (and the OpenAI spec itself) require the `content` field to be present
    on every assistant message — string OR null. Omitting the field on a
    tool-call turn triggers HTTP 400 from strict implementations.
    """
    msg: dict[str, Any] = {"role": "assistant"}
    if message.tool_calls:
        # Tool-call turn: explicit null content. Set this BEFORE the truthy
        # check below so it's never silently omitted.
        msg["content"] = message.content if message.content else None
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]
    elif message.content:
        msg["content"] = message.content
    return msg


def _truncate_args(args: dict, max_len: int = 120) -> str:
    """Truncate args dict to a readable log string."""
    s = json.dumps(args, ensure_ascii=False)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _error_response(message: str, elapsed: float) -> dict:
    return {
        "content": message,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost": 0.0,
        "duration_s": elapsed,
        "finish_reason": "error",
    }
