"""Provider-agnostic LLM access for FinanceOS apps.

All app-level LLM calls go through this kernel module — no provider SDK
should be imported directly from anywhere else in the codebase. Switching
between Anthropic, OpenAI, Ollama, Groq, OpenRouter, Together, LM Studio,
or a custom OpenAI-compatible endpoint is a `.env` change, not a code change.

Mirrors the AirOS pattern at `airos/agents/llm_client.py` + `llm_config.py`.
"""
from financeos.os.llm.client import (
    LLMClient,
    LLMResponse,
    ToolCall,
    assistant_msg,
    make_parameters,
    make_tool,
    system_msg,
    tool_result_msg,
    user_msg,
)
from financeos.os.llm.config import (
    ALL_PROVIDERS,
    LLMConfig,
    PROVIDER_PRESETS,
    load_config,
)

__all__ = [
    "LLMClient", "LLMResponse", "ToolCall",
    "user_msg", "system_msg", "assistant_msg", "tool_result_msg",
    "make_tool", "make_parameters",
    "LLMConfig", "load_config", "PROVIDER_PRESETS", "ALL_PROVIDERS",
]
