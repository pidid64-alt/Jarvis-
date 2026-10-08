"""Каналы сообщений: Telegram, почта (Gmail через IMAP/SMTP), WhatsApp.

Задача модуля — говорить с внешним миром и не мешать остальному Jarvis.
Каналы ничего не знают про навыки и подтверждения: это просто «отправить» и
«что нового», а решения принимает ядро.

Зависимостей нет: Telegram — обычные HTTPS-запросы к Bot API, почта — imaplib и
smtplib из стандартной библиотеки, WhatsApp — программа ``wacli`` (привязка
телефона по QR, как в OpenClaw), с которой Jarvis говорит через командную
строку и JSON.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import json
import logging
import re
import smtplib
import ssl
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from typing import Any

from ..core.errors import ProviderError
from .http import HttpClient

log = logging.getLogger("jarvis.providers.messengers")

TELEGRAM_API = "https://api.telegram.org"


def _raw_header(raw: bytes, name: str) -> str:
    """Значение заголовка прямо из байтов письма.

    Стандартный разбор превращает русские буквы, отправленные без пометки
    кодировки, в «????». Чтобы в уведомлении было видно настоящее имя
    отправителя, читаем строку заголовка сами и раскодируем её по очереди
    UTF-8, cp1251 и latin-1, а затем раскодируем слова вида «=?utf-8?...?=».
    """
    prefix = (name + ":").encode("ascii").lower()
    for line in (raw or b"").split(b"\n"):
        stripped = line.rstrip(b"\r")
        if not stripped:
            break                       # заголовки закончились
        if stripped.lower().startswith(prefix):
            value = stripped[len(prefix):].strip()
            for candidate in ("utf-8", "cp1251", "latin-1"):
                try:
                    return _clean(value.decode(candidate))
                except UnicodeDecodeError:
                    continue
            return _clean(value.decode("utf-8", "replace"))
    return ""


def _clean(value: str | None) -> str:
    """Заголовок письма в читаемый вид.

    Письма приходят в разных кодировках, и часть отправителей пишет имя
    по-русски прямо в UTF-8 без пометки. Поэтому сначала стандартная раскодировка
    заголовка, а если кодировка не указана или неизвестна — пробуем по очереди
    UTF-8, cp1251 и latin-1, чтобы вместо «????» было видно настоящее имя.
    """
    if not value:
        return ""
    try:
        parts = decode_header(str(value))   # Header из библиотеки email тоже подходит
    except (ValueError, LookupError):  # битая кодировка — показываем как есть
        return str(value).strip()
    result: list[str] = []
    for text, charset in parts:
        if isinstance(text, bytes):
            for candidate in (charset, "utf-8", "cp1251", "latin-1"):
                if not candidate:
                    continue
                try:
                    result.append(text.decode(candidate))
                    break
                except (LookupError, UnicodeDecodeError):
                    continue
            else:
                result.append(text.decode("utf-8", "replace"))
        else:
            result.append(text)
    text = "".join(result)
    if "\ufffd" in text:
        text = text.replace("\ufffd", "")      # битые байты в имени лучше убрать
    if any("\udc80" <= char <= "\udcff" for char in text):
        # отправитель положил русский текст прямо в заголовок без пометки кодировки:
        # python вернул такие байты «как есть» — пробуем прочитать их сами
        raw = text.encode("utf-8", "surrogateescape")
        for candidate in ("utf-8", "cp1251"):
            try:
                return raw.decode(candidate).strip()
            except UnicodeDecodeError:
                continue
    return text.strip()


@dataclass
class IncomingMessage:
    """Одно входящее сообщение из любого канала."""

    channel: str
    sender: str
    text: str
    #: когда пришло (ISO-строка) — по ней же идёт «что нового»
    ts: str = ""
    #: внутренний идентификатор: по нему не показываем одно и то же дважды
    uid: str = ""
    subject: str = ""
    conversation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "sender": self.sender,
            "text": self.text,
            "ts": self.ts,
            "uid": self.uid,
            "subject": self.subject,
            "conversation": self.conversation,
        }

    def describe(self) -> str:
        who = self.sender or self.conversation or "?"
        if self.subject:
            return f"{who}: {self.subject} — {self.text}"
        return f"{who}: {self.text}"


# ---------------------------------------------------------------- Telegram
class TelegramChannel:
    """Бот Telegram: отправка через sendMessage, приём — getUpdates.

    Никакого вебхука не нужно: Jarvis сам раз в минуту спрашивает у Telegram,
    что нового, и запоминает, до какого обновления уже дочитал.
    """

    name = "telegram"
    title = "Telegram"

    def __init__(self, token: str, *, timeout: float = 15.0, validate: bool = True):
        self.token = (token or "").strip()
        self.timeout = timeout
        self.validate = validate
        self.client = HttpClient(timeout=timeout, retries=1)
        self._username = ""
        self._checked = False

    # ------------------------------------------------------------ доступность
    def available(self) -> tuple[bool, str]:
        if not self.token:
            return False, "не задан токен бота (JARVIS_TELEGRAM_TOKEN)"
        if not self.validate:
            return True, "готов (проверка при отправке)"
        if self._checked:
            return True, f"готов (бот @{self._username})" if self._username else "готов"
        try:
            data = self._call("getMe", {})
        except ProviderError as exc:
            return False, f"Telegram не ответил: {exc}"
        self._checked = True
        self._username = str((data.get("result") or {}).get("username", ""))
        return True, f"готов (бот @{self._username})" if self._username else "готов"

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{TELEGRAM_API}/bot{self.token}/{method}"
        try:
            response = self.client.post_json(url, payload)
            data = json.loads(response.body.decode("utf-8", "replace") or "{}")
        except Exception as exc:  # noqa: BLE001 - сеть и токен бывают разными
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
        if not data.get("ok"):
            description = str(data.get("description") or "неизвестная ошибка")
            raise ProviderError(description)
        return data

    # ------------------------------------------------------------------ работа
    def send(self, chat_id: str, text: str) -> str:
        data = self._call("sendMessage", {
            "chat_id": chat_id, "text": text, "disable_web_page_preview": True,
        })
        result = data.get("result") or {}
        return str(result.get("message_id", ""))

    def poll(self, offset: int = 0) -> tuple[list[IncomingMessage], int]:
        """Новые сообщения и следующий offset (чтобы не показывать их дважды)."""
        data = self._call("getUpdates", {"offset": offset, "timeout": 0,
                                         "allowed_updates": ["message"]})
        messages: list[IncomingMessage] = []
        next_offset = offset
        for update in data.get("result") or []:
            next_offset = max(next_offset, int(update.get("update_id", 0)) + 1)
            message = update.get("message") or {}
            text = str(message.get("text") or message.get("caption") or "")
            if not text:
                continue
            chat = message.get("chat") or {}
            who = message.get("from") or {}
            name = " ".join(item for item in (who.get("first_name"), who.get("last_name")) if item)
            messages.append(IncomingMessage(
                channel=self.name,
                sender=name or str(chat.get("title") or chat.get("username") or chat.get("id")),
                text=text,
                ts=datetime.fromtimestamp(int(message.get("date", 0)), tz=timezone.utc)
                .isoformat() if message.get("date") else "",
                uid=f"tg:{update.get('update_id')}",
                conversation=str(chat.get("id", "")),
            ))
        return messages, next_offset

    def state(self, *, probe: bool = False) -> dict[str, Any]:
        if probe:
            ready, reason = self.available()
        elif self.token:
            # без сети: окно не должно тормозить на проверке связи
            ready, reason = True, "токен задан, связь проверится при отправке"
        else:
            ready, reason = False, "не задан токен бота (JARVIS_TELEGRAM_TOKEN)"
        return {"available": ready, "reason": reason, "bot": self._username}


# ------------------------------------------------------------------- почта
class MailChannel:
    """Почта через IMAP/SMTP: вход — адрес и пароль приложения Google."""

    name = "mail"
    title = "Почта"

    def __init__(self, *, login: str = "", password: str = "", smtp_host: str = "smtp.gmail.com",
                 smtp_port: int = 465, imap_host: str = "imap.gmail.com", mailbox: str = "INBOX",
                 timeout: float = 30.0):
        self.login = (login or "").strip()
        self.password = self.normalize_app_password(password)
        self.smtp_host = smtp_host
        self.smtp_port = int(smtp_port)
        self.imap_host = imap_host
        self.mailbox = mailbox or "INBOX"
        self.timeout = timeout

    @staticmethod
    def normalize_app_password(password: str) -> str:
        """Убирает пробелы из пароля приложения Google.

        Google показывает пароль группами: «abcd efgh ijkl mnop». Пробелы нужны
        только для чтения, при входе они лишние. Трогаем пароль, лишь если после
        удаления пробелов получается ровно 16 знаков (такой длины бывает пароль
        приложения) — любой другой пароль остаётся ровно как введён.
        """
        text = (password or "").strip()
        if " " in text and len(text.replace(" ", "")) == 16:
            return text.replace(" ", "")
        return text

    def available(self) -> tuple[bool, str]:
        if not self.login or not self.password:
            return False, "не заданы почта и пароль приложения (JARVIS_GMAIL_USER, JARVIS_GMAIL_PASSWORD)"
        return True, f"готов ({self.login})"

    # ------------------------------------------------------------------ отправка
    def send(self, to: str, text: str, *, subject: str = "") -> str:
        if not self.login or not self.password:
            raise ProviderError("не заданы почта и пароль приложения")
        if not to:
            raise ProviderError("не указан получатель")
        letter = EmailMessage()
        letter["From"] = self.login
        letter["To"] = to
        letter["Subject"] = subject or "Сообщение от Jarvis"
        letter["Date"] = email.utils.formatdate(localtime=True)
        letter.set_content(text)
        context = ssl.create_default_context()
        try:
            if self.smtp_port == 465:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=self.timeout,
                                      context=context) as server:
                    server.login(self.login, self.password)
                    server.send_message(letter)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout) as server:
                    server.starttls(context=context)
                    server.login(self.login, self.password)
                    server.send_message(letter)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            raise ProviderError(f"письмо не ушло: {exc}") from exc
        return ""

    # --------------------------------------------------------------------- приём
    def poll(self, *, last_uid: int = 0, limit: int = 5) -> tuple[list[IncomingMessage], int]:
        """Новые письма в ящике. Пометку «прочитано» не ставим: это делает человек."""
        if not self.login or not self.password:
            raise ProviderError("не заданы почта и пароль приложения")
        try:
            client = imaplib.IMAP4_SSL(self.imap_host, timeout=self.timeout)
        except (OSError, imaplib.IMAP4.error) as exc:
            raise ProviderError(f"не удалось подключиться к {self.imap_host}: {exc}") from exc
        messages: list[IncomingMessage] = []
        highest = int(last_uid or 0)
        try:
            client.login(self.login, self.password)
            client.select(self.mailbox, readonly=True)
            criteria = f"UID {int(last_uid or 0) + 1}:*" if last_uid else "ALL"
            status, data = client.uid("search", None, criteria)
            if status != "OK":
                raise ProviderError(f"поиск в {self.mailbox} не удался")
            uids = [int(item) for item in (data[0] or b"").split() if item.isdigit()]
            for uid in sorted(uids)[-limit:]:
                highest = max(highest, uid)
                status, raw = client.uid("fetch", str(uid), "(RFC822)")
                if status != "OK" or not raw:
                    continue
                payload = raw[0][1] if isinstance(raw[0], tuple) else b""
                messages.append(self._parse(payload, uid))
        except imaplib.IMAP4.error as exc:
            raise ProviderError(f"почта ответила ошибкой: {exc}") from exc
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001 - соединение могло уже закрыться
                pass
        return messages, highest

    def _parse(self, raw: bytes, uid: int) -> IncomingMessage:
        letter = email.message_from_bytes(raw or b"")
        sender = _raw_header(raw, "From")
        subject = _raw_header(raw, "Subject")
        date = _clean(letter.get("Date"))
        text = ""
        if letter.is_multipart():
            for part in letter.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    payload = part.get_payload(decode=True) or b""
                    text = payload.decode(part.get_content_charset() or "utf-8", "replace")
                    if text.strip():
                        break
        else:
            payload = letter.get_payload(decode=True) or b""
            text = payload.decode(letter.get_content_charset() or "utf-8", "replace")
        text = re.sub(r"\s+", " ", text).strip()
        return IncomingMessage(
            channel=self.name,
            sender=sender,
            text=text[:500],
            subject=subject,
            ts=date,
            uid=f"mail:{uid}",
            conversation=self.mailbox,
        )

    def state(self, *, probe: bool = False) -> dict[str, Any]:
        if probe:
            ready, reason = self.doctor()
        elif self.login and self.password:
            ready, reason = True, "почта и пароль заданы, вход проверится при отправке"
        else:
            ready, reason = self.available()
        return {"available": ready, "reason": reason, "login": self.login,
                "mailbox": self.mailbox}

    def doctor(self) -> tuple[bool, str]:
        """Проверка входа в почту — вызывается из `jarvis messengers --check`."""
        if not self.login or not self.password:
            return False, "не заданы почта и пароль приложения"
        try:
            client = imaplib.IMAP4_SSL(self.imap_host, timeout=self.timeout)
            client.login(self.login, self.password)
            client.select(self.mailbox, readonly=True)
            client.logout()
        except (OSError, imaplib.IMAP4.error) as exc:
            return False, f"не удалось войти: {exc}"
        return True, "вход выполнен"


# ------------------------------------------------------------------ WhatsApp
class WhatsAppChannel:
    """WhatsApp через программу ``wacli`` (привязка телефона по QR).

    Jarvis не говорит по протоколу WhatsApp сам: этим занимается wacli (как в
    OpenClaw). Он хранит связанный сеанс и локальный поиск сообщений, а Jarvis
    вызывает его команды и читает JSON.
    """

    name = "whatsapp"
    title = "WhatsApp"

    def __init__(self, command: str = "wacli", store: str = "", *, timeout: float = 60.0):
        self.command = (command or "wacli").strip()
        self.store = (store or "").strip()
        self.timeout = timeout
        self._executable = ""
        self._checked = False

    # ------------------------------------------------------------ доступность
    def executable(self) -> str:
        import shutil

        if not self._executable:
            self._executable = shutil.which(self.command) or ""
        return self._executable

    def available(self) -> tuple[bool, str]:
        if not self.executable():
            return False, (f"не найдена программа «{self.command}» — поставьте wacli "
                           f"(https://wacli.sh) и свяжите телефон по QR: "
                           f"{self.command} auth")
        if not self._checked:
            try:
                self._run(["doctor"], json_output=False)
                self._checked = True
            except ProviderError as exc:
                return False, f"wacli не готов: {exc}"
        return True, "готов (через wacli)"

    def _base(self) -> list[str]:
        command = [self.executable() or self.command]
        if self.store:
            command += ["--store", self.store]
        return command

    def _run(self, args: list[str], *, json_output: bool = True,
             env_extra: dict[str, str] | None = None) -> Any:
        import os

        command = self._base() + (["--json"] if json_output else []) + args
        env = os.environ.copy()
        env["WACLI_READONLY"] = "0"
        env.update(env_extra or {})
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=self.timeout, env=env)
        except FileNotFoundError as exc:
            raise ProviderError(f"программа «{command[0]}» не найдена") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"wacli не ответил за {self.timeout:g} с") from exc
        except OSError as exc:
            raise ProviderError(f"не удалось запустить wacli: {exc}") from exc
        if result.returncode != 0:
            reason = (result.stderr or result.stdout or "").strip().splitlines()
            raise ProviderError(reason[-1] if reason else f"wacli вернул код {result.returncode}")
        if not json_output:
            return result.stdout
        text = (result.stdout or "").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ProviderError("wacli ответил не-JSON (ожидается --json)") from exc

    # ------------------------------------------------------------------ контакты
    def resolve(self, query: str, *, limit: int = 20) -> tuple[dict[str, Any] | None, list[str]]:
        """Ищет получателя: возвращает (найденный, список похожих), без угадывания."""
        query = (query or "").strip()
        if not query:
            return None, []
        if re.fullmatch(r"\+?[\d\s()\-]{5,}", query) or "@" in query:
            # телефон или JID передаём как есть: так надёжнее, чем искать по имени
            return {"jid": query, "name": query}, []
        data = self._run(["chats", "list", "--query", query, "--limit", str(limit)])
        items = data.get("chats") if isinstance(data, dict) else data
        items = [item for item in (items or []) if isinstance(item, dict)]
        exact = [item for item in items
                 if str(item.get("name") or item.get("display_name") or "").strip().lower()
                 == query.lower()]
        if len(exact) == 1:
            return exact[0], []
        names = [str(item.get("name") or item.get("jid") or "") for item in items]
        return None, names

    def send(self, jid: str, text: str) -> str:
        data = self._run(["send", "text", "--to", jid, "--message", text, "--no-preview"])
        if isinstance(data, dict) and data.get("store_warning"):
            log.warning("wacli: сообщение ушло, но запись в историю не удалась: %s",
                        data["store_warning"])
        return str((data or {}).get("id", "")) if isinstance(data, dict) else ""

    # --------------------------------------------------------------------- приём
    def poll(self, *, after: str = "", limit: int = 5) -> list[IncomingMessage]:
        """Что нового с прошлой проверки (wacli ведёт локальный поиск по истории)."""
        args = ["messages", "list", "--from-them", "--limit", str(max(1, limit)), "--asc"]
        if after:
            args += ["--after", after]
        data = self._run(args)
        items = data.get("messages") if isinstance(data, dict) else data
        messages: list[IncomingMessage] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("body") or "").strip()
            if not text:
                continue
            messages.append(IncomingMessage(
                channel=self.name,
                sender=str(item.get("sender_name") or item.get("sender") or ""),
                text=text[:500],
                ts=str(item.get("timestamp") or item.get("time") or ""),
                uid=str(item.get("id") or ""),
                conversation=str(item.get("chat_name") or item.get("chat") or ""),
            ))
        return messages

    def qr_hint(self) -> str:
        return (f"Свяжите телефон: {self.command} auth — отсканируйте QR в WhatsApp → "
                f"Связанные устройства")

    def state(self, *, probe: bool = False) -> dict[str, Any]:
        if probe:
            ready, reason = self.available()
        elif self.executable():
            ready, reason = True, "программа найдена, связь телефона проверяется при отправке"
        else:
            ready, reason = False, f"не найдена программа «{self.command}»"
        return {"available": ready, "reason": reason, "command": self.command}


# ------------------------------------------------------------------- сводка
@dataclass
class Channels:
    """Все каналы сообщений вместе: их показывает и навык, и окно."""

    telegram: TelegramChannel | None = None
    mail: MailChannel | None = None
    whatsapp: WhatsAppChannel | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def all(self) -> list[Any]:
        return [item for item in (self.telegram, self.mail, self.whatsapp) if item is not None]

    def get(self, name: str) -> Any:
        key = (name or "").strip().lower()
        aliases = {
            "телеграм": "telegram", "телеграмм": "telegram", "тг": "telegram", "telegram": "telegram",
            "почта": "mail", "мейл": "mail", "email": "mail", "mail": "mail",
            "gmail": "mail", "письмо": "mail",
            "ватсап": "whatsapp", "вотсап": "whatsapp", "whatsapp": "whatsapp", "ватцап": "whatsapp",
        }
        wanted = aliases.get(key, key)
        return next((item for item in self.all() if item.name == wanted), None)

    def state(self, *, probe: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for channel in self.all():
            try:
                result[channel.name] = channel.state(probe=probe)
            except Exception as exc:  # noqa: BLE001 - состояние не должно падать
                result[channel.name] = {"available": False, "reason": str(exc)}
        return result
