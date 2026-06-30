"""Per-model configuration loader.

Maps model short names (used by adapters and CLI flags) to config dicts
that control sampling, message processing, turn limits, and prompt behavior.

Configs are loaded from YAML files under models/configs/<slug>/:
  - model.yaml  — adapter, model_id, aliases, pricing
  - profiles.yaml — sampling profiles (default, coding, fast, …)

Fallback defaults come from models/configs/_base/profiles.yaml.

Usage:
    from models import get_model_config, clean_output

    cfg = get_model_config("glm")
    text = clean_output("some <think>inner thought</think> text", cfg)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal: build the registry at import time
# ---------------------------------------------------------------------------

_CONFIGS_DIR = Path(__file__).parent / "configs"

# Compiled regex for stripping <think>...</think> blocks.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Shallow-merge override into a copy of base; nested dicts are merged one level deep."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = {**result[k], **v}
        else:
            result[k] = v
    return result


def _build_registry() -> tuple[dict[str, dict], dict[str, str]]:
    """Scan configs/ and return (slug→flat_config, alias→slug)."""
    # Load base profile defaults
    base_profiles_path = _CONFIGS_DIR / "_base" / "profiles.yaml"
    base_profiles: dict = _load_yaml(base_profiles_path) if base_profiles_path.exists() else {}
    base_default: dict = base_profiles.get("default", {})

    slug_configs: dict[str, dict] = {}
    alias_map: dict[str, str] = {}

    for slug_dir in sorted(_CONFIGS_DIR.iterdir()):
        if not slug_dir.is_dir():
            continue
        slug = slug_dir.name
        if slug == "_base":
            continue

        model_yaml_path = slug_dir / "model.yaml"
        if not model_yaml_path.exists():
            continue

        model_yaml = _load_yaml(model_yaml_path)

        # Load model-specific profiles (or empty if absent)
        profiles_path = slug_dir / "profiles.yaml"
        model_profiles: dict = _load_yaml(profiles_path) if profiles_path.exists() else {}

        # Merge: base_default ← model default profile
        model_default = model_profiles.get("default", {})
        profile = _deep_merge(base_default, model_default)

        # Extract pricing
        pricing = model_yaml.get("pricing", {})
        price_in = pricing.get("input_per_mtok", base_default.get("price_in", 1.40))
        price_out = pricing.get("output_per_mtok", base_default.get("price_out", 4.40))

        # Build the flat config dict callers expect
        flat: dict[str, Any] = {
            # model identity
            "adapter": model_yaml.get("adapter", "openrouter"),
            "model_id": model_yaml.get("model_id", slug),
            # pricing
            "price_in": price_in,
            "price_out": price_out,
            # sampling & behaviour from profile
            "temperature": profile.get("temperature", 0.7),
            "top_p": profile.get("top_p", 1.0),
            "max_tokens": profile.get("max_tokens", None),
            "max_turns": profile.get("max_turns", {}),
            "strip_think_tags": profile.get("strip_think_tags", False),
            "system_prompt_suffix": profile.get("system_prompt_suffix", ""),
            "checkpoint_interval": profile.get("checkpoint_interval", 5),
            "budget_warning_turns": profile.get("budget_warning_turns", 7),
            "budget_critical_turns": profile.get("budget_critical_turns", 3),
            # pass through full model.yaml for adapters that need extras
            "_model_yaml": model_yaml,
            "_profiles": model_profiles,
        }

        slug_configs[slug] = flat

        # Register slug itself as an alias — slug self-registration always wins
        alias_map[slug] = slug

        # Register all declared aliases; skip if another slug already owns it
        # (slug self-registration has priority over foreign alias claims)
        for alias in model_yaml.get("aliases", []):
            alias_str = str(alias)
            if alias_str in alias_map and alias_map[alias_str] != slug:
                existing_slug = alias_map[alias_str]
                if existing_slug == alias_str:
                    # The alias IS that slug's own name — don't steal it
                    log.warning(
                        "Slug %r tried to claim alias %r, but that's slug %r's own name — skipping",
                        slug, alias_str, existing_slug,
                    )
                    continue
            alias_map[alias_str] = slug

    return slug_configs, alias_map


_SLUG_CONFIGS, _ALIAS_MAP = _build_registry()

# Determine the default slug (fallback when model is unknown)
_DEFAULT_SLUG = "default" if "default" in _SLUG_CONFIGS else (
    next(iter(_SLUG_CONFIGS), None)
)

log.debug(
    "models: loaded %d slugs, %d aliases; default=%s",
    len(_SLUG_CONFIGS), len(_ALIAS_MAP), _DEFAULT_SLUG,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_model_config(model: str) -> dict[str, Any]:
    """Return the config dict for *model*.

    Resolution order:
    1. Exact alias match (including slug name itself).
    2. Falls back to the 'default' slug if the model is unknown.
    """
    slug = _ALIAS_MAP.get(model)
    if slug is None:
        log.warning("No config for model %r, using default", model)
        slug = _DEFAULT_SLUG

    cfg = _SLUG_CONFIGS.get(slug)
    if cfg is None:
        # Should never happen if _DEFAULT_SLUG is valid, but be defensive
        log.error("Slug %r not found in registry; returning empty config", slug)
        return {}

    # Return a shallow copy (callers may mutate)
    return dict(cfg)


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
            log.info(
                "Stripped <think> tags from output (%d -> %d chars)",
                len(text), len(cleaned),
            )
        return cleaned

    return text
