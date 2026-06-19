"""Model config: MiniMax M3.

Needs temperature=0.0 for stable tool use. No think-tag issues.
"""

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
    "strip_think_tags": False,  # M3 doesn't emit think tags

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
    "price_in": 0.50,
    "price_out": 1.10,
}
