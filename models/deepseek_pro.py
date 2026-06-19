"""Model config: DeepSeek V4 Pro (via OpenRouter).

Same think-tag issue as Flash but more capable — gets slightly more turns.
"""

CONFIG = {
    # --- Sampling ---
    "temperature": 0.3,
    "top_p": 0.95,
    "max_tokens": 8192,

    # --- Turn limits (per phase) ---
    "max_turns": {
        "investigate": 25,
        "implement": 30,
        "review": 20,
    },

    # --- Message processing ---
    "strip_think_tags": True,

    # --- System prompt additions ---
    "system_prompt_suffix": (
        "\n\nIMPORTANT CONSTRAINTS:\n"
        "- Output plain text only. Do NOT wrap your reasoning in <think> or any XML tags.\n"
        "- Be concise. Read only the files you need.\n"
        "- When you have enough information, STOP and write your answer.\n"
    ),

    # --- Checkpoint nudges ---
    "checkpoint_interval": 5,
    "budget_warning_turns": 8,
    "budget_critical_turns": 4,

    # --- Pricing per million tokens (USD) ---
    "price_in": 2.00,
    "price_out": 8.00,
}
