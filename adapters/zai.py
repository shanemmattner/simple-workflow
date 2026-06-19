"""Z.ai adapter — GLM models via Z.ai subscription API (OpenAI-compat format)."""

from __future__ import annotations

import os

BASE_URL = "https://api.z.ai/api/coding/paas/v4"  # Coding Plan subscription endpoint — the ONLY supported endpoint for GLM

AVAILABLE_MODELS: set[str] = {
    "glm-5.2",
    "glm-4.7",
    "glm-4.7-flash",
}

_SHORT_NAMES: dict[str, str] = {
    "glm": "glm-5.2",
    "glm-flash": "glm-4.7-flash",
    "glm-4.7-flash": "glm-4.7-flash",
}


def resolve_model(model: str) -> str:
    """Map short names to full Z.ai model strings."""
    return _SHORT_NAMES.get(model, model)


def validate_model(model: str) -> None:
    """Raise ValueError if *model* is not available on Z.ai."""
    resolved = resolve_model(model)
    if resolved not in AVAILABLE_MODELS:
        raise ValueError(
            f"Model {model!r} (resolved: {resolved!r}) not available on Z.ai. "
            f"Available: {sorted(AVAILABLE_MODELS)}"
        )


def get_config(model: str) -> dict:
    """Return config dict for the direct OpenAI SDK runtime.

    Uses plain model name (no prefix) since we're hitting Z.ai's
    OpenAI-compatible endpoint directly with the openai Python SDK.
    """
    resolved = resolve_model(model)
    validate_model(resolved)

    # ZAI_API_KEY is canonical; GLM_API_KEY is legacy fallback
    api_key = os.environ.get("ZAI_API_KEY") or os.environ.get("GLM_API_KEY")
    if not api_key:
        raise EnvironmentError("ZAI_API_KEY (or GLM_API_KEY) env var not set")

    return {
        "api_key": api_key,
        "base_url": BASE_URL,
        "model": resolved,
    }
