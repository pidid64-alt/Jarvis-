"""Сообщения Jarvis: адресная книга, отправка и проверка входящих.

Здесь живёт всё, что не относится к конкретному сервису: кому можно писать
(``contacts.toml``), что уже показано (состояние в ``state/messengers.json``) и
как выглядит «что нового» для человека.

Правило проекта, взятое у OpenClaw: получатель и текст обязательны,
подтверждение спрашивается всегда, при неясности — уточняем, а не угадываем.
"""

from __future__ import annotations

import json
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .errors import ConfigError, ProviderError
from .logging_setup import get_logger

log = get_logger("core.messengers")

MAX_RECENT = 50
#: сколько идентификаторов помним, чтобы не показать одно сообщение дважды
MAX_SEEN = 300


def normalize_name(name: str) -> str:
    """«Артёму» → «артем»: основа слова для сравнения имён из фразы и книги.

    Пользователь говорит «отправь Артёму», а в адресной книге написано «Артём» —
    имена сравниваем по основе, а не по точному совпадению.
    """
    value = (name or "").strip().casefold().replace("ё", "е")
    for suffix in ("ами", "ями", "ого", "его", "ому", "ему", "ой", "ей", "ом", "ем",
                   "ах", "ях", "ам", "ям", "у", "ю", "е", "а", "я", "ы", "и"):
        if len(value) > len(suffix) + 2 and value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def names_match(spoken: str, contact_name: str) -> bool:
    """Похоже ли названное имя на запись в книге (с учётом падежа)."""
    left, right = normalize_name(spoken), normalize_name(contact_name)
    if len(left) < 3 or len(right) < 3:
        return False
    return left == right or left.startswith(right) or right.startswith(left)


@dataclass
class Contact:
    """Один человек из адресной книги."""

    name: str
    telegram: str = ""
    email: str = ""
    whatsapp: str = ""
    notes: str = ""

    def target(self, channel: str) -> str:
        return {"telegram": self.telegram, "mail": self.email, "whatsapp": self.whatsapp}.get(
            channel, "")

    def to_public_dict(self) -> dict[str, Any]:
        return {"name": self.name, "channels": [
            name for name, value in (("telegram", self.telegram), ("mail", self.email),
                                     ("whatsapp", self.whatsapp)) if value]}


def ensure_contacts_file() -> Path:
    """Создаёт ``contacts.toml`` с примерами при первом обращении."""
    path = paths.contacts_path()
    if path.exists():
        return path
    paths.ensure_dirs()
    example = paths.bundled_contacts_example_path()
    text = example.read_text(encoding="utf-8") if example.exists() else "# Адресная книга Jarvis\n"
    path.write_text(text, encoding="utf-8")
    return path


def parse_contacts(data: dict[str, Any]) -> list[Contact]:
    """Разбирает секции ``[[contact]]``."""
    items = data.get("contact") or []
    if isinstance(items, dict):
        items = [items]
    contacts: list[Contact] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigError(f"contacts.toml: запись contact должна быть таблицей (номер {index + 1})")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ConfigError(f"contacts.toml: у записи номер {index + 1} не указано имя (name)")
        contacts.append(Contact(
            name=name,
            telegram=str(item.get("telegram", "")).strip(),
            email=str(item.get("email", "")).strip(),
            whatsapp=str(item.get("whatsapp", "")).strip(),
            notes=str(item.get("notes", "")).strip(),
        ))
    return contacts


