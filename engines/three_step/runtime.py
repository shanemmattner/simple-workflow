"""Generic OpenAI-compatible SDK runtime for the three-step pipeline.

Supports any OpenAI-compatible endpoint: Z.ai (GLM), DeepSeek, MiniMax, local
Qwen, or OpenRouter. The caller (orchestrator._call_openai_compat) resolves the
api_key and base_url via adapters.get_config(model) and passes them directly.

Generic OpenAI-compatible agent loop — same tool set (Read/Edit/Write/Bash/Glob/Grep),
same return shape. Key/URL resolution and pricing tables come from the caller
via adapters.get_config(model).

Requirements:
    pip install openai
    Python >= 3.12
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
# Defaults (overridden by model_config from caller)
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent working in a repository. You have tools to run "
    "commands, read files, write files, edit files, glob for paths, and grep "
    "for patterns. Make progress every turn; read only what you need, then act."
)

# Fallback pricing (USD per 1M tokens) when model_config provides no rates.
# Intentionally conservative — better to over-report cost than under-report.
_FALLBACK_PRICE_IN = 1.40   # GLM-5.2 input rate (adapters/zai.py)
_FALLBACK_PRICE_OUT = 4.40  # GLM-5.2 output rate


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
    model: str,
    cwd: str = ".",
    max_turns: int = 30,
    timeout: int | None = None,
    system_prompt: str | None = None,
    api_key: str,
    base_url: str,
    model_config: dict | None = None,
) -> dict:
    """Run an agent loop using the OpenAI SDK against any OpenAI-compatible endpoint.

    Called by orchestrator._call_openai_compat() with adapter-resolved credentials.
    Supports Z.ai (GLM), DeepSeek, MiniMax, OpenRouter, and local Qwen endpoints.

    Returns a dict with keys:
        content              - text body of the final assistant message
        tokens_in            - total input tokens
        tokens_out           - total output tokens
        cost                 - total cost in USD (float)
        duration_s           - wall-clock seconds
        finish_reason        - "end_turn" | "max_iterations" | "truncated" | "error"
        session_id           - always "" (OpenAI API has no session concept)
        num_turns            - number of turns used
        cache_read_tokens    - always 0 (not reported by OpenAI-compat APIs)
        cache_creation_tokens - always 0

    Never raises — returns a dict with finish_reason="error" on failures.
    """
    start = time.monotonic()

    if not api_key:
        return _error_response(
            "No API key provided. Check adapter configuration.",
            time.monotonic() - start,
        )
    if not base_url:
        return _error_response(
            "No base_url provided. Check adapter configuration.",
            time.monotonic() - start,
        )

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

    cfg: dict = dict(model_config or {})

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

    # Pricing: from model_config if available, else conservative fallback
    price_in = cfg.get("price_in", _FALLBACK_PRICE_IN)
    price_out = cfg.get("price_out", _FALLBACK_PRICE_OUT)

    # Budget warning thresholds (turns remaining when to nudge the model)
    checkpoint_interval = cfg.get("checkpoint_interval", 4)
    budget_warning_turns = cfg.get("budget_warning_turns", 8)
    budget_critical_turns = cfg.get("budget_critical_turns", 4)

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
            log.info("[turn %d/%d] calling %s @ %s", turn + 1, max_turns, model, base_url)

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
                error_str = str(e)
                # Retry transient errors with exponential backoff (1s, 2s, 4s)
                transient = any(x in error_str.lower() for x in (
                    "429", "rate limit", "timeout", "502", "503", "overloaded",
                ))
                if not transient:
                    raise
                delay = 1.0
                response = None
                last_err: Exception = e
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

            # Append assistant message BEFORE tool results to avoid tool_call_id
            # mismatch errors on strict OpenAI-compat gateways
            serialized = _serialize_message(message)
            if cfg.get("strip_think_tags") and serialized.get("content"):
                try:
                    from models import clean_output
                    serialized["content"] = clean_output(serialized["content"], cfg)
                except ImportError:
                    pass
            messages.append(serialized)

            # No tool calls → final response
            if not message.tool_calls:
                elapsed = time.monotonic() - start
                content = message.content or ""
                if cfg.get("strip_think_tags"):
                    try:
                        from models import clean_output
                        content = clean_output(content, cfg)
                    except ImportError:
                        pass
                cost = (total_in * price_in + total_out * price_out) / 1_000_000

                if finish_reason == "length":
                    effective_finish = "truncated"
                    log.warning(
                        "[done] %d turns, finish_reason=length (truncated), "
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
                    "session_id": "",
                    "num_turns": turn + 1,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "tool_stats": tool_stats,
                }

            # Execute tool calls
            for tc in message.tool_calls:
                func_name = tc.function.name
                func_args = _parse_arguments(tc.function.arguments)
                log.info("[tool] %s(%s)", func_name, _truncate_args(func_args))

                result = _execute_tool(func_name, func_args, cwd)

                if func_name in tool_stats:
                    tool_stats[func_name] += 1

                if func_name in ("Edit", "Write") and result.startswith("OK"):
                    has_edited = True

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            # Checkpoint nudge: prompt to act if no edits after N turns
            turn_num = turn + 1
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

        # Max turns exhausted
        elapsed = time.monotonic() - start
        cost = (total_in * price_in + total_out * price_out) / 1_000_000
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
            "session_id": "",
            "num_turns": max_turns,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "tool_stats": tool_stats,
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

def _serialize_message(message: Any) -> dict:
    """Convert an OpenAI ChatCompletionMessage to a plain dict for the messages list.

    Sets content to None explicitly on tool-call turns so strict OpenAI-compat
    gateways don't reject the message for a missing content field.
    """
    msg: dict[str, Any] = {"role": "assistant"}
    if message.tool_calls:
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
        "session_id": "",
        "num_turns": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
