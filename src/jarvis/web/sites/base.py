"""Базовый интерфейс парсера сайта.

Новый сайт = новый класс с matches() + parse(); зарегистрируйте его в
SiteRegistry, и инструмент read_url начнёт использовать его автоматически.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from jarvis.core.tools.base import ToolError
from jarvis.web.fetcher import FetchResult


class ParseError(ToolError):
    pass


@dataclass
class ParsedPage:
    url: str
    title: str
    markdown: str


class SiteParser(ABC):
    """Интерфейс плагина: fetch (общий загрузчик) → parse → normalize."""

    name: str = "base"

    @abstractmethod
    def matches(self, url: str) -> bool:
        """Подходит ли парсер для URL (обычно — проверка домена)."""

    async def fetch(self, url: str) -> FetchResult:
        """Загрузка через общий вежливый загрузчик (robots/rate limit/кеш)."""
        raise ParseError("Парсер не поддерживает самостоятельную загрузку")

    @abstractmethod
    async def parse(self, html: str, url: str) -> ParsedPage:
        """HTML → ParsedPage (заголовок + markdown)."""

    def normalize(self, page: ParsedPage, max_chars: int) -> str:
        """Приведение к итоговому виду для модели (обрезка, оформление)."""
        markdown = page.markdown
        truncated = False
        if len(markdown) > max_chars:
            markdown = markdown[:max_chars]
            truncated = True
        header = f"# {page.title}\nИсточник: {page.url}\n\n" if page.title else f"Источник: {page.url}\n\n"
        return header + markdown + ("\n\n[…контент обрезан]" if truncated else "")


class SiteRegistry:
    """Выбирает парсер по URL; последний — всегда generic."""

    def __init__(self):
        self._parsers: list[SiteParser] = []

    def register(self, parser: SiteParser) -> None:
        self._parsers.append(parser)

    def pick(self, url: str) -> SiteParser:
        for parser in self._parsers:
            if parser.matches(url):
                return parser
        raise ParseError(f"Для {url} нет подходящего парсера (включая generic)")
