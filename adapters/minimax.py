"""MiniMax adapter — MiniMax-M3 via MiniMax API (OpenAI-compat format)."""

from __future__ import annotations

import os

BASE_URL = "https://api.minimax.io/v1"


def _resolve_api_key() -> str | None:
    """Return the MiniMax API key from env, with ~/.zshrc fallback."""
    key = os.environ.get("MINIMAX_API_KEY")
    if key:
        return key
    # Canonical implementation lives in engines/github_minimax/runtime.py.
    # Import lazily to avoid a top-level circular import (runtime imports
    # MODELS and resolve_model from this module).
    from engines.github_minimax.runtime import _lookup_rc_env
    return _lookup_rc_env(["MINIMAX_API_KEY"])

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

    api_key = _resolve_api_key()
    if not api_key:
        raise EnvironmentError(
            "MINIMAX_API_KEY not set (and ~/.zshrc has no export — "
            "run `export MINIMAX_API_KEY=...` or add it to ~/.zshrc)"
        )

    return {
        "api_key": api_key,
        "base_url": BASE_URL,
        "model": resolved,
        **_DEFAULT_PARAMS,
    }


# ---------------------------------------------------------------------------
# Interchangeability layer: resolve_auto() picks M3 vs M2.7-highspeed based
# on a task-type heuristic. Callers say `model: minimax` (the wildcard alias)
# and the runtime picks the right concrete model for the phase. Explicit
# m3/m27hs/MiniMax-M3/MiniMax-M2.7-highspeed aliases bypass this — they keep
# their original resolve_model() mapping.
#
# Override with MINIMAX_TASK_DEFAULT (env var) for ad-hoc swaps.
# Format: "code=m3;bulk=m27hs" (semicolon-delimited key=value pairs).
# Unknown task type → m27hs (cheap default).
# ---------------------------------------------------------------------------

_TASK_DEFAULT: dict[str, str] = {
    "search":    "m27hs",  # M2.7-highspeed: 100 TPS, cheap
    "classify":  "m27hs",
    "summarize": "m3",     # 1M context — only M3 has it
    "code":      "m3",     # Thinkwright: M3 ties GLM-5.2 here
    "plan":      "m3",     # architecture needs reasoning
    "bulk":      "m27hs",  # cost-sensitive
    "tool":      "m3",     # native tool call
}
_ENV_DEFAULT = os.environ.get("MINIMAX_TASK_DEFAULT", "")
for _kv in _ENV_DEFAULT.split(";"):
    if "=" in _kv:
        _k, _v = _kv.split("=", 1)
        _TASK_DEFAULT[_k.strip()] = _v.strip()


def resolve_auto(task: str) -> str:
    """Pick the right MiniMax model for a task type. task ∈ {search, classify,
    summarize, code, plan, bulk, tool}. Honors MINIMAX_TASK_DEFAULT override.
    Unknown task → m27hs (cheap default). Returns the full model id, ready
    to be passed to the API.
    """
    short = _TASK_DEFAULT.get(task, "m27hs")
    return resolve_model(short)
