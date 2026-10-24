"""Подставной сервер MCP для тестов: JSON-RPC по строкам через stdin/stdout.

Повторяет поведение настоящего сервера настолько, насколько это нужно Jarvis:

* ``initialize`` — рукопожатие с версией протокола;
* ``tools/list`` — несколько инструментов разного вида (с аргументами, «только
  чтение», «меняет данные», медленный, всегда падающий);
* ``tools/call`` — вызов с результатом в формате content.

Инструмент ``secret`` отвечает текстом с секретом из окружения: так тесты
проверяют, что значение не попадает в журнал и в вывод CLI.

Запуск:  python tests/fake_mcp_server.py
"""

from __future__ import annotations

import json
import os
import sys
import time

TOOLS = [
    {
        "name": "echo",
        "description": "Повторяет текст обратно",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "sum",
        "description": "Складывает два числа",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "destroy",
        "description": "Меняет данные без спроса",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"destructiveHint": True},
    },
    {
        "name": "slow",
        "description": "Отвечает через пять секунд",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "boom",
        "description": "Всегда возвращает ошибку",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "secret",
        "description": "Возвращает значение переменной окружения",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _text(value: str, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": value}], "isError": is_error}


def handle(message: dict) -> dict | None:
    method = message.get("method", "")
    request_id = message.get("id")
    if request_id is None:  # уведомление — отвечать не нужно
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fake-mcp", "version": "1.0"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"tools": TOOLS, "nextCursor": None}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if name == "echo":
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": _text("эхо: " + str(arguments.get("text", "привет")))}
        if name == "sum":
            try:
                total = float(arguments["a"]) + float(arguments["b"])
            except (KeyError, TypeError, ValueError):
                return {"jsonrpc": "2.0", "id": request_id,
                        "result": _text("нужны два числа", is_error=True)}
            return {"jsonrpc": "2.0", "id": request_id, "result": _text(str(total))}
        if name == "destroy":
            return {"jsonrpc": "2.0", "id": request_id, "result": _text("данные испорчены")}
        if name == "slow":
            time.sleep(5)
            return {"jsonrpc": "2.0", "id": request_id, "result": _text("наконец-то")}
        if name == "boom":
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": _text("внутри инструмента всё сломалось", is_error=True)}
        if name == "secret":
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": _text("значение: " + os.environ.get("FAKE_MCP_SECRET", "нет"))}
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": f"нет инструмента {name}"}}
    if method == "notifications/initialized":
        return None
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"нет метода {method}"}}


def main() -> int:
    if "--die" in sys.argv:  # сервер, который сразу падает (проверка понятной ошибки)
        print("не запускаюсь", file=sys.stderr)
        return 3
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        answer = handle(message)
        if answer is not None:
            _send(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
