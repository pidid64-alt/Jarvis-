"""Инструменты долгосрочной памяти: сохранить факт, найти факты."""

from __future__ import annotations

from jarvis.core.tools.base import Tool, ToolResult
from jarvis.memory.store import MemoryStore


def create_tools(memory: MemoryStore) -> list[Tool]:
    async def memory_save(args: dict) -> ToolResult:
        text = str(args.get("text", "")).strip()
        if not text:
            return ToolResult(output="Ошибка: пустой текст факта.")
        kind = str(args.get("kind", "fact"))
        fact_id = await memory.save_fact(text, kind=kind)
        return ToolResult(output=f"Сохранено в память (id={fact_id}, вид={kind}).")

    async def memory_search(args: dict) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult(output="Ошибка: пустой запрос.")
        hits = await memory.search(query, top_k=int(args.get("limit", 5)))
        if not hits:
            return ToolResult(output="В памяти ничего не найдено.")
        lines = [f"[id={h['id']}] ({h.get('kind', 'fact')}) {h['text']}" for h in hits]
        return ToolResult(output="Найдено в памяти:\n" + "\n".join(lines))

    return [
        Tool(
            name="memory_save",
            description=(
                "Сохранить факт в долгосрочную память: сведения о пользователе, контактах, "
                "задачах, предпочтениях. Виды: fact | contact | task | note."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Факт одним предложением"},
                    "kind": {"type": "string", "enum": ["fact", "contact", "task", "note"]},
                },
                "required": ["text"],
            },
            handler=memory_save,
            side_effect="write",
        ),
        Tool(
            name="memory_search",
            description="Поиск по долгосрочной памяти (факты, контакты, задачи).",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос"},
                    "limit": {"type": "integer", "description": "Максимум результатов (1-10)"},
                },
                "required": ["query"],
            },
            handler=memory_search,
            side_effect="read",
        ),
    ]
