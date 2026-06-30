"""Provider adapter router — picks the right adapter for a given model name."""

from __future__ import annotations

import importlib
from types import ModuleType


def get_adapter(model: str) -> ModuleType:
    """Return the adapter module for *model*."""
    m = model.lower().strip()
    if m.startswith("glm"):
        return importlib.import_module("adapters.zai")
    if m.startswith("claude") or m in ("sonnet", "opus", "haiku"):
        return importlib.import_module("adapters.claude_cli")
    if m.startswith("minimax") or m in ("m3", "m27hs", "minimax-m2.7-highspeed"):
        return importlib.import_module("adapters.minimax")
    # Everything else → OpenRouter (with approved-model gate inside)
    return importlib.import_module("adapters.openrouter")


def get_config(model: str) -> dict:
    """Convenience: resolve adapter then call its get_config."""
    adapter = get_adapter(model)
    return adapter.get_config(model)
