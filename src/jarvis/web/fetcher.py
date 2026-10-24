"""Вежливый загрузчик страниц: robots.txt, rate limit на домен, кеш в SQLite.

Всё, что скачано отсюда, — недоверенные данные: содержимое передаётся модели
только внутри рамки <untrusted> (см. core/prompts.py).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from aiolimiter import AsyncLimiter
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from jarvis.config import WebConfig
from jarvis.core.tools.base import ToolError
from jarvis.memory.db import Database

log = logging.getLogger("jarvis.web.fetcher")

_ROBOTS_TOKEN = "JarvisAssistant"
_MAX_BODY_BYTES = 5_000_000


class FetchError(ToolError):
    """Ошибка загрузки страницы (финальная — попадает модели как наблюдение)."""


@dataclass
class FetchResult:
    url: str
    status: int
    html: str
    from_cache: bool = False


class _NoopLimiter:
    """Заглушка для rate_limit_seconds = 0 (тесты)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


_NOOP_LIMITER = _NoopLimiter()
_UNSET = object()


class WebFetcher:
    def __init__(self, db: Database, config: WebConfig):
        self._db = db
        self._cfg = config
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(config.request_timeout),
            headers={"User-Agent": config.user_agent},
        )
        self._limiters: dict[str, AsyncLimiter] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    async def close(self) -> None:
        await self._client.aclose()

    # ---------- вежливость (общие правила для fetcher и браузера) ----------

    async def ensure_allowed(self, url: str) -> None:
        """Проверяет robots.txt; бросает FetchError, если загрузка запрещена."""
        if not self._cfg.respect_robots:
            return
        parts = urlsplit(url)
        await self._check_robots(url, parts)

    async def wait_domain_slot(self, url: str) -> None:
        """Занимает слот rate limit для домена (перед реальным запросом)."""
        limiter = self._limiter_for(urlsplit(url).netloc)
        await limiter.acquire()

    # ---------- основной метод ----------

    async def fetch(self, url: str, *, use_cache: bool = True) -> FetchResult:
        url = url.strip()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise FetchError(f"Некорректный URL: {url!r}")

        if use_cache:
            cached = await self._cache_get(url)
            if cached is not None:
                return cached

        await self.ensure_allowed(url)

        async with self._limiter_for(parts.netloc):
            try:
                response = await self._get(url)
            except httpx.HTTPError as e:
                raise FetchError(f"Не удалось загрузить {url}: {e}") from e

        if response.status_code >= 400:
            raise FetchError(f"{url} вернул HTTP {response.status_code}")
        if len(response.content) > _MAX_BODY_BYTES:
            raise FetchError(f"{url}: страница больше {_MAX_BODY_BYTES // 1_000_000} МБ, пропускаю")

        html = response.text
        if use_cache:
            await self._cache_put(url, response.status_code, response.headers.get("content-type", ""), response.content)
        return FetchResult(url=url, status=response.status_code, html=html)

    @retry(
        retry=retry_if_exception_type((httpx.TransportError,)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _get(self, url: str) -> httpx.Response:
        return await self._client.get(url)

    # ---------- robots.txt ----------

    async def _check_robots(self, url: str, parts) -> None:
        host = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(host, _UNSET)
        if parser is _UNSET:
            parser = await self._load_robots(host)
            self._robots[host] = parser
        if parser is not None and not parser.can_fetch(_ROBOTS_TOKEN, url):
            raise FetchError(f"robots.txt сайта {host} запрещает загрузку {url}")

    async def _load_robots(self, host: str) -> RobotFileParser | None:
        """None = robots недоступен/пуст (404 и т.п.) — загрузки разрешены."""
        try:
            resp = await self._client.get(f"{host}/robots.txt", timeout=8.0)
        except httpx.HTTPError as e:
            log.warning("robots.txt %s недоступен (%s) — считаю разрешённым", host, e)
            return None
        if resp.status_code >= 400:
            return None
        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        return parser

    # ---------- rate limit ----------

    def _limiter_for(self, netloc: str):
        seconds = max(self._cfg.rate_limit_seconds, 0.0)
        if seconds == 0:
            return _NOOP_LIMITER
        limiter = self._limiters.get(netloc)
        if limiter is None:
            limiter = AsyncLimiter(1, seconds)  # 1 запрос каждые N секунд на домен
            self._limiters[netloc] = limiter
        return limiter

    # ---------- кеш ----------

    async def _cache_get(self, url: str) -> FetchResult | None:
        row = await self._db.fetchone(
            "SELECT fetched_at, status, content_type, body FROM page_cache WHERE url = ?", (url,)
        )
        if row is None:
            return None
        if time.time() - row["fetched_at"] > self._cfg.cache_ttl_seconds:
            return None
        return FetchResult(
            url=url,
            status=row["status"],
            html=bytes(row["body"]).decode("utf-8", errors="replace"),
            from_cache=True,
        )

    async def _cache_put(self, url: str, status: int, content_type: str, body: bytes) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO page_cache (url, fetched_at, status, content_type, body) "
            "VALUES (?, ?, ?, ?, ?)",
            (url, time.time(), status, content_type, body),
        )
