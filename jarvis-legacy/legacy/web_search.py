#!/usr/bin/env python3
"""
web_search.py
-------------
Веб-поиск для Джарвиса через DuckDuckGo (библиотека ddgs, бесплатно,
без ключей). Возвращает сниппеты — их суммаризирует LLM или открывает
браузер jarvis.py.

Никакого shell здесь нет и быть не может: модуль только ЧИТАЕТ выдачу
поиска. Это read-only источник данных.

Все ошибки оборачиваются в SearchError — вызывающий код (handle_search)
ловит и озвучивает «поиск недоступен», ничего не падает.
"""

import logging

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None


class SearchError(Exception):
    """Любая ошибка поиска: нет сети, нет ddgs, пустая выдача."""


def search_snippets(query: str, max_results: int = 5) -> list[dict]:
    """Ищет в DuckDuckGo, возвращает [{'title', 'body', 'href'}, ...].

    Бросает SearchError при любой проблеме (в т.ч. если ddgs не
    установлен — чтобы fallback-ветка говорила «поиск недоступен»).
    """
    if not query or not query.strip():
        raise SearchError("пустой поисковый запрос")
    if DDGS is None:
        raise SearchError("библиотека ddgs не установлена")

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query.strip(), max_results=max_results))
    except Exception as e:
        raise SearchError(f"поиск не удался: {e}") from e

    snippets = []
    for r in raw:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        href = (r.get("href") or "").strip()
        if not (title or body):
            continue
        snippets.append({"title": title, "body": body, "href": href})

    if not snippets:
        raise SearchError("ничего не нашлось")
    logging.info("web_search: %r → %d сниппетов", query, len(snippets))
    return snippets


def snippets_to_context(snippets: list[dict], max_chars: int = 4000) -> str:
    """Собирает сниппеты в текст для суммаризации LLM."""
    lines = []
    total = 0
    for i, s in enumerate(snippets, 1):
        line = f"{i}. {s['title']}. {s['body']}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)
