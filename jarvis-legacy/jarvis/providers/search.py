"""Веб-поиск: ddgs, встроенный запрос к DuckDuckGo или свой SearxNG.

Внешних обязательных зависимостей нет: запасной путь использует
Instant Answer API и HTML-выдачу DuckDuckGo через стандартную библиотеку.

Важно: в части сетей DuckDuckGo недоступен целиком. Поэтому, если в настройках
задан адрес своего поисковика (``search.instance`` — любой SearxNG), он идёт
первым, а в тексте ошибки видно, кто именно не ответил.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse

from ..core.errors import ProviderError
from .http import CircuitBreaker, HttpClient

log = logging.getLogger("jarvis.providers.search")

_HREF_RE = re.compile(r'<a rel="nofollow" class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_SNIPPET_RE = re.compile(r'<a class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(value: str) -> str:
    return html.unescape(_TAG_RE.sub("", value)).strip()


class WebSearch:
    """Поиск в интернете. Возвращает список сниппетов {title, body, href}."""

    def __init__(self, *, max_results: int = 5, timeout: float = 12.0, enabled: bool = True,
                 instance: str = "", engine: str = "auto"):
        self.max_results = max_results
        self.enabled = enabled
        self.instance = (instance or "").strip().rstrip("/")
        self.engine = (engine or "auto").strip().lower()
        if self.engine not in ("auto", "duckduckgo", "searx"):
            log.warning("неизвестный поисковик %r — работаю как auto", engine)
            self.engine = "auto"
        self.client = HttpClient(timeout=timeout, retries=1, breaker=CircuitBreaker("search", threshold=3,
                                                                                     cooldown=60.0))

    # ------------------------------------------------------------------ state
    def available(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "поиск выключен в настройках"
        if self.engine == "searx" or (self.engine == "auto" and self.instance):
            if not self.instance:
                return False, "выбран свой поисковик, но не задан его адрес (search.instance)"
            return True, f"готов (свой поисковик: {self.instance})"
        try:
            import ddgs  # noqa: F401

            return True, "готов (ddgs)"
        except ImportError:
            return True, "готов (встроенный поиск DuckDuckGo)"

    # ------------------------------------------------------------------- call
    def search(self, query: str) -> list[dict[str, str]]:
        query = (query or "").strip()
        if not query:
            raise ProviderError("пустой поисковый запрос")
        ready, reason = self.available()
        if not ready:
            raise ProviderError(reason)

        # Порядок попыток: свой поисковик (если задан), затем три пути DuckDuckGo.
        attempts: list[tuple[str, object]] = []
        if self.engine == "searx" or (self.engine == "auto" and self.instance):
            attempts.append(("свой поисковик", self._via_searx))
        if self.engine != "searx":
            attempts.append(("ddgs", self._via_ddgs))
            attempts.append(("DuckDuckGo Instant Answer", self._via_instant_answer))
            attempts.append(("html.duckduckgo.com", self._via_html))

        problems: list[str] = []
        for name, step in attempts:
            try:
                snippets = step(query)  # type: ignore[operator]
            except ProviderError as exc:  # понятная причина — запомним и покажем
                problems.append(f"{name}: {exc}")
                continue
            if snippets:
                log.info("поиск «%s» ответил через %s", query, name)
                return snippets
        if problems:
            raise ProviderError("ни один из сервисов не ответил — " + "; ".join(problems[:3]))
        raise ProviderError("ни один из сервисов ничего не вернул по этому запросу")

    def _via_ddgs(self, query: str) -> list[dict[str, str]]:
        try:
            from ddgs import DDGS
        except ImportError:
            return []
        try:
            with DDGS() as engine:
                items = list(engine.text(query, max_results=self.max_results))
        except Exception as exc:  # noqa: BLE001 - библиотека кидает свои исключения
            log.warning("ddgs не сработал: %s", exc)
            return []
        return [
            {"title": item.get("title", ""), "body": item.get("body", ""), "href": item.get("href", "")}
            for item in items
            if item.get("title") or item.get("body")
        ]

    def _via_searx(self, query: str) -> list[dict[str, str]]:
        """Запрос к своему SearxNG: /search?format=json."""
        if not self.instance:
            raise ProviderError("адрес поисковика не задан")
        url = (f"{self.instance}/search?format=json&language=ru&safesearch=1&q="
               + urllib.parse.quote_plus(query))
        try:
            body = self.client.get(url, headers={"Accept": "application/json"}).body
        except Exception as exc:  # noqa: BLE001 - адрес мог быть введён с ошибкой
            raise ProviderError(f"не ответил ({exc})") from exc
        try:
            data = json.loads(body.decode("utf-8", "replace") or "{}")
        except ValueError as exc:
            raise ProviderError("ответ не в формате json (включите format=json в SearxNG)") from exc
        results: list[dict[str, str]] = []
        for item in (data.get("results") or [])[: self.max_results]:
            if not isinstance(item, dict):
                continue
            results.append({
                "title": str(item.get("title", "")),
                "body": str(item.get("content", "") or item.get("snippet", "")),
                "href": str(item.get("url", "")),
            })
        return [item for item in results if item["title"] or item["body"]]

    def _via_instant_answer(self, query: str) -> list[dict[str, str]]:
        url = ("https://api.duckduckgo.com/?format=json&no_html=1&skip_disambig=1&q="
               + urllib.parse.quote_plus(query))
        try:
            data = json.loads(self.client.get(url).body.decode("utf-8", "replace") or "{}")
        except Exception as exc:  # noqa: BLE001
            log.warning("Instant Answer недоступен: %s", exc)
            return []
        results: list[dict[str, str]] = []
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading") or query,
                "body": data["AbstractText"],
                "href": data.get("AbstractURL", ""),
            })
        for topic in (data.get("RelatedTopics") or [])[: self.max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "").split(" - ")[0][:80],
                    "body": topic.get("Text", ""),
                    "href": (topic.get("FirstURL") or ""),
                })
        return results[: self.max_results]

    def _via_html(self, query: str) -> list[dict[str, str]]:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        try:
            page = self.client.get(url, headers={"Accept-Language": "ru,en"}).body.decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"поиск недоступен: {exc}") from exc
        titles = [(href, _strip_tags(title)) for href, title in _HREF_RE.findall(page)]
        snippets = [_strip_tags(item) for item in _SNIPPET_RE.findall(page)]
        results: list[dict[str, str]] = []
        for index, (href, title) in enumerate(titles[: self.max_results]):
            body = snippets[index] if index < len(snippets) else ""
            results.append({"title": title, "body": body, "href": urllib.parse.unquote(href)})
        return results

    def state(self) -> dict[str, object]:
        ready, reason = self.available()
        return {"available": ready, "reason": reason}
