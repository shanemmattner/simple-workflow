"""OpenRouter adapter — approved models only, OPENROUTER_API_KEY auth."""

from __future__ import annotations

import os

BASE_URL = "https://openrouter.ai/api/v1"

AVAILABLE_MODELS: set[str] = {
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-v3.2",
    "x-ai/grok-code-fast-1",
    "google/gemini-3-flash-preview",
    "xiaomi/mimo-v2-flash:free",
    "mistralai/devstral-2512:free",
    "openai/gpt-oss-120b",
    "z-ai/glm-5.2",
}

_SHORT_NAMES: dict[str, str] = {
    "deepseek-flash": "deepseek/deepseek-v4-flash",
    "deepseek-pro": "deepseek/deepseek-v4-pro",
    "deepseek": "deepseek/deepseek-v4-flash",
    "deepseek-v3": "deepseek/deepseek-v3.2",
    "grok": "x-ai/grok-code-fast-1",
    "gemini-flash": "google/gemini-3-flash-preview",
    "mimo": "xiaomi/mimo-v2-flash:free",
    "devstral": "mistralai/devstral-2512:free",
    "gpt-oss": "openai/gpt-oss-120b",
    "glm-openrouter": "z-ai/glm-5.2",
}


def resolve_model(model: str) -> str:
    """Map short names to full OpenRouter model strings."""
    return _SHORT_NAMES.get(model, model)


def validate_model(model: str) -> None:
    """Raise ValueError if *model* is not on the approved list."""
    resolved = resolve_model(model)
    if resolved not in AVAILABLE_MODELS:
        raise ValueError(
            f"Model {model!r} (resolved: {resolved!r}) not on OpenRouter "
            f"approved list. Approved: {sorted(AVAILABLE_MODELS)}"
        )


def get_config(model: str) -> dict:
    """Return config dict ready for the OpenHands SDK LLM constructor."""
    resolved = resolve_model(model)
    validate_model(resolved)

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENROUTER_API_KEY env var not set")

    return {
        "api_key": api_key,
        "base_url": BASE_URL,
        "model": f"openrouter/{resolved}",
    }
