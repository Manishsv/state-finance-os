"""Tests for financeos.os.llm.config — provider preset resolution and env-var loading."""
from __future__ import annotations

import os

import pytest

from financeos.os.llm.config import (
    ALL_PROVIDERS,
    PROVIDER_PRESETS,
    LLMConfig,
    load_config,
)


@pytest.fixture
def clean_env(monkeypatch):
    """Clear all LLM_* and provider-specific env vars before each test."""
    for k in list(os.environ.keys()):
        if k.startswith("LLM_") or k in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}:
            monkeypatch.delenv(k, raising=False)


def test_default_provider_is_anthropic(clean_env):
    cfg = load_config()
    assert cfg.provider == "anthropic"
    assert cfg.model == PROVIDER_PRESETS["anthropic"]["default_model"]


def test_provider_can_be_overridden_by_env(clean_env, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    cfg = load_config()
    assert cfg.provider == "openai"
    assert cfg.base_url == PROVIDER_PRESETS["openai"]["base_url"]


def test_anthropic_picks_up_anthropic_api_key_env(clean_env, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test123")
    cfg = load_config()
    assert cfg.api_key == "sk-ant-test123"


def test_llm_api_key_overrides_provider_specific(clean_env, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-llm-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key")
    cfg = load_config()
    assert cfg.api_key == "sk-llm-key"


def test_overrides_dict_wins_over_env(clean_env, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "from-env")
    cfg = load_config(overrides={"model": "from-override"})
    assert cfg.model == "from-override"


def test_ollama_provides_dummy_key_without_env(clean_env, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    cfg = load_config()
    assert cfg.api_key == "ollama"
    assert cfg.base_url.startswith("http://localhost")


def test_unknown_provider_falls_back_to_custom(clean_env, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "made-up-provider")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "xyz")
    monkeypatch.setenv("LLM_MODEL", "weird-model")
    cfg = load_config()
    # provider name is preserved, but resolution uses custom preset
    assert cfg.provider == "made-up-provider"
    assert cfg.base_url == "https://example.com/v1"
    assert cfg.api_key == "xyz"
    assert cfg.model == "weird-model"


def test_max_tokens_and_temperature_from_env(clean_env, monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "2048")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.7")
    cfg = load_config()
    assert cfg.max_tokens == 2048
    assert cfg.temperature == 0.7


def test_all_presets_have_required_fields():
    """Every preset must declare base_url + default_model + label, plus either
    api_key or api_key_env so load_config can resolve a key."""
    for name, preset in PROVIDER_PRESETS.items():
        assert "base_url" in preset, f"{name} missing base_url"
        assert "default_model" in preset, f"{name} missing default_model"
        assert "label" in preset, f"{name} missing label"
        assert "api_key" in preset or "api_key_env" in preset, \
            f"{name} missing both api_key and api_key_env"


def test_all_providers_list_matches_presets():
    assert set(ALL_PROVIDERS) == set(PROVIDER_PRESETS.keys())
