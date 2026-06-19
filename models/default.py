"""Default model config — used when no model-specific config exists.

Provides sensible defaults that match the current hardcoded behavior.
"""

CONFIG = {
    # --- Sampling ---
    "temperature": 0.7,
    "top_p": 1.0,
    "max_tokens": None,       # Let the API decide

    # --- Turn limits (per phase) ---
    "max_turns": {},           # Empty = use orchestrator defaults

    # --- Message processing ---
    "strip_think_tags": False,

    # --- System prompt additions ---
    "system_prompt_suffix": "",

    # --- Checkpoint nudges ---
    "checkpoint_interval": 5,
    "budget_warning_turns": 7,
    "budget_critical_turns": 3,

    # --- Pricing per million tokens (USD) ---
    "price_in": 1.40,
    "price_out": 4.40,
}
