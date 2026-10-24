"""Базовые типы инструментов агента."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

# Класс действия инструмента — основа политики подтверждений:
#   read        — чтение (никогда не подтверждается)
#   write       — локальная запись (память, файлы; не подтверждается)
#   outbound    — исходящее действие во внешний мир (по умолчанию подтверждение)
#   high_stakes — финансы/документы/обещания (подтверждение обязательно всегда)
SideEffect = Literal["read", "write", "outbound", "high_stakes"]


class ToolError(RuntimeError):
    """Финальная ошибка инструмента: попадает модели как наблюдение."""


@dataclass
class ToolResult:
    output: str
    # True — содержимое пришло из внешнего мира и будет обёрнуто в <untrusted>.
    untrusted: bool = False


@dataclass
class Tool:
    name: str  # [a-zA-Z0-9_-], до 64 символов (требование function calling)
    description: str
    parameters: dict  # JSON Schema аргументов
    handler: Callable[[dict], Awaitable[ToolResult]]
    side_effect: SideEffect = "read"
    returns_untrusted: bool = False
    timeout: float = 90.0
    source: str = "builtin"  # builtin | mcp:<server> | connector:<name>
    meta: dict = field(default_factory=dict)


@dataclass
class ConfirmRequest:
    tool: Tool
    args: dict
    reason: str


# Интерфейс подтверждения: интерфейс (CLI/Telegram-бот) показывает действие
# и возвращает решение пользователя.
Confirmer = Callable[[ConfirmRequest], Awaitable[bool]]
