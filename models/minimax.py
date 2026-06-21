"""Model config: MiniMax M3.

Needs temperature=0.0 for stable tool use. Also emits think tags like DeepSeek/GLM.

Environment:
- MINIMAX_API_KEY: required (adapter raises EnvironmentError if missing).
- MINIMAX_STRIP_THINK_TAGS: optional, default "true". Set to "false" to preserve
  <think>...</think> blocks in model output. See the A/B note below under
  `strip_think_tags` in CONFIG.
- LLM_DROP_PARAMS=true: REQUIRED when calling MiniMax via OpenHands/LiteLLM.
  MiniMax errors on unknown body fields rather than ignoring them; LiteLLM
  forwards provider-specific extras that MiniMax rejects unless this flag
  silently drops them. Community research note 2026-06-20.
"""

import os

CONFIG = {
    # --- Sampling ---
    "temperature": 0.0,       # Required for stable tool use
    "top_p": 0.95,
    "max_tokens": 16384,

    # --- Turn limits (per phase) ---
    "max_turns": {
        "investigate": 20,
        "implement": 25,
        "review": 15,
    },

    # --- Message processing ---
    # A/B test in progress: M3 emits think tags like DeepSeek/GLM, and the
    # default is to strip them. Community research (2026-06-20) found that
    # stripping breaks reasoning continuity across multi-turn agent runs —
    # the model can no longer see its own scratchpad on subsequent turns.
    # Set MINIMAX_STRIP_THINK_TAGS=false to preserve them and compare
    # multi-turn task success. Default true to preserve legacy behavior.
    "strip_think_tags": os.environ.get("MINIMAX_STRIP_THINK_TAGS", "true").lower() != "false",  # M3 emits think tags

    # --- System prompt additions ---
    "system_prompt_suffix": (
        "\n\nIMPORTANT CONSTRAINTS:\n"
        "- Be concise. Do not over-explore the codebase.\n"
        "- When you have enough information, STOP and write your answer.\n"
    ),

    # --- Checkpoint nudges ---
    "checkpoint_interval": 4,
    "budget_warning_turns": 8,
    "budget_critical_turns": 4,

    # --- Pricing per million tokens (USD) ---
    # Standard tier (≤512k context): $0.30/M in, $1.20/M out (research 2026-06-20).
    # >512k context tier doubles both to $0.60/M in, $2.40/M out — gate that
    # branch on the upstream `count_tokens` endpoint before charging it.
    "price_in": 0.30,
    "price_out": 1.20,
}
