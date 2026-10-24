"""Агентный цикл: план → действие → наблюдение → … → финальный ответ."""

from __future__ import annotations

import json
import logging
import uuid

from jarvis.config import AgentConfig
from jarvis.core.llm.base import LLMError, LLMProvider, LLMResponse
from jarvis.core.prompts import build_system_prompt, wrap_untrusted
from jarvis.core.tools.base import Confirmer
from jarvis.core.tools.registry import ToolRegistry
from jarvis.memory.store import MemoryStore

log = logging.getLogger("jarvis.agent")

# Обрезка tool-результатов при воспроизведении истории: экономия контекста.
_TOOL_REPLAY_LIMIT = 4000


class Agent:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        memory: MemoryStore,
        config: AgentConfig,
        confirmer: Confirmer | None = None,
    ):
        self._llm = llm
        self._registry = registry
        self._memory = memory
        self._cfg = config
        self._confirmer = confirmer

    async def handle(self, user_text: str, session_id: str = "cli-main") -> str:
        """Обрабатывает один запрос пользователя, возвращает финальный ответ."""
        history = await self._memory.recent_dialog(session_id, self._cfg.recent_messages)
        await self._memory.append_dialog(session_id, "user", user_text)

        facts = await self._memory.search(user_text, top_k=self._cfg.recall_top_k)
        messages: list[dict] = [
            {"role": "system", "content": build_system_prompt(facts)},
            *history,
            {"role": "user", "content": user_text},
        ]

        try:
            answer = await self._run_loop(messages, session_id)
        except LLMError as e:
            log.error("Ошибка модели: %s", e)
            return f"[Ошибка модели] {e}"

        # Диалог пишется здесь один раз — все ветки _run_loop возвращают текст.
        await self._memory.append_dialog(session_id, "assistant", answer)
        return answer

    async def _run_loop(self, messages: list[dict], session_id: str) -> str:
        tools = self._registry.schemas()
        for _step in range(self._cfg.max_steps):
            response = await self._llm.complete(
                messages,
                tools,
                temperature=self._cfg.temperature,
                max_tokens=self._cfg.max_tokens,
            )

            if not response.tool_calls:
                text = response.text or ""
                if not text.strip():
                    # Модель вернула пустой ответ — просим сформулировать явно.
                    messages.append(
                        {"role": "user", "content": "Дай финальный текстовый ответ без вызова инструментов."}
                    )
                    continue
                messages.append({"role": "assistant", "content": text})
                return text

            messages.append(self._assistant_tool_message(response))
            await self._memory.append_dialog(
                session_id,
                "assistant",
                response.text or "",
                tool_calls=[
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            )

            for call in response.tool_calls:
                result = await self._registry.execute(call.name, call.arguments, self._confirmer)
                content = (
                    wrap_untrusted(result.output, call.name)
                    if result.untrusted
                    else result.output
                )
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
                await self._memory.append_dialog(
                    session_id, "tool", result.output, tool_call_id=call.id
                )

        # Лимит шагов исчерпан — заставляем модель подвести итог без инструментов.
        log.info("Достигнут лимит шагов (%s)", self._cfg.max_steps)
        messages.append(
            {
                "role": "user",
                "content": "Лимит шагов исчерпан. Сформулируй итоговый ответ пользователю на основе уже полученной информации, без новых вызовов инструментов.",
            }
        )
        response = await self._llm.complete(
            messages, [], temperature=self._cfg.temperature, max_tokens=self._cfg.max_tokens
        )
        return response.text or "Не удалось получить финальный ответ (лимит шагов)."

    @staticmethod
    def _assistant_tool_message(response: LLMResponse) -> dict:
        return {
            "role": "assistant",
            "content": response.text,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in response.tool_calls
            ],
        }

    @staticmethod
    def new_session_id() -> str:
        return f"cli-{uuid.uuid4().hex[:8]}"
