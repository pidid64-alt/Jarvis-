"""Тесты Google OAuth-коннектора: разбор ответов API без сети."""


from jarvis.connectors.base import ConnectorError
from jarvis.connectors.google_oauth import (
    SCOPES,
    GoogleOAuthConnector,
    doc_markdown,
    gmail_body_text,
    sheet_rows_to_text,
)


def test_scopes_are_read_only():
    # Принцип минимальных прав: все скоупы только на чтение.
    assert all(scope.endswith(".readonly") for scope in SCOPES)
    assert len(SCOPES) == 4  # gmail, docs, sheets, drive


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
