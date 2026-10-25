"""Коннектор Google через OAuth 2.0 (как интеграции у Claude/arena.ai).

Один логин в браузере → токен с автопродлением → доступ к Gmail API,
Google Docs, Google Sheets и Google Drive (по умолчанию только чтение).

Что нужно пользователю (один раз):
  1. console.cloud.google.com → создать проект
  2. APIs & Services → включить Gmail API, Google Docs API, Google Sheets API, Google Drive API
  3. Credentials → Create credentials → OAuth client ID → Desktop app
  4. Скачать JSON → сохранить в корень проекта как credentials.json
  5. `jarvis google login` — откроется браузер, войти и «Разрешить».

Токен хранится в data/google_token.json; credentials.json и токен —
секреты, они в .gitignore.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path

from jarvis.connectors.base import ConnectorError

log = logging.getLogger("jarvis.google")

# Чтение везде; отправка — только почта (принцип минимальных прав).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

_DOC_URL = re.compile(r"document/d/([a-zA-Z0-9_-]{10,})")
_SHEET_URL = re.compile(r"spreadsheets/d/([a-zA-Z0-9_-]{10,})")
_GMAIL_URL = re.compile(r"#inbox/([a-zA-Z0-9]+)")


# ---------- чистые функции разбора (покрыты тестами) ----------

def doc_markdown(document: dict) -> str:
    """Структура Google Docs API → markdown-подобный текст."""
    lines = []
    for block in document.get("body", {}).get("content", []):
        paragraph = block.get("paragraph")
        if not paragraph:
            continue
        text = "".join(
            element.get("textRun", {}).get("content", "")
            for element in paragraph.get("elements", [])
        )
        if not text.strip():
            continue
        style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
        if style.startswith("HEADING"):
            level = style[-1] if style[-1].isdigit() else "2"
            lines.append("#" * int(level) + " " + text.strip())
        else:
            lines.append(text)
    return "\n".join(lines)


def sheet_rows_to_text(values: list[list], max_rows: int = 200) -> str:
    """Значения листа → текст «ячейка | ячейка»."""
    lines = []
    for row in values[:max_rows]:
        cells = ["" if v is None else str(v) for v in row]
        if any(c.strip() for c in cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def gmail_body_text(payload: dict, limit: int = 4000) -> str:
    """Тело письма из Gmail API (base64url) → текст."""
    import email as email_lib

    def decode(part: dict) -> str:
        data = part.get("body", {}).get("data")
        if not data:
            return ""
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        charset = "utf-8"
        for header in part.get("headers", []):
            if header.get("name", "").lower() == "content-type":
                match = re.search(r'charset="?([\w-]+)', header.get("value", ""))
                if match:
                    charset = match.group(1)
                break
        return raw.decode(charset, errors="replace")

    if payload.get("mimeType") == "text/plain":
        return decode(payload)[:limit]
    chunks = []
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            chunks.append(decode(part))
    text = "\n".join(chunks).strip()
    if not text:  # только HTML-часть — грубо снимаем теги
        html = next((decode(p) for p in payload.get("parts", []) if p.get("mimeType") == "text/html"), "")
        text = re.sub(r"<[^>]+>", " ", html)
        text = email_lib.utils.unquote(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


# ---------- коннектор ----------

class GoogleOAuthConnector:
    name = "google"

    def __init__(self, credentials_path: Path, token_path: Path):
        self._credentials_path = credentials_path
        self._token_path = token_path
        self.problem: str | None = None
        self._creds = None

    @property
    def credentials_configured(self) -> bool:
        return self._credentials_path.exists()

    @property
    def authorized(self) -> bool:
        return self._creds is not None or self._token_path.exists()

    def problem_text(self) -> str:
        if not self.credentials_configured:
            return (
                "нет credentials.json — скачайте OAuth-клиент (Desktop app) из Google Cloud "
                "Console и положите в корень проекта (см. README)"
            )
        if not self.authorized:
            return "не выполнен вход — запустите `jarvis google login`"
        return ""

    # ---------- вход ----------

    def login_interactive(self) -> str:
        """Открывает браузер и ждёт разрешения; сохраняет токен."""
        if not self.credentials_configured:
            raise ConnectorError(self.problem_text())
        from google_auth_oauthlib.flow import InstalledAppFlow

        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(self._credentials_path), SCOPES)
        except Exception as e:
            raise ConnectorError(
                f"credentials.json повреждён или не является OAuth-клиентом Desktop app: {e}"
            ) from e
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(creds.to_json(), encoding="utf-8")
        self._creds = creds
        log.info("Google OAuth: вход выполнен, токен в %s", self._token_path)
        return "Готово: доступ к Gmail, Docs, Sheets и Drive получен (только чтение)."

    def _credentials(self):
        if self._creds is not None:
            return self._creds
        if not self._token_path.exists():
            raise ConnectorError(self.problem_text())
        from google.oauth2.credentials import Credentials

        self._creds = Credentials.from_authorized_user_file(str(self._token_path), SCOPES)
        return self._creds

    def _service(self, name: str, version: str):
        from googleapiclient.discovery import build

        return build(name, version, credentials=self._credentials(), cache_discovery=False)

    # ---------- Drive ----------

    def drive_search_sync(self, query: str, limit: int = 10) -> str:
        service = self._service("drive", "v3")
        result = (
            service.files()
            .list(
                q=f"name contains '{query.replace(chr(39), '')}' and trashed = false",
                pageSize=limit,
                fields="files(id, name, mimeType)",
            )
            .execute()
        )
        files = result.get("files", [])
        if not files:
            return f"В Google Drive ничего не найдено по запросу {query!r}."
        lines = [f"- {f['name']} ({f['mimeType'].split('.')[-1]}, id={f['id']})" for f in files]
        return "Найдено в Google Drive:\n" + "\n".join(lines)

    def _resolve_id(self, name_or_url: str, mime_prefix: str) -> tuple[str, str]:
        """URL/имя → (id, имя). Ищем в Drive файл нужного типа."""
        raw = name_or_url.strip()
        match = _DOC_URL.search(raw) or _SHEET_URL.search(raw)
        if match:
            return match.group(1), raw
        service = self._service("drive", "v3")
        result = (
            service.files()
            .list(
                q=f"name contains '{raw.replace(chr(39), '')}' and mimeType contains '{mime_prefix}' "
                "and trashed = false",
                pageSize=1,
                fields="files(id, name)",
            )
            .execute()
        )
        files = result.get("files", [])
        if not files:
            raise ConnectorError(f"В Google Drive не найден файл {raw!r}.")
        return files[0]["id"], files[0]["name"]

    # ---------- Docs / Sheets ----------

    def read_doc_sync(self, name_or_url: str) -> str:
        doc_id, title = self._resolve_id(name_or_url, "application/vnd.google-apps.document")
        document = self._service("docs", "v1").documents().get(documentId=doc_id).execute()
        text = doc_markdown(document)
        header = document.get("title") or title
        if not text.strip():
            return f"Документ «{header}» пуст."
        return f"Документ «{header}»:\n\n{text}"

    def read_sheet_sync(self, name_or_url: str, sheet_range: str = "A1:Z500") -> str:
        sheet_id, title = self._resolve_id(name_or_url, "application/vnd.google-apps.spreadsheet")
        values = (
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=sheet_range)
            .execute()
            .get("values", [])
        )
        text = sheet_rows_to_text(values)
        if not text:
            return f"Таблица «{title}» пуста."
        return f"Таблица «{title}» (диапазон {sheet_range}):\n\n{text}"

    # ---------- Gmail API (совместимо с сигнатурами IMAP-коннектора) ----------

    @staticmethod
    def _header(message: dict, name: str) -> str:
        for header in message.get("payload", {}).get("headers", []):
            if header.get("name", "").lower() == name:
                return header.get("value", "")
        return ""

    def _list_messages(self, query: str, limit: int) -> list[dict]:
        service = self._service("gmail", "v1")
        result = service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        ids = [m["id"] for m in result.get("messages", [])]
        messages = []
        for message_id in ids:
            full = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            messages.append(full)
        return messages

    def unread_summary_sync(self, limit: int = 20) -> str:
        messages = self._list_messages("is:unread in:inbox", limit)
        if not messages:
            return "Непрочитанных писем нет."
        lines = []
        for m in messages:
            date = m.get("snippet", "")[:150].replace("\n", " ")
            lines.append(f"[uid={m['id']}] {self._header(m, 'From')} | {self._header(m, 'Subject')}\n    {date}")
        return f"Непрочитанные письма ({len(lines)}):\n" + "\n".join(lines)

    def search_sync(self, query: str, limit: int = 10) -> str:
        messages = self._list_messages(query, limit)
        if not messages:
            return f"По запросу {query!r} писем не найдено."
        lines = [
            f"[uid={m['id']}] {self._header(m, 'From')} | {self._header(m, 'Subject')}"
            for m in messages
        ]
        return "Результаты поиска Gmail:\n" + "\n".join(lines)

    def read_sync(self, message_id: str) -> str:
        service = self._service("gmail", "v1")
        m = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        header = f"{self._header(m, 'From')} | {self._header(m, 'Subject')}"
        return f"{header}\n\n{gmail_body_text(m.get('payload', {})) or '(без текстовой части)'}"

    # ---------- async-обёртки (API синхронные — уходим в поток) ----------

    async def drive_search(self, query: str, limit: int = 10) -> str:
        return await asyncio.to_thread(self.drive_search_sync, query, limit)

    async def read_doc(self, name_or_url: str) -> str:
        return await asyncio.to_thread(self.read_doc_sync, name_or_url)

    async def read_sheet(self, name_or_url: str, sheet_range: str = "A1:Z500") -> str:
        return await asyncio.to_thread(self.read_sheet_sync, name_or_url, sheet_range)

    # Сигнатуры как у IMAP-коннектора — инструменты gmail_* работают с обоими.
    async def unread_summary(self, limit_dialogs: int = 20) -> str:
        return await asyncio.to_thread(self.unread_summary_sync, limit_dialogs)

    async def search_messages(self, query: str, chat: str | None = None, limit: int = 10) -> str:
        return await asyncio.to_thread(self.search_sync, query, limit)

    async def read_message(self, uid: str) -> str:
        return await asyncio.to_thread(self.read_sync, uid)

    # ---------- отправка (этап 2.6: письма с подтверждением) ----------

    @staticmethod
    def build_raw_email(to: str, subject: str, body: str) -> str:
        """Собирает MIME-письмо и кодирует в base64url для Gmail API."""
        from email.message import EmailMessage

        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        return base64.urlsafe_b64encode(message.as_bytes()).decode()

    def send_sync(self, to: str, subject: str, body: str) -> str:
        raw = self.build_raw_email(to, subject, body)
        sent = (
            self._service("gmail", "v1")
            .users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        return f"Письмо отправлено (id={sent.get('id')}) на {to}: «{subject}»."

    async def send_message(self, to: str, subject: str, body: str) -> str:
        return await asyncio.to_thread(self.send_sync, to, subject, body)
