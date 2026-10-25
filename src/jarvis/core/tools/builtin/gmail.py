"""Инструменты Gmail (IMAP, только чтение): непрочитанные, поиск, открыть письмо."""

from __future__ import annotations

from jarvis.connectors.base import ConnectorError
from jarvis.connectors.gmail_imap import GmailImapConnector
from jarvis.core.tools.base import Tool, ToolResult


def create_tools(connector: GmailImapConnector) -> list[Tool]:
    async def gmail_unread(args: dict) -> ToolResult:
        try:
            result = await connector.unread_summary(limit_dialogs=int(args.get("limit", 20)))
        except ConnectorError as e:
            return ToolResult(output=f"Ошибка Gmail: {e}")
        return ToolResult(output=result, untrusted=True)

    async def gmail_search(args: dict) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult(output="Ошибка: пустой запрос.")
        try:
            result = await connector.search_messages(query, limit=int(args.get("limit", 10)))
        except ConnectorError as e:
            return ToolResult(output=f"Ошибка Gmail: {e}")
        return ToolResult(output=result, untrusted=True)

    async def gmail_read(args: dict) -> ToolResult:
        uid = str(args.get("uid", "")).strip()
        if not uid:
            return ToolResult(output="Ошибка: укажи uid письма из результатов gmail_search/gmail_unread.")
        try:
            result = await connector.read_message(uid)
        except ConnectorError as e:
            return ToolResult(output=f"Ошибка Gmail: {e}")
        return ToolResult(output=result, untrusted=True)

    return [
        Tool(
            name="gmail_unread",
            description="Непрочитанные письма Gmail: отправитель, тема, кусок текста и uid.",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "Сколько писем (1-30)"}},
            },
            handler=gmail_unread,
            side_effect="read",
            returns_untrusted=True,
            timeout=60.0,
            source="connector:gmail",
        ),
        Tool(
            name="gmail_search",
            description="Поиск по письмам Gmail (по слову; поддерживает FROM:…, SUBJECT:…).",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Что искать"},
                    "limit": {"type": "integer", "description": "Сколько результатов"},
                },
                "required": ["query"],
            },
            handler=gmail_search,
            side_effect="read",
            returns_untrusted=True,
            timeout=60.0,
            source="connector:gmail",
        ),
        Tool(
            name="gmail_read",
            description="Прочитать письмо Gmail целиком по uid из результатов поиска.",
            parameters={
                "type": "object",
                "properties": {"uid": {"type": "string", "description": "uid письма"}},
                "required": ["uid"],
            },
            handler=gmail_read,
            side_effect="read",
            returns_untrusted=True,
            timeout=60.0,
            source="connector:gmail",
        ),
    ]
