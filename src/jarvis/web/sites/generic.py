"""Универсальный парсер: любой URL → основной контент в markdown (trafilatura)."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.web.extract import extract_async
from jarvis.web.fetcher import FetchResult, WebFetcher
from jarvis.web.sites.base import ParsedPage, SiteParser


@dataclass
class GenericSiteParser(SiteParser):
    name: str = "generic"
    fetcher: WebFetcher | None = None

    def matches(self, url: str) -> bool:
        return True  # запасной вариант для любых сайтов

    async def fetch(self, url: str) -> FetchResult:
        assert self.fetcher is not None, "В парсер не передан загрузчик"
        return await self.fetcher.fetch(url)

    async def parse(self, html: str, url: str) -> ParsedPage:
        title, markdown = await extract_async(html, url)
        return ParsedPage(url=url, title=title, markdown=markdown)
