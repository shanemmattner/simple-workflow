"""OpenHands SDK runtime — uses openhands-sdk with LiteLLM for model calls.

Default model: DeepSeek V4 Flash via OpenRouter.
Auth: OPENROUTER_API_KEY env var (passed as LLM api_key).
No Docker — local workspace mode only.

Requirements:
    pip install -U openhands-sdk openhands-tools
    Python >= 3.12
"""

from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_DEFAULT_SYSTEM_PROMPT = (
    "You are a coding agent. Focus on the task in your prompt. "
    "Do not delegate work."
)

# ---------------------------------------------------------------------------
# Model name mapping
# ---------------------------------------------------------------------------

# The orchestrator passes short model names like "sonnet", "haiku", "opus".
# Map these to OpenRouter model strings.  Pass anything else through as-is
# (the caller may already supply a fully-qualified LiteLLM model string).
_MODEL_MAP: dict[str, str] = {
    "sonnet": "anthropic/claude-sonnet-4-6",
    "haiku": "anthropic/claude-haiku-4-5",
    "opus": "anthropic/claude-opus-4-6",
    # DeepSeek shortcuts
    "deepseek": _DEFAULT_MODEL,
    "deepseek-flash": _DEFAULT_MODEL,
    "deepseek-pro": "deepseek/deepseek-v4-pro",
}


def _resolve_model(model: str) -> str:
    """Map short names to fully-qualified OpenRouter model strings."""
    return _MODEL_MAP.get(model, model)


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
    system_prompt: str | None = None,
) -> dict:
    """Run an OpenHands agent and return a parsed response dict.

    Returns a dict with keys:
        content       - text body of the final assistant message
        tokens_in     - total input tokens
        tokens_out    - output tokens
        cost          - total cost in USD (float)
        duration_s    - wall-clock seconds
        finish_reason - "end_turn" | "timeout" | "error"

    Never raises — returns a dict with finish_reason="error" on failures.
    """
    resolved_model = _resolve_model(model)
    start = time.monotonic()

    try:
        return _run_openhands(
            prompt,
            model=resolved_model,
            cwd=cwd,
            max_turns=max_turns,
            timeout=timeout,
            system_prompt=system_prompt or _DEFAULT_SYSTEM_PROMPT,
            start=start,
        )
    except Exception as exc:
        elapsed = time.monotonic() - start
        log.exception("call_agent failed")
        return _error_response(str(exc), elapsed)


def _run_openhands(
    prompt: str,
    *,
    model: str,
    cwd: str,
    max_turns: int,
    timeout: int | None,
    system_prompt: str,
    start: float,
) -> dict:
    """Core OpenHands SDK execution.  Separated for clean error handling."""

    # Late imports so the module loads even if openhands-sdk is not installed
    # (allows import-time checks and better error messages).
    try:
        from openhands.sdk import LLM, Agent, Conversation, Tool
        from openhands.tools.file_editor import FileEditorTool
        from openhands.tools.terminal import TerminalTool
    except ImportError as exc:
        elapsed = time.monotonic() - start
        return _error_response(
            f"openhands-sdk not installed: {exc}. "
            "Run: pip install -U openhands-sdk openhands-tools",
            elapsed,
        )

    # --- Resolve API key ---
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("LLM_API_KEY")
    if not api_key:
        elapsed = time.monotonic() - start
        return _error_response(
            "No API key: set OPENROUTER_API_KEY or LLM_API_KEY env var",
            elapsed,
        )

    # --- Determine base_url ---
    # All models route through OpenRouter — set base_url unconditionally.
    custom_base = os.environ.get("LLM_BASE_URL")
    base_url = custom_base or os.environ.get("OPENROUTER_API_BASE", _OPENROUTER_BASE_URL)

    # When LLM_BASE_URL is set (e.g. local Claude subscription proxy at
    # localhost:8420), the `anthropic/` prefix causes LiteLLM to bypass the
    # custom base_url and route directly to Anthropic's API.  Force `openai/`
    # prefix so LiteLLM treats it as an OpenAI-compatible endpoint.
    if custom_base and model.startswith("anthropic/"):
        model = "openai/" + model.removeprefix("anthropic/")
        log.info("LLM_BASE_URL set — rewrote model to %s for OpenAI-compat routing", model)

    # --- Build LLM ---
    llm_kwargs: dict = {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
    }

    llm = LLM(**llm_kwargs)

    # --- Build Agent ---
    agent = Agent(
        llm=llm,
        system_prompt=system_prompt,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
        ],
    )

    # --- Run Conversation ---
    conversation = Conversation(
        agent=agent,
        workspace=cwd,
        max_iteration_per_run=max_turns,
    )

    conversation.send_message(prompt)

    # Run the agent loop.  The SDK's run() may or may not accept a timeout
    # kwarg depending on version — handle both gracefully.
    effective_timeout = float(timeout if timeout is not None else 600)
    try:
        conversation.run(timeout=effective_timeout)
    except TypeError:
        # Older SDK versions don't accept timeout on run()
        log.warning("conversation.run() does not accept timeout param, running without")
        conversation.run()

    elapsed = time.monotonic() - start

    # --- Extract results ---
    return _extract_response(conversation, llm, elapsed)


