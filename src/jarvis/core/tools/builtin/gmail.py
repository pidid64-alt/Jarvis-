"""Инструменты Gmail: непрочитанные, поиск, открыть письмо.

Бэкенд выбирается в runtime: Google OAuth (Gmail API) либо IMAP с паролем
приложения — у обоих одинаковые методы unread_summary/search_messages/read_message.
"""

from __future__ import annotations

from jarvis.connectors.base import ConnectorError
from jarvis.core.tools.base import Tool, ToolResult


def create_tools(connector, allow_send: bool = False) -> list[Tool]:
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

    async def gmail_send(args: dict) -> ToolResult:
        to = str(args.get("to", "")).strip()
        subject = str(args.get("subject", "(без темы)")).strip()
        body = str(args.get("body", "")).strip()
        if not to or not body:
            return ToolResult(output="Ошибка: нужны to и body.")
        try:
            result = await connector.send_message(to, subject, body)
        except ConnectorError as e:
            return ToolResult(output=f"Ошибка Gmail: {e}")
        except Exception as e:
            detail = str(e)
            if "insufficient" in detail.lower() or "permission" in detail.lower():
                return ToolResult(
                    output="Нет права отправки: выполните повторный вход `jarvis google login` "
                    "(добавлен скоуп gmail.send)."
                )
            return ToolResult(output=f"Не удалось отправить письмо: {detail[:300]}")
        return ToolResult(output=result)

    tools = [
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

    # Отправка — только когда бэкенд OAuth и режим не read_only.
    # Каждое письмо проходит гейт подтверждения (кнопки в боте).
    if allow_send:
        tools.append(
            Tool(
                name="gmail_send",
                description=(
                    "Отправить письмо с адреса пользователя (Gmail). "
                    "Требует подтверждения пользователем перед отправкой."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Адрес получателя"},
                        "subject": {"type": "string", "description": "Тема письма"},
                        "body": {"type": "string", "description": "Текст письма"},
                    },
                    "required": ["to", "body"],
                },
                handler=gmail_send,
                side_effect="outbound",
                timeout=90.0,
                source="connector:gmail",
            )
        )
    return tools
