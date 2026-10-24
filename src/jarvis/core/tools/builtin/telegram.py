"""Инструменты Telegram (только чтение): непрочитанные, последние сообщения, поиск."""

from __future__ import annotations

from jarvis.connectors.base import Connector, ConnectorError
from jarvis.core.tools.base import Tool, ToolResult


def create_tools(connector: Connector) -> list[Tool]:
    async def telegram_unread(args: dict) -> ToolResult:
        try:
            summary = await connector.unread_summary(limit_dialogs=int(args.get("limit_dialogs", 30)))
        except ConnectorError as e:
            return ToolResult(output=f"Ошибка Telegram: {e}")
        return ToolResult(output=summary, untrusted=True)

    async def telegram_recent(args: dict) -> ToolResult:
        chat = str(args.get("chat", "")).strip()
        if not chat:
            return ToolResult(output="Ошибка: укажите чат (имя, @юзернейм или id).")
        try:
            result = await connector.recent_messages(chat, limit=int(args.get("limit", 10)))
        except ConnectorError as e:
            return ToolResult(output=f"Ошибка Telegram: {e}")
        return ToolResult(output=result, untrusted=True)

    async def telegram_search(args: dict) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult(output="Ошибка: пустой запрос.")
        try:
            result = await connector.search_messages(
                query, chat=args.get("chat"), limit=int(args.get("limit", 10))
            )
        except ConnectorError as e:
            return ToolResult(output=f"Ошибка Telegram: {e}")
        return ToolResult(output=result, untrusted=True)

    return [
        Tool(
            name="telegram_unread",
            description="Сводка непрочитанных сообщений в Telegram (диалоги, счётчики, последние сообщения).",
            parameters={
                "type": "object",
                "properties": {"limit_dialogs": {"type": "integer", "description": "Сколько диалогов проверить"}},
            },
            handler=telegram_unread,
            side_effect="read",
            returns_untrusted=True,
            timeout=120.0,
            source="connector:telegram",
        ),
        Tool(
            name="telegram_recent",
            description="Последние сообщения указанного Telegram-чата (личный, группа или канал).",
            parameters={
                "type": "object",
                "properties": {
                    "chat": {"type": "string", "description": "Название диалога, @юзернейм или id"},
                    "limit": {"type": "integer", "description": "Сколько сообщений (1-50)"},
                },
                "required": ["chat"],
            },
            handler=telegram_recent,
            side_effect="read",
            returns_untrusted=True,
            timeout=120.0,
            source="connector:telegram",
        ),
        Tool(
            name="telegram_search",
            description="Поиск по истории Telegram-сообщений: в указанном чате или по последним диалогам.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый текст"},
                    "chat": {"type": "string", "description": "Чат (необязательно; без него — по всем диалогам)"},
                    "limit": {"type": "integer", "description": "Сколько результатов (1-30)"},
                },
                "required": ["query"],
            },
            handler=telegram_search,
            side_effect="read",
            returns_untrusted=True,
            timeout=180.0,
            source="connector:telegram",
        ),
    ]
