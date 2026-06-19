"""Model config: DeepSeek V4 Flash (via OpenRouter).

DeepSeek emits <think>...</think> blocks that contaminate downstream prompts.
It also over-explores — 636K tokens for investigation vs Haiku's 142 tokens.
Needs hard turn limits and aggressive nudges.
"""

CONFIG = {
    # --- Sampling ---
    "temperature": 0.3,       # Lower than default 0.7 — reduces rambling
    "top_p": 0.95,
    "max_tokens": 8192,

    # --- Turn limits (per phase) ---
    "max_turns": {
        "investigate": 20,    # Default is 40 — DeepSeek over-explores at 40
        "implement": 25,
        "review": 15,
    },

    # --- Message processing ---
    "strip_think_tags": True,  # Remove <think>...</think> from output

    # --- System prompt additions ---
    "system_prompt_suffix": (
        "\n\nIMPORTANT CONSTRAINTS:\n"
        "- Output plain text only. Do NOT wrap your reasoning in <think> or any XML tags.\n"
        "- Be extremely concise. Read only the files you need — do not explore broadly.\n"
        "- When you have enough information, STOP reading and write your answer.\n"
        "- You have a strict turn budget. Do not waste turns on unnecessary reads.\n"
    ),

    # --- Checkpoint nudges ---
    "checkpoint_interval": 4,   # Nudge every 4 turns instead of 5
    "budget_warning_turns": 8,  # Start warning at 8 remaining (vs default 7)
    "budget_critical_turns": 4, # Critical at 4 remaining (vs default 3)

    # --- Pricing per million tokens (USD) ---
    "price_in": 0.20,
    "price_out": 0.60,
}
