"""Стиль пользователя и черновики сообщений от его имени.

Примеры стиля (few-shot) хранятся в БД и пополняются вручную или из истории
мессенджеров. draft_reply генерирует черновик — и только черновик: отправка
идёт отдельным инструментом telegram_send_message с подтверждением.
"""

from __future__ import annotations

import logging

from jarvis.core.llm.base import LLMProvider
from jarvis.core.tools.base import Tool, ToolResult
from jarvis.memory.store import MemoryStore

log = logging.getLogger("jarvis.tools.style")

_DRAFT_SYSTEM = """Ты помогаешь пользователю составить черновик ответа в мессенджере.
Пиши от первого лица, ТОЧНО в стиле пользователя по примерам ниже: той же длины,
тона, знаком препинания и типичных оборотов. Пиши по-русски.
Ответь ТОЛЬКО текстом черновика, без пояснений и кавычек.
Если примеров стиля мало — пиши нейтрально-вежливо и коротко."""


def create_tools(memory: MemoryStore, llm: LLMProvider | None) -> list[Tool]:
    async def add_style_example(args: dict) -> ToolResult:
        text = str(args.get("text", "")).strip()
        if not text:
            return ToolResult(output="Ошибка: пустой пример стиля.")
        example_id = await memory.add_style_example(text, source=str(args.get("source", "manual")))
        return ToolResult(output=f"Пример стиля сохранён (id={example_id}). Теперь черновики будут ближе к вашей манере.")

    async def draft_reply(args: dict) -> ToolResult:
        incoming = str(args.get("incoming_message", "")).strip()
        if not incoming:
            return ToolResult(output="Ошибка: передай текст входящего сообщения.")
        context = str(args.get("context", "")).strip()
        if llm is None:
            return ToolResult(output="Ошибка: модель недоступна, черновик не собрать.")

        examples = await memory.list_style_examples(limit=15)
        example_block = "\n\n".join(f"Пример {i}: {e['text'][:400]}" for i, e in enumerate(reversed(examples), 1))
        user_prompt = ""
        if example_block:
            user_prompt += f"Примеры сообщений пользователя (его стиль):\n{example_block}\n\n"
        user_prompt += f"Входящее сообщение, на которое нужно ответить:\n{incoming}"
        if context:
            user_prompt += f"\n\nКонтекст диалога: {context[:1000]}"

        try:
            response = await llm.complete(
                [{"role": "system", "content": _DRAFT_SYSTEM}, {"role": "user", "content": user_prompt}],
                [],
                temperature=0.5,
                max_tokens=400,
            )
        except Exception as e:
            return ToolResult(output=f"Ошибка модели при составлении черновика: {e}")
        draft = (response.text or "").strip()
        if not draft:
            return ToolResult(output="Модель вернула пустой черновик, попробуй ещё раз.")
        return ToolResult(
            output=(
                f"Черновик ответа (в стиле пользователя, НЕ отправлен):\n\n{draft}\n\n"
                "Для отправки подтверди действие или скопируй текст вручную."
            )
        )

    return [
        Tool(
            name="add_style_example",
            description=(
                "Сохранить пример сообщения пользователя для обучения стилю черновиков "
                "(его типичная фраза из переписки). Добавляй, когда пользователь диктует "
                "или пересылает своё сообщение."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Сообщение пользователя дословно"},
                    "source": {"type": "string", "description": "Откуда: manual | telegram_history"},
                },
                "required": ["text"],
            },
            handler=add_style_example,
            side_effect="write",
        ),
        Tool(
            name="draft_reply",
            description=(
                "Составить черновик ответа от имени пользователя в его стиле (по сохранённым "
                "примерам). Черновик НЕ отправляется — только текст для просмотра."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "incoming_message": {"type": "string", "description": "Сообщение, на которое отвечаем"},
                    "context": {"type": "string", "description": "Чем закончился предыдущий диалог (необязательно)"},
                },
                "required": ["incoming_message"],
            },
            handler=draft_reply,
            side_effect="write",
            timeout=120.0,
        ),
    ]
