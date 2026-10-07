"""LLM-провайдер: любой OpenAI-совместимый сервер (OmniRoute, OpenRouter, Ollama).

Ключ берётся из окружения по ссылке из конфига (``${JARVIS_LLM_KEY}``).
Значение ключа не логируется и не возвращается наружу.
"""

from __future__ import annotations

from typing import Any

from ..core import secrets
from ..core.errors import (
    ProviderError,
    ProviderUnavailableError,
    SecretMissingError,
)
from ..core.logging_setup import get_logger
from .http import CircuitBreaker, HttpClient

log = get_logger("providers.llm")


class LLMProvider:
    """Chat completion по протоколу OpenAI."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 8.0,
                 retries: int = 1, enabled: bool = True, max_tokens: int = 400,
                 temperature: float = 0.2, breaker: CircuitBreaker | None = None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.enabled = enabled
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = HttpClient(timeout=timeout, retries=retries,
                                 breaker=breaker or CircuitBreaker("llm", threshold=3, cooldown=60.0))

    # ------------------------------------------------------------------ state
    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.model)

    def available(self) -> tuple[bool, str]:
        """(готов, причина). Причину показывает doctor/GUI, ключ не раскрывается."""
        if not self.enabled:
            return False, "LLM выключен в настройках"
        if not self.base_url:
            return False, "не задан адрес LLM (llm.base_url)"
        if not self.model:
            return False, "не задана модель (llm.model)"
        if not self.api_key:
            return False, "не задан ключ (переменная окружения или .env)"
        if not self.client.breaker.allow():
            return False, f"временно отключён после сбоев: {self.client.breaker.last_error}"
        return True, "готов"

    # ------------------------------------------------------------------- call
    def chat(self, messages: list[dict[str, str]], *, max_tokens: int | None = None,
             temperature: float | None = None) -> str:
        """Ответ модели в формате JSON (так работает планировщик действий)."""
        return self._complete(messages, max_tokens=max_tokens, temperature=temperature, json_mode=True)

    def chat_text(self, messages: list[dict[str, str]], *, max_tokens: int | None = None,
                  temperature: float | None = None) -> str:
        """Обычный текстовый ответ — для пересказа результатов поиска."""
        return self._complete(messages, max_tokens=max_tokens, temperature=temperature, json_mode=False)

    def _complete(self, messages: list[dict[str, str]], *, max_tokens: int | None,
                  temperature: float | None, json_mode: bool) -> str:
        ready, reason = self.available()
        if not ready:
            # Проверяем точную формулировку, а не подстроку: слово «отключён»
            # тоже содержит «ключ», и раньше это давало неверную ошибку.
            if reason.startswith("не задан ключ"):
                raise SecretMissingError(reason)
            if reason.startswith("временно отключён"):
                raise ProviderUnavailableError(reason)
            raise ProviderError(reason)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        response = self.client.post_json(f"{self.base_url}/chat/completions", payload, headers=headers)
        data = response.json()
        choices = (data or {}).get("choices") or []
        if not choices:
            raise ProviderError("модель вернула пустой список вариантов")
        content = (choices[0].get("message") or {}).get("content") or ""
        if not content:
            raise ProviderError("модель вернула пустой ответ")
        usage = (data or {}).get("usage") or {}
        log.debug("LLM ответил, токенов: %s", usage.get("total_tokens", "?"))
        return content

    def state(self) -> dict[str, Any]:
        ready, reason = self.available()
        return {
            "provider": "openai-compatible",
            "model": self.model,
            "base_url": self.base_url,
            "key_present": bool(self.api_key),
            "key_masked": secrets.mask_secret(self.api_key),
            "available": ready,
            "reason": reason,
        }
