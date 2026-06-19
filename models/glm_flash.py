"""Model config: GLM 4.7 Flash (via Z.ai).

GLM also emits <think> blocks. Lower token costs since it's subscription-based.
"""

CONFIG = {
    # --- Sampling ---
    "temperature": 0.3,
    "top_p": 0.95,
    "max_tokens": 8192,

    # --- Turn limits (per phase) ---
    "max_turns": {
        "investigate": 20,
        "implement": 25,
        "review": 15,
    },

    # --- Message processing ---
    "strip_think_tags": True,

    # --- System prompt additions ---
    "system_prompt_suffix": (
        "\n\nIMPORTANT CONSTRAINTS:\n"
        "- Output plain text only. Do NOT wrap your reasoning in <think> or any XML tags.\n"
        "- Be extremely concise. Read only the files you need.\n"
        "- When you have enough information, STOP and write your answer.\n"
        "- You have a strict turn budget. Do not waste turns.\n"
    ),

    # --- Checkpoint nudges ---
    "checkpoint_interval": 4,
    "budget_warning_turns": 8,
    "budget_critical_turns": 4,

    # --- Pricing per million tokens (USD) — subscription, so nominal ---
    "price_in": 1.40,
    "price_out": 4.40,
}
