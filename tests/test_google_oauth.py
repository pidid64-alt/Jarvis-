"""Тесты Google OAuth-коннектора: разбор ответов API без сети."""


from jarvis.connectors.base import ConnectorError
from jarvis.connectors.google_oauth import (
    SCOPES,
    GoogleOAuthConnector,
    doc_markdown,
    gmail_body_text,
    sheet_rows_to_text,
)


def test_scopes_minimal_privilege():
    # Чтение везде + отправка только почты; никаких full-access/destructive скоупов.
    assert len(SCOPES) == 5
    read_only = [s for s in SCOPES if s.endswith(".readonly")]
    assert len(read_only) == 4  # gmail, documents, spreadsheets, drive
    assert "https://www.googleapis.com/auth/gmail.send" in SCOPES


def test_build_raw_email():
    import base64
    from email import message_from_bytes
    from email.header import decode_header

    raw_b64 = GoogleOAuthConnector.build_raw_email(
        "drug@example.com", "Встреча в кафе", "Привет! Давай в 18:00."
    )
    mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64))
    assert mime["To"] == "drug@example.com"
    # Кирилличная тема кодируется RFC2047 — декодируем для сравнения.
    subject = "".join(
        part.decode(charset or "utf-8") if isinstance(part, bytes) else part
        for part, charset in decode_header(mime["Subject"])
    )
    assert subject == "Встреча в кафе"
    # Тело письма достаём через get_payload(decode=True).
    body = mime.get_payload(decode=True).decode("utf-8")
    assert "18:00" in body


def test_doc_markdown_extraction():
    document = {
        "body": {
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": "Заголовок отчёта"}}],
                                "paragraphStyle": {"namedStyleType": "HEADING_1"}}},
                {"paragraph": {"elements": [{"textRun": {"content": "Обычный абзац. "},
                                              }, {"textRun": {"content": "Продолжение."}}]}},
                {"paragraph": {"elements": [{"textRun": {"content": "  "}}]}},  # пустой
            ]
        }
    }
    text = doc_markdown(document)
    assert "# Заголовок отчёта" in text
    assert "Обычный абзац. Продолжение." in text
    assert text.count("\n") == 1  # пустая строка отброшена


def test_sheet_rows_to_text():
    values = [["Статья", "Сумма"], ["Продукты", 12000], [None, ""], ["Итого", 35000]]
    text = sheet_rows_to_text(values)
    assert "Статья | Сумма" in text
    assert "Продукты | 12000" in text
    assert "Итого | 35000" in text
    # Пустая строка отбрасывается.
    assert len(text.splitlines()) == 3


def test_gmail_body_text_plain():
    import base64

    encoded = base64.urlsafe_b64encode("Заказ отправлен, трек RF123.".encode()).decode()
    payload = {"mimeType": "text/plain", "body": {"data": encoded}}
    assert "RF123" in gmail_body_text(payload)


def test_gmail_body_text_from_parts():
    import base64

    encoded = base64.urlsafe_b64encode("<p>HTML-версия письма</p>".encode()).decode()
    plain = base64.urlsafe_b64encode("текстовая версия".encode()).decode()
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": plain}},
        {"mimeType": "text/html", "body": {"data": encoded}},
    ]}
    assert gmail_body_text(payload) == "текстовая версия"


def test_connector_states(tmp_path):
    connector = GoogleOAuthConnector(tmp_path / "credentials.json", tmp_path / "token.json")
    assert connector.authorized is False
    assert "credentials.json" in connector.problem_text()

    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")
    assert connector.credentials_configured is True
    assert "jarvis google login" in connector.problem_text()

    try:
        connector.login_interactive()
        assert False, "должен был бросить ConnectorError"
    except ConnectorError:
        pass
