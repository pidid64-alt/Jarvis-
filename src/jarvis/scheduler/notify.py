"""Уведомления владельцу: Telegram-бот (основной канал) + журнал (фолбэк)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from jarvis.config import Settings

log = logging.getLogger("jarvis.notify")


class TelegramNotifier:
    """Шлёт сообщения владельцу через бот-API (httpx, без aiogram).

    chat_id владельца берётся из JARVIS_TELEGRAM_OWNER_ID либо из
    data/bot_owner.json (файл создаётся при первом /start бота).
    """

    def __init__(self, token: str, data_dir: Path, owner_id: int | None = None):
        self._token = token
        self._owner_file = data_dir / "bot_owner.json"
        self._owner_id = owner_id
        self._client = httpx.AsyncClient(timeout=15.0)

    async def _chat_id(self) -> int | None:
        if self._owner_id:
            return self._owner_id
        try:
            return json.loads(self._owner_file.read_text(encoding="utf-8")).get("owner_id")
        except Exception:
            return None

    async def send(self, text: str) -> bool:
        chat_id = await self._chat_id()
        if not self._token or not chat_id:
            log.warning("Уведомление не отправлено (нет токена бота или владельца): %.200s", text)
            return False
        try:
            resp = await self._client.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:3900]},
            )
            if resp.status_code != 200:
                log.warning("Telegram ответил %s на уведомление", resp.status_code)
                return False
            return True
        except httpx.HTTPError as e:
            log.warning("Сеть при отправке уведомления: %s", e)
            return False

    async def close(self) -> None:
        await self._client.aclose()


def create_notifier(settings: Settings, data_dir: Path) -> TelegramNotifier:
    return TelegramNotifier(settings.telegram_bot_token, data_dir, settings.telegram_owner_id)
