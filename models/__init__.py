"""Per-model configuration loader.

Maps model short names (used by adapters and CLI flags) to config dicts
that control sampling, message processing, turn limits, and prompt behavior.

Usage:
    from models import get_model_config, clean_output

    cfg = get_model_config("deepseek-flash")
    text = clean_output("some <think>inner thought</think> text", cfg)
"""

from __future__ import annotations

import importlib
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

# Map CLI short names and adapter model strings to config module names.
# If a model isn't listed here, falls back to "default".
_MODEL_TO_MODULE: dict[str, str] = {
    # DeepSeek
    "deepseek-flash": "deepseek_flash",
    "deepseek/deepseek-v4-flash": "deepseek_flash",
    "deepseek-pro": "deepseek_pro",
    "deepseek/deepseek-v4-pro": "deepseek_pro",
    "deepseek": "deepseek_flash",
    "deepseek-v3": "deepseek_flash",  # v3 uses same config as flash
    "deepseek/deepseek-v3.2": "deepseek_flash",
    # GLM
    "glm": "glm",
    "glm-5.2": "glm",
    "glm-flash": "glm_flash",
    "glm-4.7-flash": "glm_flash",
    "glm-4.7": "glm",
    # MiniMax
    "minimax": "minimax",
    "minimax-m3": "minimax",
    "m3": "minimax",
    "MiniMax-M3": "minimax",
    # Others via OpenRouter
    "grok": "default",
    "gemini-flash": "default",
    "mimo": "default",
    "devstral": "default",
    "gpt-oss": "default",
}

# Compiled regex for stripping <think>...</think> blocks.
# Handles multiline content and nested tags (greedy match to outermost).
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def get_model_config(model: str) -> dict[str, Any]:
    """Load the config dict for a model name.

    Falls back to default config if no model-specific config exists.
    Merges with default to ensure all keys are present.
    """
    module_name = _MODEL_TO_MODULE.get(model, "default")

    try:
        mod = importlib.import_module(f"models.{module_name}")
        cfg = dict(mod.CONFIG)  # shallow copy
    except (ImportError, AttributeError):
        log.warning("No config module for model %r, using default", model)
        mod = importlib.import_module("models.default")
        cfg = dict(mod.CONFIG)

    # Merge with default to fill any missing keys
    if module_name != "default":
        default_mod = importlib.import_module("models.default")
        for key, val in default_mod.CONFIG.items():
            if key not in cfg:
                cfg[key] = val

    return cfg


def clean_output(text: str, config: dict[str, Any]) -> str:
    """Process model output according to config rules.

    Currently handles:
    - Stripping <think>...</think> blocks
    """
    if not text:
        return text

    if config.get("strip_think_tags", False):
        cleaned = _THINK_RE.sub("", text).strip()
        if cleaned != text.strip():
            log.info("Stripped <think> tags from output (%d -> %d chars)",
                     len(text), len(cleaned))
        return cleaned

    return text
