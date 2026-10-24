"""Фабрика провайдеров модели: выбор по JARVIS_LLM_PROVIDER."""

from __future__ import annotations

from jarvis.config import Settings
from jarvis.core.llm.base import LLMError, LLMProvider


def create_provider(settings: Settings) -> LLMProvider:
    """Создаёт провайдер по настройкам. Ollama и другие OpenAI-совместимые
    endpoint'ы покрываются провайдером openai_compat (достаточно base_url)."""
    name = settings.llm_provider.lower()
    if name in ("openai_compat", "openai", "openrouter", "groq", "ollama"):
        from jarvis.core.llm.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            default_model=settings.llm_model,
            timeout=settings.llm_timeout,
        )
    if name == "anthropic":
        from jarvis.core.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.llm_api_key,
            default_model=settings.llm_model,
            timeout=settings.llm_timeout,
        )
    raise LLMError(
        f"Неизвестный провайдер {settings.llm_provider!r}. "
        "Доступны: openai_compat, anthropic."
    )
