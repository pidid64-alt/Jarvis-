"""Веб-поиск через DuckDuckGo (ddgs) — без API-ключей, бесплатно.

Синхронный клиент ddgs уходит в поток через to_thread.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("jarvis.web.search")


class SearchError(RuntimeError):
    pass


def _search_sync(query: str, max_results: int) -> list[dict]:
    from ddgs import DDGS

    with DDGS() as ddgs:
        return list(ddgs.text(query, region="ru-ru", safesearch="moderate", max_results=max_results))


def format_results(results: list[dict], query: str) -> str:
    """Чистая функция форматирования — покрыта юнит-тестом."""
    if not results:
        return f"По запросу {query!r} ничего не найдено."
    lines = [f"Результаты поиска по запросу {query!r}:"]
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        href = (r.get("href") or r.get("url") or "").strip()
        body = (r.get("body") or "").strip().replace("\n", " ")[:200]
        lines.append(f"{i}. {title}\n   {href}\n   {body}")
    lines.append("(Прочитать страницу целиком можно инструментом read_url.)")
    return "\n".join(lines)


async def search_web(query: str, max_results: int = 5) -> str:
    query = query.strip()
    if not query:
        raise SearchError("Пустой поисковый запрос")
    try:
        results = await asyncio.to_thread(_search_sync, query, max(1, min(max_results, 10)))
    except Exception as e:
        raise SearchError(f"Поиск не удался: {e}") from e
    return format_results(results, query)
