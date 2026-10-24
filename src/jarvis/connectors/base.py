"""Общий интерфейс коннектора: чтение, поиск, черновики, отправка.

MVP реализует только чтение (read-методы). Отправка (drafts/send) появится
на этапе 2 и пройдёт через гейт подтверждений с классом outbound/high_stakes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ConnectorError(RuntimeError):
    pass


class Connector(ABC):
    name: str = "base"

    @abstractmethod
    async def start(self) -> bool:
        """Подключение. False — коннектор не настроен/не авторизован (проблема в .problem)."""

    @abstractmethod
    async def stop(self) -> None:
        ...

    # ---- чтение (MVP) ----
    @abstractmethod
    async def unread_summary(self, limit_dialogs: int = 30) -> str:
        ...

    @abstractmethod
    async def recent_messages(self, chat: str, limit: int = 10) -> str:
        ...

    @abstractmethod
    async def search_messages(self, query: str, chat: str | None = None, limit: int = 10) -> str:
        ...