def _extract_response(conversation, llm, elapsed: float) -> dict:
    """Pull content, tokens, cost, and finish_reason from a completed conversation."""

    # --- Finish reason from execution status ---
    finish_reason = "end_turn"
    try:
        status = conversation.state.execution_status
        # ConversationExecutionStatus enum: FINISHED, ERROR, STUCK, IDLE, etc.
        status_name = status.name if hasattr(status, "name") else str(status)
        if status_name in ("ERROR", "STUCK"):
            finish_reason = "error"
        # FINISHED and IDLE both map to end_turn (success)
    except Exception:
        pass  # fall through to end_turn default

    # --- Extract content from events ---
    content = ""

    # Try 1: canonical utility function (available in newer SDK versions)
    try:
        from openhands.sdk.conversation.response_utils import get_agent_final_response
        content = get_agent_final_response(conversation.state.events) or ""
        if content:
            log.debug("extracted content via get_agent_final_response (len=%d)", len(content))
    except ImportError:
        log.debug("get_agent_final_response not available, using manual extraction")
    except Exception as exc:
        log.warning("get_agent_final_response failed: %s", exc)

    # Try 2: manual loop — find the finish tool's ActionEvent, extract .action.message
    if not content:
        try:
            from openhands.sdk.event.llm_convertible import ActionEvent
            from openhands.sdk.tool.builtins.finish import FinishAction, FinishTool

            for event in reversed(list(conversation.state.events)):
                if (isinstance(event, ActionEvent)
                        and event.source == "agent"
                        and event.tool_name == FinishTool.name
                        and isinstance(event.action, FinishAction)):
                    content = event.action.message or ""
                    if content:
                        log.debug("extracted content from FinishAction.message (len=%d)", len(content))
                    break
        except ImportError:
            log.debug("FinishAction/FinishTool imports not available")
        except Exception as exc:
            log.warning("manual finish-event extraction failed: %s", exc)

    # Try 3: last resort — any ActionEvent from agent, try .action.message then string attrs
    if not content:
        try:
            from openhands.sdk.event.llm_convertible import ActionEvent

            for event in reversed(list(conversation.state.events)):
                if isinstance(event, ActionEvent) and getattr(event, "source", "") == "agent":
                    action = getattr(event, "action", None)
                    if action:
                        msg = getattr(action, "message", None)
                        if msg:
                            content = str(msg)
                            log.debug("extracted content from agent ActionEvent.action.message (len=%d)", len(content))
                            break
                        # Try any string attribute on the action
                        for attr in dir(action):
                            if attr.startswith("_"):
                                continue
                            val = getattr(action, attr, None)
                            if isinstance(val, str) and len(val) > 20:
                                content = val
                                log.debug("extracted content from action.%s (len=%d)", attr, len(content))
                                break
                    if content:
                        break
        except ImportError:
            pass
        except Exception as exc:
            log.warning("fallback event extraction failed: %s", exc)

    if not content:
        log.warning("no content extracted from conversation events")

    # --- Extract token usage and cost ---
    tokens_in = 0
    tokens_out = 0
    cost = 0.0

    # Try conversation_stats first (aggregated across all LLM calls)
    try:
        metrics = conversation.conversation_stats.get_combined_metrics()
        cost = getattr(metrics, "accumulated_cost", 0.0) or 0.0
        usage = getattr(metrics, "accumulated_token_usage", None)
        if usage:
            tokens_in = getattr(usage, "prompt_tokens", 0) or 0
            tokens_out = getattr(usage, "completion_tokens", 0) or 0
    except Exception:
        pass

    # Fallback: try llm.metrics directly
    if tokens_in == 0 and tokens_out == 0:
        try:
            if llm.metrics:
                cost = getattr(llm.metrics, "accumulated_cost", 0.0) or 0.0
                usage = getattr(llm.metrics, "accumulated_token_usage", None)
                if usage:
                    tokens_in = getattr(usage, "prompt_tokens", 0) or 0
                    tokens_out = getattr(usage, "completion_tokens", 0) or 0
        except Exception:
            pass

    # --- Extract tool calls from events ---
    tool_calls: list[dict] = []
    try:
        from openhands.sdk.event.llm_convertible import ActionEvent

        for event in conversation.state.events:
            if not isinstance(event, ActionEvent):
                continue
            if getattr(event, "source", "") != "agent":
                continue
            tool_name = getattr(event, "tool_name", None)
            if not tool_name:
                continue
            action = getattr(event, "action", None)
            # Serialize tool input from the action object
            tool_input = ""
            if action:
                try:
                    import json as _json
                    tool_input = _json.dumps(
                        {k: v for k, v in vars(action).items() if not k.startswith("_")},
                        default=str,
                    )
                except Exception:
                    tool_input = str(action)
            # Try to find the corresponding result
            tool_result = ""
            result_obj = getattr(event, "result", None)
            if result_obj:
                tool_result = str(getattr(result_obj, "output", "")) or str(result_obj)
            tool_calls.append({
                "tool_name": tool_name,
                "tool_input": tool_input[:10_000],   # cap size
                "tool_result": tool_result[:10_000],
                "duration_ms": 0,
            })
    except ImportError:
        log.debug("ActionEvent import not available for tool call extraction")
    except Exception as exc:
        log.warning("tool call extraction failed: %s", exc)

    return {
        "content": content,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": cost,
        "duration_s": elapsed,
        "finish_reason": finish_reason,
        "_tool_calls": tool_calls,
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
