"""Тесты документов (PDF/DOCX/XLSX), приложений и разбора писем Gmail."""

import email

import pytest

from jarvis.config import Settings
from jarvis.connectors.gmail_imap import GmailImapConnector, _body_text, _format_message
from jarvis.core.tools.builtin.apps import _normalize
from jarvis.core.tools.builtin.documents import _docx_text, _pdf_text, _xlsx_text


@pytest.fixture
def docs_dir(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    return docs


def test_pdf_reading(docs_dir):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    # Простейший PDF без текста не даст извлечь строки — проверяем, что не падает.
    path = docs_dir / "blank.pdf"
    with path.open("wb") as f:
        writer.write(f)
    assert isinstance(_pdf_text(path, max_pages=5), str)


def test_docx_reading(docs_dir):
    import docx

    doc = docx.Document()
    doc.add_paragraph("Отчёт за октябрь")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Товар"
    table.cell(0, 1).text = "Цена"
    table.cell(1, 0).text = "Кофе"
    table.cell(1, 1).text = "350"
    path = docs_dir / "otchet.docx"
    doc.save(str(path))

    text = _docx_text(path)
    assert "Отчёт за октябрь" in text
    assert "Кофе | 350" in text
    assert "Товар | Цена" in text


def test_xlsx_reading(docs_dir):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Бюджет"
    sheet.append(["Статья", "Сумма"])
    sheet.append(["Продукты", 12000])
    workbook.save(str(docs_dir / "budget.xlsx"))

    text = _xlsx_text(docs_dir / "budget.xlsx", max_rows=50)
    assert "[Лист: Бюджет]" in text
    assert "Продукты | 12000" in text


def test_document_tool_reports_missing_file(tmp_path):
    from jarvis.core.tools.builtin.documents import create_tools

    settings = Settings(data_dir=tmp_path, gmail_user="", gmail_app_password="")
    tools = {t.name: t for t in create_tools(settings)}
    result = __import__("asyncio").run(tools["read_document"].handler({"path": "нет такого.docx"}))
    assert "не найден" in result.output


# ---------- приложения ----------

def test_app_name_normalization():
    assert _normalize("блокнот") == "notepad.exe"
    assert _normalize("Калькулятор") == "calc.exe"
    assert _normalize("chrome") == "chrome.exe"
    assert _normalize("notepad.exe") == "notepad.exe"


def test_close_app_policy_is_gated():
    """Закрытие приложения требует подтверждения — класс outbound."""
    from jarvis.core.tools.builtin.apps import create_tools

    tools = {t.name: t for t in create_tools()}
    assert tools["open_app"].side_effect == "write"
    assert tools["close_app"].side_effect == "outbound"  # гейт подтверждений


# ---------- Gmail: разбор писем без сети ----------

def test_gmail_password_spaces_stripped():
    connector = GmailImapConnector("user@gmail.com", "abcd efgh ijkl mnop")
    assert connector._password == "abcdefghijklmnop"


def test_gmail_message_parsing():
    raw = (
        b"From: =?utf-8?B?0J/RgNC40LLQtdGCIQ==?= <shop@example.com>\r\n"
        b"Subject: =?utf-8?B?0J/RgNC10LLRgdC10YAg0Y3RgtCw0Lw=?=\r\n"
        b"Date: Sat, 25 Oct 2026 10:00:00 +0300\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        + "Заказ 123 отправлен, трек-номер RF123456789.".encode()
    )
    message = email.message_from_bytes(raw)
    line = _format_message(b"42", message, with_body=True)
    assert "uid=42" in line
    assert "Заказ 123 отправлен" in line
    body = _body_text(message)
    assert "RF123456789" in body
