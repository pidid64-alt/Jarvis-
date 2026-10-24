"""Тесты извлечения контента: навигация/реклама отбрасывается, статья остаётся."""

from jarvis.web.extract import html_to_markdown

PAGE = """<html><head><title>Заголовок страницы</title></head><body>
<nav><a href="/">Главная</a> <a href="/about">О сайте</a></nav>
<div class="ads">Купи слона! Реклама со скидкой!</div>
<article>
<h1>Как работает асинхронный Python</h1>
<p>Asyncio — событийный цикл, который позволяет выполнять неблокирующие операции
ввода-вывода. Это основа производительности современных сетевых сервисов.</p>
<p>Ключевые компоненты: корутины, задачи и события. Вместе они образуют модель
кооперативной многозадачности, где код сам сообщает, когда готов продолжить.</p>
</article>
<footer>© 2026 Все права защищены. Контакты: mail@example.com</footer>
</body></html>"""


def test_extracts_main_content_as_markdown():
    title, markdown = html_to_markdown(PAGE, "https://example.com/article")
    # trafilatura может взять заголовок из <title> или из H1 статьи — оба варианта верны.
    assert title in {"Заголовок страницы", "Как работает асинхронный Python"}
    assert "Asyncio" in markdown
    assert "корутины" in markdown
    # Мусор не должен попасть в основной контент.
    assert "Купи слона" not in markdown


def test_tiny_page_raises_extract_error():
    import pytest

    from jarvis.web.extract import ExtractError

    with pytest.raises(ExtractError):
        html_to_markdown("<html><body><p>ок</p></body></html>", "https://x.test/1")
