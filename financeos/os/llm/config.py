"""LLM provider configuration for FinanceOS apps.

All LLM calls go through this config — no provider SDK is imported anywhere
else. Switch providers by changing .env variables, no code changes.

Environment variables
---------------------
LLM_PROVIDER     — Provider preset name (default: anthropic for FinanceOS)
                   One of: anthropic | openai | ollama | groq | together |
                           openrouter | lmstudio | custom
LLM_BASE_URL     — Override the provider's default base URL
LLM_API_KEY      — API key. For Anthropic, ANTHROPIC_API_KEY is also accepted.
LLM_MODEL        — Model name (overrides provider default)
LLM_MAX_TOKENS   — Max tokens for responses (default: 1024 — short briefs)
LLM_TEMPERATURE  — Sampling temperature (default: 0.1 — deterministic for analysis)
LLM_TIMEOUT      — HTTP timeout in seconds (default: 120)

Note on default provider: AirOS defaults to `ollama` (local-first). FinanceOS
defaults to `anthropic` because most users running budget analysis are using a
hosted Claude API rather than a local model. Override with LLM_PROVIDER=ollama
in .env if you want local inference.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------

PROVIDER_PRESETS: dict = {
    "anthropic": {
        "base_url":      "https://api.anthropic.com",
        "api_key_env":   "ANTHROPIC_API_KEY",
        "default_model": "claude-haiku-4-5",
        "label":         "Anthropic (Claude)",
        "notes":         "Native Messages API. Models: claude-haiku-4-5, claude-sonnet-4-6, claude-opus-4-7",
    },
    "ollama": {
        "base_url":      "http://localhost:11434/v1",
        "api_key":       "ollama",
        "default_model": "gpt-oss:20b-cloud",
        "label":         "Ollama (local)",
        "notes":         "Tool-calling models: gpt-oss:20b-cloud, llama3.1, qwen2.5, mistral-nemo. Run: ollama list",
    },
    "openai": {
        "base_url":      "https://api.openai.com/v1",
        "api_key_env":   "LLM_API_KEY",
        "default_model": "gpt-4o-mini",
        "label":         "OpenAI",
        "notes":         "Models: gpt-4o, gpt-4o-mini, gpt-4-turbo",
    },
    "groq": {
        "base_url":      "https://api.groq.com/openai/v1",
        "api_key_env":   "LLM_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
        "label":         "Groq (fast inference)",
        "notes":         "Free tier available. Models: llama-3.3-70b-versatile, mixtral-8x7b-32768",
    },
    "together": {
        "base_url":      "https://api.together.xyz/v1",
        "api_key_env":   "LLM_API_KEY",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "label":         "Together AI",
        "notes":         "Open models at scale.",
    },
    "openrouter": {
        "base_url":      "https://openrouter.ai/api/v1",
        "api_key_env":   "LLM_API_KEY",
        "default_model": "google/gemini-flash-1.5",
        "label":         "OpenRouter (multi-model)",
        "notes":         "200+ models with one key. Supports Claude via anthropic/claude-*, GPT, Gemini, Llama.",
    },
    "lmstudio": {
        "base_url":      "http://localhost:1234/v1",
        "api_key":       "lmstudio",
        "default_model": "local-model",
        "label":         "LM Studio (local)",
        "notes":         "Start LM Studio → Local Server tab → Start Server",
    },
    "custom": {
        "base_url":      "",
        "api_key_env":   "LLM_API_KEY",
        "default_model": "",
        "label":         "Custom (OpenAI-compatible)",
        "notes":         "Any server exposing /v1/chat/completions (vLLM, text-generation-webui, etc.)",
    },
}

ALL_PROVIDERS = list(PROVIDER_PRESETS.keys())


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    provider:    str
    base_url:    str
    api_key:     str
    model:       str
    max_tokens:  int   = 1024
    temperature: float = 0.1
    timeout:     int   = 120

    @property
    def label(self) -> str:
        return PROVIDER_PRESETS.get(self.provider, {}).get("label", self.provider)

    def to_dict(self) -> dict:
        return {
            "provider":    self.provider,
            "base_url":    self.base_url,
            "model":       self.model,
            "max_tokens":  self.max_tokens,
            "temperature": self.temperature,
        }


# ---------------------------------------------------------------------------
# Config loader — env vars → LLMConfig
# ---------------------------------------------------------------------------

def load_config(overrides: Optional[dict] = None) -> LLMConfig:
    """Load LLM config from environment, with optional dict overrides.

    Priority for each field: overrides dict > env var > provider preset default.
    For API keys, also falls back to the provider's standard env var name
    (e.g. ANTHROPIC_API_KEY for the anthropic provider).
    """
    ov = overrides or {}

    provider = (ov.get("provider") or os.environ.get("LLM_PROVIDER", "anthropic")).lower()
    preset   = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])

    base_url = (
        ov.get("base_url")
        or os.environ.get("LLM_BASE_URL")
        or preset["base_url"]
    )

    api_key = (
        ov.get("api_key")
        or os.environ.get("LLM_API_KEY", "")
        or os.environ.get(preset.get("api_key_env", ""), "")
        or preset.get("api_key", "")
    )

    model = (
        ov.get("model")
        or os.environ.get("LLM_MODEL", "")
        or preset["default_model"]
    )

    return LLMConfig(
        provider=provider,
        base_url=base_url,
        api_key=api_key or "no-key",
        model=model,
        max_tokens=int(ov.get("max_tokens") or os.environ.get("LLM_MAX_TOKENS", 1024)),
        temperature=float(ov.get("temperature") or os.environ.get("LLM_TEMPERATURE", 0.1)),
        timeout=int(ov.get("timeout") or os.environ.get("LLM_TIMEOUT", 120)),
    )
