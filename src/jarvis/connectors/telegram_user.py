"""Коннектор Telegram от юзер-аккаунта (Telethon), режим «только читать».

Использует официальную клиентскую библиотеку MTProto — без скрапинга
интерфейса. Сессия хранится в data/sessions (файл чувствительный: содержит
ключ авторизации; не коммитить, не пересылать).
"""

from __future__ import annotations

import logging
from pathlib import Path

from jarvis.config import Settings, TelegramConnectorConfig
from jarvis.connectors.base import Connector, ConnectorError

log = logging.getLogger("jarvis.telegram")

_SNIPPET = 120


class TelegramUserConnector(Connector):
    name = "telegram"

    def __init__(self, settings: Settings, cfg: TelegramConnectorConfig):
        self._settings = settings
        self._cfg = cfg
        self._client = None
        self.problem: str | None = None

    @property
    def client(self):
        return self._client

    async def start(self) -> bool:
        if not self._cfg.enabled:
            self.problem = "коннектор отключён в connectors.yaml"
            return False
        if not self._settings.telegram_api_id or not self._settings.telegram_api_hash:
            self.problem = (
                "не заданы JARVIS_TELEGRAM_API_ID / JARVIS_TELEGRAM_API_HASH (my.telegram.org)"
            )
            return False
        from telethon import TelegramClient

        session_path = Path(self._cfg.session_file)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self._client = TelegramClient(
            str(session_path),
            self._settings.telegram_api_id,
            self._settings.telegram_api_hash,
        )
        await self._client.connect()
        if not await self._client.is_user_authorized():
            self.problem = "нет активной сессии — выполните `jarvis telegram login`"
            await self._client.disconnect()
            self._client = None
            return False
        me = await self._client.get_me()
        log.info("Telegram подключён: %s", getattr(me, "username", None) or getattr(me, "id", "?"))
        return True

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    # ---------- чтение ----------

    async def unread_summary(self, limit_dialogs: int = 30) -> str:
        client = self._require_client()
        dialogs = await client.get_dialogs(limit=limit_dialogs)
        lines = []
        for d in dialogs:
            if not d.unread_count:
                continue
            last = ""
            if d.message and getattr(d.message, "message", None):
                last = str(d.message.message).replace("\n", " ")[:_SNIPPET]
            when = ""
            if d.message and d.message.date:
                when = d.message.date.strftime("%d.%m %H:%M UTC")
            kind = "канал" if d.is_broadcast else ("группа" if d.is_group else "личный")
            lines.append(f"- {d.name} [{kind}]: {d.unread_count} непрочитанных. Последнее ({when}): {last}")
        if not lines:
            return f"Непрочитанных сообщений нет (в первых {limit_dialogs} диалогах)."
        return "Непрочитанные диалогы:\n" + "\n".join(lines)

    async def recent_messages(self, chat: str, limit: int = 10) -> str:
        client = self._require_client()
        entity = await self._resolve_chat(chat)
        messages = [m async for m in client.iter_messages(entity, limit=limit)]
        if not messages:
            return f"В чате {chat!r} сообщений не найдено."
        lines = []
        for m in messages:
            sender = _sender_name(m)
            text = (m.message or "(медиа/без текста)").replace("\n", " ")[:_SNIPPET]
            when = m.date.strftime("%d.%m %H:%M UTC") if m.date else ""
            lines.append(f"[{when}] {sender}: {text}")
        return f"Последние сообщения в чате {chat!r} (новые сверху):\n" + "\n".join(lines)

    async def search_messages(self, query: str, chat: str | None = None, limit: int = 10) -> str:
        client = self._require_client()
        if chat:
            entity = await self._resolve_chat(chat)
            messages = [m async for m in client.iter_messages(entity, search=query, limit=limit)]
            return self._format_search(messages, f"чат {chat!r}")
        # Глобальный поиск: Telethon ищет только внутри конкретных диалогов,
        # поэтому проходим по диалогам и собираем совпадения.
        dialogs = await client.get_dialogs(limit=25)
        found = []
        for d in dialogs:
            if d.entity is None:
                continue
            try:
                async for m in client.iter_messages(d.entity, search=query, limit=3):
                    found.append((d.name, m))
            except Exception as e:
                log.debug("Поиск в %s не удался: %s", d.name, e)
            if len(found) >= limit:
                break
        found.sort(key=lambda pair: pair[1].date or 0, reverse=True)
        return self._format_search([m for _, m in found[:limit]], "последним 25 диалогам", names=[n for n, _ in found[:limit]])

    # ---------- служебное ----------

    @staticmethod
    def _format_search(messages: list, where: str, names: list[str] | None = None) -> str:
        if not messages:
            return f"По запросу в {where} ничего не найдено."
        lines = []
        for i, m in enumerate(messages):
            chat_prefix = f"{names[i]} — " if names else ""
            sender = _sender_name(m)
            text = (m.message or "(медиа/без текста)").replace("\n", " ")[:_SNIPPET]
            when = m.date.strftime("%d.%m.%Y %H:%M UTC") if m.date else ""
            lines.append(f"[{when}] {chat_prefix}{sender}: {text}")
        return f"Результаты поиска ({where}):\n" + "\n".join(lines)

    def _require_client(self):
        if self._client is None:
            raise ConnectorError("Telegram не подключён (нет сессии или ключей).")
        return self._client

    async def _resolve_chat(self, chat: str):
        """Юзернейм, ссылка, id или точное название диалога → entity."""
        client = self._require_client()
        try:
            return await client.get_entity(chat)
        except Exception:
            pass
        lowered = chat.strip().lower()
        async for d in client.iter_dialogs(limit=100):
            if d.name and d.name.strip().lower() == lowered:
                return d.entity
        raise ConnectorError(f"Чат {chat!r} не найден среди ваших диалогов.")


def _sender_name(message) -> str:
    sender = getattr(message, "sender", None)
    if sender is None:
        return "?"
    name = getattr(sender, "title", None) or " ".join(
        part for part in (getattr(sender, "first_name", ""), getattr(sender, "last_name", "")) if part
    )
    return name or getattr(sender, "username", "") or "?"
