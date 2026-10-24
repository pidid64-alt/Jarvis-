"""Базовые типы абстракции модели.

Внутренний формат сообщений совместим с OpenAI Chat Completions:
  {"role": "system"|"user"|"assistant"|"tool", "content": str|None,
   "tool_calls": [{"id", "type": "function", "function": {"name", "arguments": str-JSON}}],
   "tool_call_id": str}
Провайдеры конвертируют его в свой формат (например, Anthropic — в content-блоки).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class LLMError(RuntimeError):
    """Ошибка обращения к модели (ключ, сеть, лимиты, формат ответа)."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)


@dataclass
class ToolSchema:
    """Описание инструмента для модели (нативный function calling)."""

    name: str
    description: str
    parameters: dict  # JSON Schema


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        tools: list[ToolSchema],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """Вызов модели с инструментами. Возвращает текст и/или вызовы инструментов."""

    @abstractmethod
    async def close(self) -> None:
        """Освобождение ресурсов (http-клиенты и т.п.)."""
