"""Telegram-бот управления ассистентом (этап 1).

Отличия от коннектора юзер-аккаунта (telegram_user.py):
- работает от имени БОТА (токен BotFather), видит только сообщения,
  отправленные боту, — это интерфейс управления, а не чтение лички;
- подтверждения опасных действий — inline-кнопками ✅/❌ прямо в чате;
- доступ строго у одного владельца: JARVIS_TELEGRAM_OWNER_ID из .env,
  либо первый пользователь, нажавший /start (id запоминается в data/).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from jarvis.config import Settings, get_settings
from jarvis.core.tools.base import ConfirmRequest
from jarvis.interfaces.runtime import build_runtime, shutdown_runtime

log = logging.getLogger("jarvis.tgbot")

# Лимит Telegram на сообщение — режем с запасом.
_CHUNK = 3900
_CONFIRM_TIMEOUT = 180  # сколько ждём нажатия кнопки, секунды


class BotConfirmations:
    """Гейт подтверждений: кнопки в чате, ожидание ответа владельца."""

    def __init__(self, bot: Bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def ask(self, request: ConfirmRequest) -> bool:
        request_id = uuid.uuid4().hex[:12]
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да, выполнить", callback_data=f"cf:{request_id}:yes"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data=f"cf:{request_id}:no"),
                ]
            ]
        )
        args_preview = json.dumps(request.args, ensure_ascii=False, indent=2)
        if len(args_preview) > 1500:
            args_preview = args_preview[:1500] + "\n…"
        text = (
            f"⚠️ <b>Требуется подтверждение</b>\n"
            f"Инструмент: <code>{request.tool.name}</code>\n"
            f"Причина: {request.reason}\n\n"
            f"<pre>{args_preview}</pre>"
        )
        message = await self._bot.send_message(self._chat_id, text, reply_markup=keyboard)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[request_id] = future
        try:
            return await asyncio.wait_for(future, timeout=_CONFIRM_TIMEOUT)
        except TimeoutError:
            await self._edit(message, f"⏱ Время подтверждения истекло — действие <b>отклонено</b> "
                                      f"(<code>{request.tool.name}</code>).")
            return False
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, approved: bool) -> bool:
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    async def _edit(self, message: Message, text: str) -> None:
        try:
            await message.edit_text(text)
        except Exception:
            log.debug("Не удалось отредактировать сообщение подтверждения", exc_info=True)


class OwnerGuard:
    """Один владелец: из .env (JARVIS_TELEGRAM_OWNER_ID) или первый /start."""

    def __init__(self, settings: Settings, data_dir: Path):
        self._fixed = None
        if settings.telegram_owner_id:
            self._fixed = settings.telegram_owner_id
        self._state_file = data_dir / "bot_owner.json"
        self._claimed = self._load()

    def _load(self) -> int | None:
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8")).get("owner_id")
        except Exception:
            return None

    def check(self, user_id: int) -> bool:
        if self._fixed is not None:
            return user_id == self._fixed
        return self._claimed is not None and user_id == self._claimed

    def claim(self, user_id: int) -> bool:
        """Первый /start занимает бота; сменить владельца можно только через .env."""
        if self._fixed is not None:
            return user_id == self._fixed
        if self._claimed is not None:
            return user_id == self._claimed
        self._claimed = user_id
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps({"owner_id": user_id}), encoding="utf-8")
        log.info("Владелец бота: %s", user_id)
        return True


async def run_bot() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit(
            "Не задан JARVIS_TELEGRAM_BOT_TOKEN. Создайте бота у @BotFather (/newbot) "
            "и вставьте токен в .env"
        )

    runtime = await build_runtime(with_llm=True)
    assert runtime.agent is not None
    bot = Bot(token=settings.telegram_bot_token)
    owner = OwnerGuard(settings, Path(settings.data_dir))
    router = Router()

    # Одна задача на чат: не гоняем параллельные запросы к модели от одного чата.
    busy: set[int] = set()

    async def _observer_for(chat_id: int):
        """Печатаем в чат ход выполнения инструментов."""
        status: dict[str, int] = {}  # tool -> message_id последнего статуса

        async def send_status(text: str, tool: str) -> None:
            try:
                msg = await bot.send_message(chat_id, text)
                status[tool] = msg.message_id
            except Exception:
                log.debug("Статус не отправлен", exc_info=True)

        def observer(event: str, **details) -> None:
            tool = details.get("tool", "")
            if event == "start":
                asyncio.ensure_future(send_status(f"⚙️ {tool}…", tool))
            elif event == "error":
                asyncio.ensure_future(send_status(f"❌ {tool}: {details.get('error', '')}", tool))
        return observer

    @router.message(CommandStart())
    async def _start(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        if not owner.claim(user_id):
            await message.answer("⛔ Это личный ассистент: доступ только у владельца.")
            return
        me = await bot.me()
        await message.answer(
            f"🤖 Джарвис на связи (бот @{me.username}).\n\n"
            "Просто напиши сообщение — отвечу с теми же инструментами, что и в CLI "
            "(чтение сайтов, поиск, память). Опасные действия подтверждай кнопками.\n"
            "Команды: /reset — сбросить диалог."
        )

    @router.message(Command("reset"))
    async def _reset(message: Message) -> None:
        if not message.from_user or not owner.check(message.from_user.id):
            return
        # Сессия клиента привязана к chat_id; новое значение = чистый контекст.
        await message.answer("🧹 Контекст диалога сброшен.")
        await _new_session(message.chat.id)

    @router.callback_query(F.data.startswith("cf:"))
    async def _confirm(callback: CallbackQuery) -> None:
        if not callback.from_user or not owner.check(callback.from_user.id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        _, request_id, verdict = (callback.data or "").split(":", 2)
        approved = verdict == "yes"
        chat_id = callback.message.chat.id if callback.message else 0
        confirmer = _confirmations_map.get(chat_id)
        ok = confirmer.resolve(request_id, approved) if confirmer else False
        await callback.answer("Принято ✅" if ok and approved else ("Отклонено" if ok else "Заявка устарела"))

    _confirmations_map: dict[int, BotConfirmations] = {}
    _sessions: dict[int, str] = {}

    async def _new_session(chat_id: int) -> str:
        _sessions[chat_id] = f"tg-{chat_id}-{uuid.uuid4().hex[:6]}"
        return _sessions[chat_id]

    @router.message(F.text)
    async def _handle(message: Message) -> None:
        if not message.from_user or not owner.check(message.from_user.id):
            await message.answer("⛔ Это личный ассистент: доступ только у владельца.")
            return
        chat_id = message.chat.id
        if chat_id in busy:
            await message.answer("⏳ Я ещё отвечаю на предыдущее сообщение…")
            return
        busy.add(chat_id)
        try:
            confirmer = _confirmations_map.setdefault(chat_id, BotConfirmations(bot, chat_id))
            session_id = _sessions.get(chat_id) or await _new_session(chat_id)

            # Индикатор «печатает…» на всё время обработки.
            typing = asyncio.create_task(_keep_typing(bot, chat_id))

            # Подменяем подтверждения/наблюдателя на ботовские для этого запроса.
            runtime.agent._confirmer = confirmer
            runtime.registry._observer = await _observer_for(chat_id)
            try:
                answer = await runtime.agent.handle(message.text or "", session_id)
            finally:
                typing.cancel()
                runtime.agent._confirmer = None
                runtime.registry._observer = None

            for chunk in _split(answer):
                await bot.send_message(chat_id, chunk)
        except Exception:
            log.exception("Ошибка обработки сообщения")
            await bot.send_message(chat_id, "😔 Произошла ошибка при обработке. Попробуй ещё раз.")
        finally:
            busy.discard(chat_id)

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    me = await bot.me()
    log.info("Бот @%s запущен, ждём сообщения владельца…", me.username)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await shutdown_runtime(runtime)
        await bot.session.close()


async def _keep_typing(bot: Bot, chat_id: int) -> None:
    try:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


def _split(text: str) -> list[str]:
    return [text[i : i + _CHUNK] for i in range(0, len(text), _CHUNK)] or [""]
