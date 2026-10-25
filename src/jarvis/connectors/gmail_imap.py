"""Коннектор Gmail по IMAP (пароль приложения Google).

Работает по стандартному протоколу без OAuth: нужен только пароль
приложения (myaccount.google.com → Безопасность → Пароли приложений).
Соединение открывается на каждый вызов — медленнее, зато не бывает
«протухших» сессий. Отправка (SMTP) — этап 3, с подтверждением.
"""

from __future__ import annotations

import asyncio
import email
import email.header
import imaplib
import logging
from email.utils import parsedate_to_datetime

from jarvis.connectors.base import Connector, ConnectorError

log = logging.getLogger("jarvis.gmail")

_SNIPPET = 200
_HOST = "imap.gmail.com"


def _decode_header(raw) -> str:
    parts = email.header.decode_header(raw or "")
    out = []
    for value, charset in parts:
        if isinstance(value, bytes):
            out.append(value.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(value)
    return "".join(out)


def _body_text(message: email.message.Message, limit: int = 4000) -> str:
    """Текст письма: предпочитаем text/plain, собираем из частей."""
    if message.is_multipart():
        chunks = []
        for part in message.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True)
                if payload:
                    chunks.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
        text = "\n".join(chunks)
    else:
        payload = message.get_payload(decode=True)
        text = payload.decode(message.get_content_charset() or "utf-8", errors="replace") if payload else ""
    return text.strip()[:limit]


def _format_message(uid: bytes, message: email.message.Message, with_body: bool) -> str:
    subject = _decode_header(message.get("Subject"))
    sender = _decode_header(message.get("From"))
    date_raw = message.get("Date")
    try:
        when = parsedate_to_datetime(date_raw).strftime("%d.%m.%Y %H:%M") if date_raw else "?"
    except Exception:
        when = "?"
    line = f"[uid={uid.decode()}] {when} | {sender} | {subject}"
    if with_body:
        body = _body_text(message, _SNIPPET).replace("\n", " ")
        if body:
            line += f"\n    {body}"
    return line


class GmailImapConnector(Connector):
    name = "gmail"

    def __init__(self, user: str, app_password: str, enabled: bool = True):
        self._user = user
        # Пароли приложений Google показываются с пробелами — они не нужны.
        self._password = app_password.replace(" ", "")
        self._enabled = enabled
        self.problem: str | None = None

    async def start(self) -> bool:
        if not self._enabled:
            self.problem = "gmail отключён в connectors.yaml"
            return False
        if not self._user or not self._password:
            self.problem = "задайте JARVIS_GMAIL_USER и JARVIS_GMAIL_APP_PASSWORD в .env"
            return False
        try:
            await asyncio.to_thread(self._probe)
            return True
        except Exception as e:
            self.problem = f"не удалось подключиться к Gmail: {e}"
            log.warning("Gmail: %s", self.problem)
            return False

    def _probe(self) -> None:
        with self._connect() as mail:
            mail.select("INBOX")

    def _connect(self) -> imaplib.IMAP4_SSL:
        mail = imaplib.IMAP4_SSL(_HOST)
        try:
            mail.login(self._user, self._password)
        except imaplib.IMAP4.error as e:
            raise ConnectorError(
                f"Gmail не принял логин/пароль приложения ({e}). "
                "Проверьте JARVIS_GMAIL_APP_PASSWORD."
            ) from e
        return mail

    # ---------- чтение ----------

    def _fetch_uids(self, mail, criteria: list[str], limit: int) -> list[bytes]:
        status, data = mail.uid("SEARCH", None, *criteria)
        if status != "OK":
            raise ConnectorError("Gmail: ошибка поиска")
        return data[0].split()[-limit:]

    def _fetch_messages(self, uids: list[bytes], with_body: bool) -> list[str]:
        lines = []
        with self._connect() as mail:
            mail.select("INBOX")
            for uid in reversed(uids):  # новые сверху
                status, data = mail.uid("FETCH", uid, "(RFC822)")
                if status != "OK" or not data or data[0] is None:
                    continue
                raw = data[0][1]
                message = email.message_from_bytes(raw)
                lines.append(_format_message(uid, message, with_body))
        return lines

    def _unread_sync(self, limit: int) -> str:
        with self._connect() as mail:
            mail.select("INBOX")
            uids = self._fetch_uids(mail, ["UNSEEN"], limit)
        if not uids:
            return "Непрочитанных писем нет."
        lines = self._fetch_messages(uids, with_body=True)
        return f"Непрочитанные письма ({len(lines)}):\n" + "\n".join(lines)

    def _search_sync(self, query: str, limit: int) -> str:
        with self._connect() as mail:
            mail.select("INBOX")
            try:
                # IMAP-синтаксис Gmail: FROM/TO/SUBJECT/TEXT — запрос целиком.
                uids = self._fetch_uids(mail, ['TEXT', query.replace('"', "")], limit)
            except imaplib.IMAP4.error:
                uids = []
            if not uids:
                # Кириллица иногда не ищется по TEXT — фильтруем последние письма вручную.
                uids = self._fetch_uids(mail, ["ALL"], 100)
                lines = []
                with self._connect() as mail2:
                    mail2.select("INBOX")
                    for uid in reversed(uids):
                        status, data = mail2.uid("FETCH", uid, "(RFC822)")
                        if status != "OK" or not data or data[0] is None:
                            continue
                        message = email.message_from_bytes(data[0][1])
                        haystack = " ".join(
                            filter(None, [
                                _decode_header(message.get("Subject")),
                                _decode_header(message.get("From")),
                                _body_text(message, 5000),
                            ])
                        )
                        if query.lower() in haystack.lower():
                            lines.append(_format_message(uid, message, with_body=False))
                        if len(lines) >= limit:
                            break
                if not lines:
                    return f"По запросу {query!r} ничего не найдено."
                return "Результаты поиска (последние письма):\n" + "\n".join(lines)
        lines = self._fetch_messages(uids, with_body=False)
        if not lines:
            return f"По запросу {query!r} ничего не найдено."
        return "Результаты поиска Gmail:\n" + "\n".join(lines)

    def _read_sync(self, uid: str) -> str:
        with self._connect() as mail:
            mail.select("INBOX")
            status, data = mail.uid("FETCH", uid.encode(), "(RFC822)")
        if status != "OK" or not data or data[0] is None:
            return f"Письмо uid={uid} не найдено."
        message = email.message_from_bytes(data[0][1])
        header = _format_message(uid.encode(), message, with_body=False)
        body = _body_text(message, limit=4000) or "(без текстовой части)"
        return f"{header}\n\n{body}"

    # ---------- интерфейс коннектора (обёртки в поток) ----------

    async def unread_summary(self, limit_dialogs: int = 20) -> str:
        return await asyncio.to_thread(self._unread_sync, limit_dialogs)

    async def search_messages(self, query: str, chat: str | None = None, limit: int = 10) -> str:
        return await asyncio.to_thread(self._search_sync, query, limit)

    async def read_message(self, uid: str) -> str:
        return await asyncio.to_thread(self._read_sync, uid)

    async def recent_messages(self, chat: str, limit: int = 10) -> str:
        # Для почты «последние» = непрочитанные + свежие; используем поиск всех.
        return await asyncio.to_thread(self._search_sync, "", max(limit, 1)) if chat else ""

    async def stop(self) -> None:
        pass  # соединения открываются на каждый вызов
