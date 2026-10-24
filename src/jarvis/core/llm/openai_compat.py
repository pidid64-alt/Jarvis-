"""OpenAI-совместимый провайдер: OpenRouter, Groq, OpenAI, Ollama, vLLM, LiteLLM…

Работает напрямую через httpx (без SDK): протокол /chat/completions прост и
стабилен, а нам нужен полный контроль над ретраями и ошибками.
"""

from __future__ import annotations

import json
import logging

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from jarvis.core.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall, ToolSchema

log = logging.getLogger("jarvis.llm.openai_compat")

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str, default_model: str, timeout: float = 120.0):
        if not api_key:
            raise LLMError(
                "Не задан JARVIS_LLM_API_KEY. Скопируйте .env.example в .env и укажите ключ."
            )
        if not default_model:
            raise LLMError("Не задана модель JARVIS_LLM_MODEL (см. .env.example).")
        headers = {"Authorization": f"Bearer {api_key}"}
        # Дополнительные заголовки OpenRouter (другим провайдерам не мешают).
        if "openrouter" in base_url:
            headers["HTTP-Referer"] = "https://local.jarvis.assistant"
            headers["X-Title"] = "Jarvis personal assistant"
        self.default_model = default_model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(timeout),
        )

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    async def _post(self, payload: dict) -> dict:
        resp = await self._client.post("/chat/completions", json=payload)
        if resp.status_code == 401:
            raise LLMError("API вернул 401: ключ неверён или истёк (JARVIS_LLM_API_KEY).")
        if resp.status_code == 404 and "model" in resp.text:
            raise LLMError(
                f"Модель не найдена (404). Проверьте JARVIS_LLM_MODEL={self.default_model!r}."
            )
        resp.raise_for_status()
        return resp.json()

    async def complete(
        self,
        messages: list[dict],
        tools: list[ToolSchema],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        payload: dict = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = "auto"
        try:
            data = await self._post(payload)
        except LLMError:
            raise
        except httpx.HTTPStatusError as e:
            raise LLMError(f"API вернул {e.response.status_code}: {e.response.text[:300]}") from e
        except Exception as e:
            raise LLMError(f"Сетевая ошибка обращения к модели: {e}") from e

        try:
            choice = data["choices"][0]
            msg = choice.get("message") or {}
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=_parse_arguments(tc["function"].get("arguments")),
                )
                for tc in msg.get("tool_calls") or []
            ]
            return LLMResponse(
                text=msg.get("content"),
                tool_calls=tool_calls,
                stop_reason=choice.get("finish_reason", ""),
                usage=data.get("usage") or {},
            )
        except (KeyError, IndexError, TypeError) as e:
            log.error("Неожиданный формат ответа: %s", data)
            raise LLMError(f"Неожиданный формат ответа модели: {e}") from e

    async def close(self) -> None:
        await self._client.aclose()


def _parse_arguments(raw) -> dict:
    """Аргументы вызова приходят JSON-строкой; терпим мусор."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        log.warning("Модель вернула некорректный JSON аргументов: %.200s", raw)
        return {}
