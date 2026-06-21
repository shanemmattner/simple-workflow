"""MiniMax adapter — MiniMax-M3 via MiniMax API (OpenAI-compat format)."""

from __future__ import annotations

import os

BASE_URL = "https://api.minimaxi.chat/v1"

AVAILABLE_MODELS: set[str] = {
    "MiniMax-M3",
    "MiniMax-M2.7-highspeed",
}

# Cost in USD per million tokens (input, output). Verify against
# https://docs.minimax.io/ before production use.
MODELS: dict[str, dict] = {
    "MiniMax-M3":            {"cost_in": 0.30, "cost_out": 1.20, "context": 1_048_576},
    "MiniMax-M2.7-highspeed":{"cost_in": 0.20, "cost_out": 0.80, "context": 205_000},
}

_SHORT_NAMES: dict[str, str] = {
    "minimax": "MiniMax-M3",
    "m3": "MiniMax-M3",
    "minimax-m3": "MiniMax-M3",
    "m27hs": "MiniMax-M2.7-highspeed",
    "minimax-m2.7-highspeed": "MiniMax-M2.7-highspeed",
}

# Extra sampling params M3 needs for stable tool-use performance
_DEFAULT_PARAMS: dict = {
    "temperature": 0.0,
    "top_p": 0.95,
    "max_tokens": 16384,
}


def resolve_model(model: str) -> str:
    """Map short names to full MiniMax model strings."""
    return _SHORT_NAMES.get(model.lower(), model)


def validate_model(model: str) -> None:
    """Raise ValueError if *model* is not available on MiniMax."""
    resolved = resolve_model(model)
    if resolved not in AVAILABLE_MODELS:
        raise ValueError(
            f"Model {model!r} (resolved: {resolved!r}) not available on MiniMax. "
            f"Available: {sorted(AVAILABLE_MODELS)}"
        )


def get_config(model: str) -> dict:
    """Return config dict for the direct OpenAI SDK runtime.

    Uses plain model name (no prefix) since we're hitting MiniMax's
    OpenAI-compatible endpoint directly with the openai Python SDK.
    Includes extra sampling params that M3 requires for reliable tool use.
    """
    resolved = resolve_model(model)
    validate_model(resolved)

    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        raise EnvironmentError("MINIMAX_API_KEY env var not set")

    return {
        "api_key": api_key,
        "base_url": BASE_URL,
        "model": resolved,
        **_DEFAULT_PARAMS,
    }
