"""Извлечение основного контента страницы: trafilatura → markdown.

trafilatura синхронная, поэтому уходим в поток через to_thread.
"""

from __future__ import annotations

import asyncio
import logging

from jarvis.core.tools.base import ToolError

log = logging.getLogger("jarvis.web.extract")


class ExtractError(ToolError):
    pass


def html_to_markdown(html: str, url: str) -> tuple[str, str]:
    """Возвращает (заголовок, markdown). Блокирующая — вызывать через to_thread."""
    import trafilatura

    try:
        markdown = trafilatura.extract(
            html, output_format="markdown", include_tables=True, include_links=True, url_hint=url
        )
    except TypeError:  # старые версии без url_hint
        markdown = trafilatura.extract(html, output_format="markdown", include_tables=True, include_links=True)

    if not markdown or len(markdown.strip()) < 40:
        # Фолбэк: хотя бы голый текст (случаи очень простых/коротких страниц).
        markdown = trafilatura.extract(html, output_format="txt") or ""
        markdown = markdown.strip()
        if len(markdown) < 10:
            raise ExtractError(
                f"Не удалось извлечь основной контент из {url} "
                "(страница пустая или полностью динамическая — нужен Playwright, этап 1)."
            )

    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        if meta:
            title = meta.title or ""
    except Exception:
        log.debug("Метаданные %s не извлеклись", url, exc_info=True)
    return title, markdown.strip()


async def extract_async(html: str, url: str) -> tuple[str, str]:
    return await asyncio.to_thread(html_to_markdown, html, url)
