"""Model config: GLM 5.2 (via Z.ai).

Full GLM model. Same think-tag behavior as Flash variant.
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

    # --- Pricing per million tokens (USD) — subscription ---
    "price_in": 1.40,
    "price_out": 4.40,
}