class AddressBook:
    """Кому Jarvis может писать. Читается с диска при каждом обращении."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.contacts_path()
        self._contacts: list[Contact] = []
        self._mtime = 0.0

    def load(self, *, force: bool = False) -> list[Contact]:
        if not self.path.exists():
            return []
        mtime = self.path.stat().st_mtime
        if not force and self._contacts and mtime == self._mtime:
            return self._contacts
        try:
            data = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"{self.path}: {exc}") from exc
        self._contacts = parse_contacts(data)
        self._mtime = mtime
        return self._contacts

    def find(self, name: str) -> Contact | None:
        """Ищет контакт: точное имя → начало имени → основа слова («Артёму»).

        Если подходит несколько записей, возвращаем None: лучше переспросить,
        чем отправить не тому.
        """
        wanted = (name or "").strip().casefold()
        if not wanted:
            return None
        contacts = self.load()
        for contact in contacts:
            if contact.name.casefold() == wanted:
                return contact
        starts = [contact for contact in contacts if contact.name.casefold().startswith(wanted)]
        if len(starts) == 1:
            return starts[0]
        loose = [contact for contact in contacts if names_match(name, contact.name)]
        return loose[0] if len(loose) == 1 else None

    def similar(self, name: str, limit: int = 5) -> list[str]:
        """Имена, которые могут иметься в виду: для уточняющего вопроса."""
        return [contact.name for contact in self.load()
                if names_match(name, contact.name)][:limit]

    def looks_like_address(self, value: str) -> bool:
        text = (value or "").strip()
        return bool(text) and ("@" in text or text.startswith("+")
                               or text.replace("-", "").replace(" ", "").isdigit())

    def to_public(self) -> list[dict[str, Any]]:
        return [contact.to_public_dict() for contact in self.load()]


@dataclass
class SendResult:
    """Итог отправки: что сказать пользователю и куда ушло."""

    channel: str
    target: str
    display: str
    message_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class MessengerService:
    """Один вход для навыка и демона: отправить, проверить входящие, состояние."""

    def __init__(self, config: Any, journal: Any = None, *, state_path: Path | None = None,
                 channels: Any = None):
        self.config = config
        self.journal = journal
        self.address_book = AddressBook()
        self._channels = channels
        self.state_path = Path(state_path) if state_path else paths.state_dir() / "messengers.json"
        self._lock = threading.RLock()
        self._settings = config.section("messengers") if config is not None else {}
        self._state = self._load_state()
        self._last_check = 0.0

    # ------------------------------------------------------------------ настройки
    @property
    def enabled(self) -> bool:
        return bool(self._settings.get("enabled", False))

    @property
    def poll_seconds(self) -> float:
        try:
            return max(10.0, float(self._settings.get("poll_seconds", 60)))
        except (TypeError, ValueError):
            return 60.0

    @property
    def notify(self) -> bool:
        return bool(self._settings.get("notify", True))

    @property
    def read_aloud(self) -> bool:
        return bool(self._settings.get("read_aloud", False))

    @property
    def mail_limit(self) -> int:
        try:
            return max(1, min(20, int(self._settings.get("mail_limit", 5))))
        except (TypeError, ValueError):
            return 5

    # -------------------------------------------------------------------- каналы
    @property
    def channels(self) -> Any:
        if self._channels is None:
            from ..providers.messengers import Channels, MailChannel, TelegramChannel, WhatsAppChannel

            section = self._settings
            self._channels = Channels(
                telegram=TelegramChannel(str(section.get("telegram_token", "") or "")),
                mail=MailChannel(
                    login=str(section.get("gmail_user", "") or ""),
                    password=str(section.get("gmail_password", "") or ""),
                    smtp_host=str(section.get("smtp_host", "smtp.gmail.com")),
                    smtp_port=int(section.get("smtp_port", 465)),
                    imap_host=str(section.get("imap_host", "imap.gmail.com")),
                    mailbox=str(section.get("mailbox", "INBOX")),
                ),
                whatsapp=WhatsAppChannel(
                    command=str(section.get("whatsapp_command", "wacli") or "wacli"),
                    store=str(section.get("whatsapp_store", "") or ""),
                ),
            )
        return self._channels

    # ---------------------------------------------------------------- состояние
    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"telegram_offset": 0, "mail_uid": {}, "seen": [], "recent": [], "viewed": []}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("состояние сообщений повреждено — начинаю заново")
            return {"telegram_offset": 0, "mail_uid": {}, "seen": [], "recent": [], "viewed": []}
        data.setdefault("telegram_offset", 0)
        data.setdefault("mail_uid", {})
        data.setdefault("seen", [])
        data.setdefault("recent", [])
        data.setdefault("viewed", [])
        return data

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    # ----------------------------------------------------------------- отправка
    def resolve(self, channel_name: str, recipient: str) -> tuple[str, str, str]:
        """Кому писать: (адрес, как показать, ошибка). Ничего не угадываем."""
        channel = self.channels.get(channel_name)
        if channel is None:
            return "", "", f"канал «{channel_name}» не поддерживается"
        recipient = (recipient or "").strip()
        if not recipient:
            return "", "", "не указан получатель"
        contact = self.address_book.find(recipient)
        if contact is not None:
            target = contact.target(channel.name)
            if not target:
                return "", contact.name, (f"у контакта «{contact.name}» не указан адрес для "
                                          f"{channel.title}")
            if channel.name == "whatsapp":
                resolved, similar = channel.resolve(target)
                if resolved is not None:
                    return str(resolved.get("jid") or target), contact.name, ""
                if similar:
                    return "", contact.name, ("в WhatsApp несколько похожих чатов: "
                                              + ", ".join(similar[:5]))
            return target, contact.name, ""
        if channel.name == "whatsapp":
            resolved, similar = channel.resolve(recipient)
            if resolved is not None:
                name = str(resolved.get("name") or recipient)
                return str(resolved.get("jid") or recipient), name, ""
            if similar:
                return "", recipient, ("в WhatsApp несколько похожих чатов: " + ", ".join(similar[:5]))
            return "", recipient, f"не нашёл «{recipient}» в WhatsApp (посмотрите: wacli chats list)"
        if channel.name == "mail" and "@" not in recipient:
            return "", recipient, f"«{recipient}» не похоже на адрес почты, и такого контакта нет"
        if channel.name == "telegram" and not recipient.replace("-", "").isdigit():
            return "", recipient, (f"«{recipient}» не похоже на chat_id Telegram, и такого контакта "
                                   f"нет в contacts.toml")
        return recipient, recipient, ""

    def send(self, channel_name: str, recipient: str, text: str, *,
             subject: str = "") -> SendResult:
        """Отправить сообщение. Ошибки — понятным текстом."""
        channel = self.channels.get(channel_name)
        if channel is None:
            raise ProviderError(f"канал «{channel_name}» не поддерживается")
        if not self.enabled:
            raise ProviderError("сообщения выключены в настройках (messengers.enabled)")
        target, display, problem = self.resolve(channel.name, recipient)
        if problem:
            raise ProviderError(problem)
        text = (text or "").strip()
        if not text:
            raise ProviderError("пустой текст сообщения")
        if channel.name == "mail":
            message_id = channel.send(target, text, subject=subject)
        else:
            message_id = channel.send(target, text)
        if self.journal is not None:
            self.journal.info("core.messengers",
                              f"отправлено в {channel.title} → {display} ({len(text)} символов)")
        return SendResult(channel=channel.name, target=target, display=display,
                          message_id=message_id)

    # --------------------------------------------------------------------- приём
    def check_new(self, *, limit: int = 5) -> list[Any]:
        """Опрашивает каналы. Возвращает только то, чего ещё не видели."""
        if not self.enabled:
            return []
        found: list[Any] = []
        with self._lock:
            seen = set(self._state.get("seen") or [])
            for channel in self.channels.all():
                if channel.name == "telegram":
                    ready, reason = channel.available()
                    if not ready:
                        log.debug("Telegram недоступен: %s", reason)
                        continue
                    try:
                        messages, offset = channel.poll(int(self._state.get("telegram_offset", 0)))
                        self._state["telegram_offset"] = offset
                    except Exception as exc:  # noqa: BLE001 - канал не должен ронять цикл
                        log.warning("Telegram: проверка не удалась: %s", exc)
                        continue
                elif channel.name == "mail":
                    ready, reason = channel.available()
                    if not ready:
                        continue
                    try:
                        uid = int((self._state.get("mail_uid") or {}).get(channel.mailbox, 0))
                        messages, highest = channel.poll(last_uid=uid, limit=limit)
                        self._state.setdefault("mail_uid", {})[channel.mailbox] = highest
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Почта: проверка не удалась: %s", exc)
                        continue
                else:
                    ready, reason = channel.available()
                    if not ready:
                        continue
                    try:
                        messages = channel.poll(limit=limit)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("WhatsApp: проверка не удалась: %s", exc)
                        continue
                for message in messages:
                    if message.uid and message.uid in seen:
                        continue
                    if message.uid:
                        seen.add(message.uid)
                        self._state.setdefault("seen", []).append(message.uid)
                    found.append(message)
            self._state["seen"] = list(self._state.get("seen") or [])[-MAX_SEEN:]
            if found:
                recent = self._state.setdefault("recent", [])
                recent.extend(message.to_dict() for message in found)
                self._state["recent"] = recent[-MAX_RECENT:]
            self._save_state()
        return found

    def recent(self, *, limit: int = 5, only_new: bool = True) -> list[dict[str, Any]]:
        """Последние входящие для навыка «что нового»."""
        with self._lock:
            items = list(self._state.get("recent") or [])
            if only_new:
                viewed = set(self._state.get("viewed") or [])
                items = [item for item in items if item.get("uid") not in viewed]
            items = items[-limit:]
            if only_new and items:
                viewed = list(self._state.get("viewed") or []) + [item.get("uid") for item in items]
                self._state["viewed"] = viewed[-MAX_SEEN:]
                self._save_state()
            return items

    def mark_viewed(self) -> None:
        with self._lock:
            self._state["viewed"] = [item.get("uid") for item in (self._state.get("recent") or [])]
            self._save_state()

    # ------------------------------------------------------------------ состояние
    def state(self, *, probe: bool = False) -> dict[str, Any]:
        try:
            channels = self.channels.state(probe=probe)
        except Exception as exc:  # noqa: BLE001
            log.warning("каналы сообщений не опрошены: %s", exc)
            channels = {}
        return {
            "enabled": self.enabled,
            "path": str(self.state_path),
            "contacts_path": str(self.address_book.path),
            "contacts": self.address_book.to_public(),
            "poll_seconds": self.poll_seconds,
            "read_aloud": self.read_aloud,
            "channels": channels,
        }

    # ------------------------------------------------- проверка по расписанию
    def check_new_if_due(self, *, now: float | None = None) -> list[Any]:
        """Проверяет входящие не чаще, чем раз в poll_seconds (для демона)."""
        if not self.enabled:
            return []
        moment = time.monotonic() if now is None else now
        if moment - self._last_check < self.poll_seconds:
            return []
        self._last_check = moment
        return self.check_new(limit=self.mail_limit)

    def doctor(self) -> list[tuple[str, bool, str]]:
        """Проверка каналов для `jarvis messengers`: (канал, готов, объяснение)."""
        result: list[tuple[str, bool, str]] = []
        for channel in self.channels.all():
            try:
                ready, reason = channel.available()
            except Exception as exc:  # noqa: BLE001
                ready, reason = False, str(exc)
            if ready and channel.name == "mail":
                ready, reason = channel.doctor()
            result.append((channel.name, ready, reason))
        return result
