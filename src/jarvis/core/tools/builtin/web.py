"""Инструменты веба: чтение страниц (статика и JavaScript) и поиск."""

from __future__ import annotations

from jarvis.config import WebConfig
from jarvis.core.tools.base import Tool, ToolResult
from jarvis.web.browser import BrowserReader
from jarvis.web.search import SearchError, search_web
from jarvis.web.sites.base import SiteRegistry


def create_tools(sites: SiteRegistry, web_cfg: WebConfig, browser: BrowserReader | None = None) -> list[Tool]:
    async def read_url(args: dict) -> ToolResult:
        url = str(args.get("url", "")).strip()
        max_chars = int(args.get("max_chars", web_cfg.max_content_chars))
        render_js = bool(args.get("render_js", False))

        if render_js:
            if browser is None:
                return ToolResult(
                    output="Ошибка: браузер недоступен в этой сборке. Установите: uv run playwright install chromium"
                )
            html = await browser.fetch_html(url)
            parser = sites.pick(url)
            page = await parser.parse(html, url)
            content = parser.normalize(page, max_chars)
            return ToolResult(output=f"Страница {url} (рендер через браузер):\n\n{content}", untrusted=True)

        parser = sites.pick(url)
        fetched = await parser.fetch(url)
        page = await parser.parse(fetched.html, fetched.url)
        content = parser.normalize(page, max_chars)
        note = " (из кеша)" if fetched.from_cache else ""
        return ToolResult(output=f"Страница {url}{note}:\n\n{content}", untrusted=True)

    async def web_search_tool(args: dict) -> ToolResult:
        try:
            result = await search_web(
                str(args.get("query", "")), int(args.get("limit", 5))
            )
        except SearchError as e:
            return ToolResult(output=f"Ошибка поиска: {e}")
        return ToolResult(output=result, untrusted=True)

    read_params: dict = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Полный URL (http/https)"},
            "max_chars": {
                "type": "integer",
                "description": "Лимит длины возвращаемого текста",
            },
            "render_js": {
                "type": "boolean",
                "description": (
                    "true — открыть страницу в headless-браузере (для сайтов, где контент "
                    "подгружается JavaScript: соцсети, SPA, бесконечные ленты)"
                ),
            },
        },
        "required": ["url"],
    }

    tools = [
        Tool(
            name="read_url",
            description=(
                "Прочитать веб-страницу по URL: скачивает, извлекает основной контент "
                "(статья/текст без навигации и рекламы) и возвращает markdown. Если страница "
                "оказалась пустой — вероятно, она динамическая: повтори с render_js=true."
            ),
            parameters=read_params,
            handler=read_url,
            side_effect="read",
            returns_untrusted=True,
            timeout=120.0,
        ),
        Tool(
            name="web_search",
            description=(
                "Поиск по интернету (DuckDuckGo): возвращает заголовки, ссылки и описания. "
                "Для чтения найденной страницы используй read_url."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос"},
                    "limit": {"type": "integer", "description": "Сколько результатов (1-10)"},
                },
                "required": ["query"],
            },
            handler=web_search_tool,
            side_effect="read",
            returns_untrusted=True,
            timeout=60.0,
        ),
    ]
    return tools
