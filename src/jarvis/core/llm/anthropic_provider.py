"""Провайдер Anthropic (Claude) Messages API через httpx.

Конвертирует внутренний OpenAI-подобный формат в content-блоки Anthropic.
Внимание: провайдер написан по спецификации API, но в MVP живыми ключами не
тестировался (в проекте нет ключа Anthropic) — при подключении проверьте.
"""

from __future__ import annotations

import json
import logging

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from jarvis.core.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall, ToolSchema

log = logging.getLogger("jarvis.llm.anthropic")


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, default_model: str, base_url: str = "https://api.anthropic.com", timeout: float = 120.0):
        if not api_key:
            raise LLMError("Не задан JARVIS_LLM_API_KEY для провайдера anthropic.")
        if not default_model:
            raise LLMError("Не задана модель JARVIS_LLM_MODEL (например, claude-sonnet-4-5).")
        self.default_model = default_model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30), reraise=True)
    async def _post(self, payload: dict) -> dict:
        resp = await self._client.post("/v1/messages", json=payload)
        if resp.status_code == 401:
            raise LLMError("Anthropic вернул 401: неверный ключ.")
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
        system_text, converted = _convert_messages(messages)
        payload: dict = {
            "model": model or self.default_model,
            "messages": converted,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_text:
            payload["system"] = system_text
        if tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters or {"type": "object", "properties": {}},
                }
                for t in tools
            ]
        try:
            data = await self._post(payload)
        except LLMError:
            raise
        except httpx.HTTPStatusError as e:
            raise LLMError(f"Anthropic вернул {e.response.status_code}: {e.response.text[:300]}") from e
        except Exception as e:
            raise LLMError(f"Сетевая ошибка обращения к Anthropic: {e}") from e

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block["id"],
                        name=block["name"],
                        arguments=block.get("input") or {},
                    )
                )
        return LLMResponse(
            text="\n".join(text_parts) or None,
            tool_calls=tool_calls,
            stop_reason=data.get("stop_reason", ""),
            usage=data.get("usage") or {},
        )

    async def close(self) -> None:
        await self._client.aclose()


def _convert_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Внутренний формат → формат Anthropic (system отдельно, tool_result в user)."""
    system_parts: list[str] = []
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
        elif role == "user":
            out.append({"role": "user", "content": m.get("content") or ""})
        elif role == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append(
                    {"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": args}
                )
            out.append({"role": "assistant", "content": blocks or ""})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id"),
                "content": [{"type": "text", "text": m.get("content") or ""}],
            }
            # Последовательные tool-сообщения склеиваем в один user-ход.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return "\n\n".join(system_parts), out
