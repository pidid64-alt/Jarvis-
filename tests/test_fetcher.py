"""Тесты загрузчика: robots.txt, кеш, rate limit не мешает тестам (0 секунд)."""

import pytest
import respx
from httpx import Response

from jarvis.config import WebConfig
from jarvis.memory.db import Database
from jarvis.web.fetcher import FetchError, WebFetcher

PAGE_HTML = "<html><body><article><p>" + "Текст статьи про тестирование. " * 20 + "</p></article></body></html>"


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


def _cfg() -> WebConfig:
    return WebConfig(rate_limit_seconds=0, cache_ttl_seconds=3600, respect_robots=True)


@respx.mock
async def test_fetch_honors_robots_disallow(db):
    respx.get("https://site.test/robots.txt").mock(
        return_value=Response(200, text="User-agent: *\nDisallow: /")
    )
    fetcher = WebFetcher(db, _cfg())
    with pytest.raises(FetchError, match="robots"):
        await fetcher.fetch("https://site.test/secret")
    await fetcher.close()


@respx.mock
async def test_fetch_success_and_cache(db):
    respx.get("https://site.test/robots.txt").mock(return_value=Response(404))
    page = respx.get("https://site.test/page").mock(return_value=Response(200, text=PAGE_HTML))

    fetcher = WebFetcher(db, _cfg())
    first = await fetcher.fetch("https://site.test/page")
    assert first.from_cache is False
    assert "Текст статьи" in first.html

    second = await fetcher.fetch("https://site.test/page")
    assert second.from_cache is True
    assert page.call_count == 1  # повторно сеть не трогали
    await fetcher.close()


@respx.mock
async def test_fetch_http_error(db):
    respx.get("https://site.test/robots.txt").mock(return_value=Response(404))
    respx.get("https://site.test/404").mock(return_value=Response(404))
    fetcher = WebFetcher(db, _cfg())
    with pytest.raises(FetchError, match="404"):
        await fetcher.fetch("https://site.test/404")
    await fetcher.close()


async def test_fetch_invalid_url(db):
    fetcher = WebFetcher(db, _cfg())
    with pytest.raises(FetchError):
        await fetcher.fetch("ftp://nope")
    with pytest.raises(FetchError):
        await fetcher.fetch("не-url")
    await fetcher.close()
