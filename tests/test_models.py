"""Tests for models/__init__.py — config loading, alias resolution, clean_output."""
from __future__ import annotations

import pytest
from models import clean_output, get_model_config


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestGetModelConfig:
    def test_known_slug_returns_dict(self):
        cfg = get_model_config("glm")
        assert isinstance(cfg, dict)
        assert cfg["adapter"] == "zai"
        assert cfg["model_id"] == "glm-5.2"

    def test_alias_resolves_to_slug(self):
        # "glm-5.2" is an alias for the "glm" slug
        cfg_slug = get_model_config("glm")
        cfg_alias = get_model_config("glm-5.2")
        assert cfg_slug["model_id"] == cfg_alias["model_id"]

    def test_unknown_model_returns_default(self):
        cfg = get_model_config("totally-unknown-model-xyz")
        # Should return something (not crash), using the default slug
        assert isinstance(cfg, dict)

    def test_sonnet_config(self):
        cfg = get_model_config("sonnet")
        assert isinstance(cfg, dict)
        assert "price_in" in cfg
        assert "price_out" in cfg
        assert "temperature" in cfg

    def test_haiku_config(self):
        cfg = get_model_config("haiku")
        assert isinstance(cfg, dict)
        assert "max_tokens" in cfg

    def test_returned_dict_is_copy(self):
        """Mutating the returned dict must not affect subsequent calls."""
        cfg1 = get_model_config("glm")
        cfg1["temperature"] = 9999
        cfg2 = get_model_config("glm")
        assert cfg2["temperature"] != 9999

    def test_required_keys_present(self):
        for slug in ("glm", "haiku", "sonnet", "opus"):
            cfg = get_model_config(slug)
            for key in ("adapter", "model_id", "price_in", "price_out",
                        "temperature", "max_tokens", "strip_think_tags"):
                assert key in cfg, f"missing key {key!r} for slug {slug!r}"

    def test_pricing_is_numeric(self):
        cfg = get_model_config("glm")
        assert isinstance(cfg["price_in"], (int, float))
        assert isinstance(cfg["price_out"], (int, float))
        assert cfg["price_in"] >= 0
        assert cfg["price_out"] >= 0


# ---------------------------------------------------------------------------
# clean_output
# ---------------------------------------------------------------------------

class TestCleanOutput:
    def test_strips_think_tags_when_enabled(self):
        cfg = {"strip_think_tags": True}
        text = "<think>internal reasoning</think>final answer"
        result = clean_output(text, cfg)
        assert "<think>" not in result
        assert "final answer" in result

    def test_does_not_strip_when_disabled(self):
        cfg = {"strip_think_tags": False}
        text = "<think>internal reasoning</think>final answer"
        result = clean_output(text, cfg)
        assert "<think>" in result

    def test_empty_string_returns_empty(self):
        cfg = {"strip_think_tags": True}
        assert clean_output("", cfg) == ""

    def test_multiline_think_block_stripped(self):
        cfg = {"strip_think_tags": True}
        text = "<think>\nline1\nline2\n</think>answer"
        result = clean_output(text, cfg)
        assert "line1" not in result
        assert "answer" in result

    def test_multiple_think_blocks_stripped(self):
        cfg = {"strip_think_tags": True}
        text = "<think>a</think>mid<think>b</think>end"
        result = clean_output(text, cfg)
        assert "a" not in result
        assert "b" not in result
        assert "mid" in result
        assert "end" in result

    def test_no_think_tags_unchanged(self):
        cfg = {"strip_think_tags": True}
        text = "plain output with no tags"
        assert clean_output(text, cfg) == text
