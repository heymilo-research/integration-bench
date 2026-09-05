"""Provider routing unit tests (no network)."""

from __future__ import annotations

import pytest

from bench.providers import ProviderError, resolve_provider


def test_sonnet_alias_routes_anthropic():
    spec, model = resolve_provider("sonnet")
    assert spec.name == "anthropic"
    assert "claude" in model


def test_gpt_routes_openai():
    spec, model = resolve_provider("gpt-4.1")
    assert spec.name == "openai"
    assert model == "gpt-4.1"


def test_deepseek_routes():
    spec, model = resolve_provider("deepseek-chat")
    assert spec.name == "deepseek"


def test_openrouter_prefix():
    spec, model = resolve_provider("openrouter/anthropic/claude-sonnet-4")
    assert spec.name == "openrouter"
    assert model == "anthropic/claude-sonnet-4"


def test_unknown_model_errors():
    with pytest.raises(ProviderError):
        resolve_provider("totally-unknown-model-xyz")
