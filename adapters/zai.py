"""Z.ai adapter — GLM models via Z.ai subscription API (OpenAI-compat format)."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

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


def _lookup_rc_env(var_names: list[str]) -> str | None:
    """Return the first matching var value from ~/.zshrc, or None.

    Some users store API keys in ~/.zshrc rather than exporting them.
    We parse that file as a fallback when the env var is unset.
    Shell command-substitution markers are rejected.
    """
    rcfile = Path.home() / ".zshrc"
    if not rcfile.is_file():
        return None
    try:
        text = rcfile.read_text()
    except OSError:
        return None
    _RC_VAR_RE = re.compile(
        r"""^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*['"]?([^'"\s$`()#]+)['"]?\s*(?:#.*)?$""",
        re.MULTILINE,
    )
    matches: dict[str, str] = {}
    for m in _RC_VAR_RE.finditer(text):
        name, value = m.group(1), m.group(2)
        if any(ch in value for ch in ("$", "`", "(", ")")):
            continue
        matches[name] = value
    for name in var_names:
        if name in matches:
            return matches[name]
    return None


def _resolve_api_key() -> str | None:
    """Return the Z.ai API key: ZAI_API_KEY first, GLM_API_KEY fallback, then ~/.zshrc."""
    key = os.environ.get("ZAI_API_KEY")
    if key:
        log.debug("zai: using ZAI_API_KEY from environment")
        return key
    key = os.environ.get("GLM_API_KEY")
    if key:
        log.debug("zai: using GLM_API_KEY from environment (legacy fallback)")
        return key
    key = _lookup_rc_env(["ZAI_API_KEY", "GLM_API_KEY"])
    if key:
        log.debug("zai: using key from ~/.zshrc")
        return key
    return None


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

    # ZAI_API_KEY is canonical; GLM_API_KEY is legacy fallback; ~/.zshrc is last resort
    api_key = _resolve_api_key()
    if not api_key:
        raise EnvironmentError(
            "No Z.ai API key found. Set ZAI_API_KEY (or GLM_API_KEY) as an env var, "
            "or add it to ~/.zshrc."
        )

    return {
        "api_key": api_key,
        "base_url": BASE_URL,
        "model": resolved,
    }
