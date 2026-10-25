"""Инструменты Google (OAuth): поиск в Drive, чтение Docs и Sheets."""

from __future__ import annotations

from jarvis.connectors.base import ConnectorError
from jarvis.connectors.google_oauth import GoogleOAuthConnector
from jarvis.core.tools.base import Tool, ToolResult


def create_tools(connector: GoogleOAuthConnector) -> list[Tool]:
    async def google_find(args: dict) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult(output="Ошибка: пустой запрос.")
        try:
            result = await connector.drive_search(query, int(args.get("limit", 10)))
        except ConnectorError as e:
            return ToolResult(output=f"Ошибка Google: {e}")
        except Exception as e:
            return ToolResult(output=f"Ошибка Google API: {e}")
        return ToolResult(output=result, untrusted=True)

    async def google_doc(args: dict) -> ToolResult:
        target = str(args.get("name", "")).strip()
        if not target:
            return ToolResult(output="Ошибка: укажи имя документа или ссылку.")
        try:
            result = await connector.read_doc(target)
        except ConnectorError as e:
            return ToolResult(output=f"Ошибка Google: {e}")
        except Exception as e:
            return ToolResult(output=f"Ошибка Google Docs: {e}")
        if len(result) > 12000:
            result = result[:12000] + "\n[…документ обрезан]"
        return ToolResult(output=result, untrusted=True)

    async def google_sheet(args: dict) -> ToolResult:
        target = str(args.get("name", "")).strip()
        if not target:
            return ToolResult(output="Ошибка: укажи имя таблицы или ссылку.")
        sheet_range = str(args.get("range", "A1:Z500"))
        try:
            result = await connector.read_sheet(target, sheet_range)
        except ConnectorError as e:
            return ToolResult(output=f"Ошибка Google: {e}")
        except Exception as e:
            return ToolResult(output=f"Ошибка Google Sheets: {e}")
        if len(result) > 12000:
            result = result[:12000] + "\n[…таблица обрезана]"
        return ToolResult(output=result, untrusted=True)

    return [
        Tool(
            name="google_find_files",
            description="Найти файл в Google Drive по имени (документы, таблицы, PDF…).",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Часть имени файла"},
                    "limit": {"type": "integer", "description": "Сколько результатов (1-20)"},
                },
                "required": ["query"],
            },
            handler=google_find,
            side_effect="read",
            returns_untrusted=True,
            timeout=60.0,
            source="connector:google",
        ),
        Tool(
            name="google_read_doc",
            description="Прочитать документ Google Docs по имени или ссылке (текст с заголовками).",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Имя документа или URL"}},
                "required": ["name"],
            },
            handler=google_doc,
            side_effect="read",
            returns_untrusted=True,
            timeout=90.0,
            source="connector:google",
        ),
        Tool(
            name="google_read_sheet",
            description="Прочитать таблицу Google Sheets по имени или ссылке (диапазон A1:Z500 по умолчанию).",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Имя таблицы или URL"},
                    "range": {"type": "string", "description": "Диапазон, например A1:C20"},
                },
                "required": ["name"],
            },
            handler=google_sheet,
            side_effect="read",
            returns_untrusted=True,
            timeout=90.0,
            source="connector:google",
        ),
    ]
