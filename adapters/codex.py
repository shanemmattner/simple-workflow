"""Codex adapter — stub, not yet configured."""

from __future__ import annotations

AVAILABLE_MODELS: set[str] = {"codex"}

_SHORT_NAMES: dict[str, str] = {}


def resolve_model(model: str) -> str:
    return _SHORT_NAMES.get(model, model)


def validate_model(model: str) -> None:
    resolved = resolve_model(model)
    if resolved not in AVAILABLE_MODELS:
        raise ValueError(f"Model {model!r} not available via Codex adapter")


def get_config(model: str = "codex") -> dict:
    raise NotImplementedError("Codex adapter not yet configured")
