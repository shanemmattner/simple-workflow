"""Direct OpenAI SDK runtime for Z.ai's OpenAI-compatible endpoint.

Bypasses OpenHands SDK entirely — just a simple tool-calling agent loop
using the openai Python package against https://api.z.ai/api/coding/paas/v4.

Requirements:
    pip install openai
    Python >= 3.12
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent working in a repository. You have tools to run "
    "commands, read files, write files, and edit files. Be concise and "
    "action-oriented. Do NOT over-explore — read only what you need, then act. "
    "When your task is done, STOP calling tools and write your final response "
    "as a plain text message. Reason in English."
)

_DEFAULT_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
_DEFAULT_MODEL = "glm-5.2"

# Pricing per million tokens (USD)
_PRICE_IN = 1.40
_PRICE_OUT = 4.40

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
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
            "name": "read_file",
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
            "name": "write_file",
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
            "name": "edit_file",
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
]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _resolve_path(path: str, cwd: str) -> str:
    """Resolve a path relative to cwd if not absolute."""
    if os.path.isabs(path):
        return path
    return os.path.join(cwd, path)


def _exec_run_command(args: dict, cwd: str) -> str:
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


def _exec_read_file(args: dict, cwd: str) -> str:
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


def _exec_write_file(args: dict, cwd: str) -> str:
    path = _resolve_path(args.get("path", ""), cwd)
    content = args.get("content", "")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"OK: wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"Error: {e}"


def _exec_edit_file(args: dict, cwd: str) -> str:
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


_TOOL_EXECUTORS = {
    "run_command": _exec_run_command,
    "read_file": _exec_read_file,
    "write_file": _exec_write_file,
    "edit_file": _exec_edit_file,
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
        api_key = os.environ.get("ZAI_API_KEY") or os.environ.get("GLM_API_KEY")
    if not api_key:
        return _error_response("No API key: set ZAI_API_KEY or GLM_API_KEY", time.monotonic() - start)

    if not base_url:
        base_url = _DEFAULT_BASE_URL

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

    # Build initial messages
    messages: list[dict] = [
        {"role": "system", "content": system_prompt or _DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    total_in = 0
    total_out = 0

    budget_warnings_sent: set[str] = set()

    try:
        for turn in range(max_turns):
            log.info("[turn %d/%d] calling %s", turn + 1, max_turns, model)

            # Inject turn-budget warnings so the model knows when to wrap up
            remaining = max_turns - turn
            if remaining <= 3 and "critical" not in budget_warnings_sent:
                budget_warnings_sent.add("critical")
                messages.append({
                    "role": "system",
                    "content": (
                        f"CRITICAL: Only {remaining} turns left. Your NEXT message "
                        "must be your final text response with NO tool calls. "
                        "Write your answer NOW."
                    ),
                })
            elif remaining <= 7 and "warning" not in budget_warnings_sent:
                budget_warnings_sent.add("warning")
                messages.append({
                    "role": "system",
                    "content": (
                        f"TURN BUDGET WARNING: You have {remaining} turns remaining. "
                        "Stop exploring and write your final response NOW. "
                        "If you keep calling tools, your work will be lost."
                    ),
                })

            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.7,
                stream=False,
                parallel_tool_calls=False,
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

            # Append assistant message to history
            messages.append(_serialize_message(message))

            # Check if model wants to call tools
            if not message.tool_calls:
                # Done — model returned a regular message
                elapsed = time.monotonic() - start
                content = message.content or ""
                cost = (total_in * _PRICE_IN + total_out * _PRICE_OUT) / 1_000_000
                log.info("[done] %d turns, %d in / %d out tokens, $%.4f, %.1fs",
                         turn + 1, total_in, total_out, cost, elapsed)
                return {
                    "content": content,
                    "tokens_in": total_in,
                    "tokens_out": total_out,
                    "cost": cost,
                    "duration_s": elapsed,
                    "finish_reason": "end_turn",
                }

            # Execute tool calls
            for tc in message.tool_calls:
                func_name = tc.function.name
                func_args = _parse_arguments(tc.function.arguments)
                log.info("[tool] %s(%s)", func_name, _truncate_args(func_args))

                result = _execute_tool(func_name, func_args, cwd)

                # Append tool result as plain string
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        # Max turns exceeded
        elapsed = time.monotonic() - start
        cost = (total_in * _PRICE_IN + total_out * _PRICE_OUT) / 1_000_000
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
        }

    except Exception as e:
        elapsed = time.monotonic() - start
        cost = (total_in * _PRICE_IN + total_out * _PRICE_OUT) / 1_000_000
        log.exception("agent loop failed")
        resp = _error_response(str(e), elapsed)
        resp["tokens_in"] = total_in
        resp["tokens_out"] = total_out
        resp["cost"] = cost
        return resp


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _serialize_message(message) -> dict:
    """Convert an OpenAI ChatCompletionMessage to a plain dict for the messages list."""
    msg: dict[str, Any] = {"role": "assistant"}
    if message.content:
        msg["content"] = message.content
    if message.tool_calls:
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
