"""Headless-браузер (Playwright): страницы, требующие JavaScript.

Браузер запускается лениво при первом использовании и держится открытым
до остановки Runtime (запуск Chromium стоит 1–2 с). Вежливость соблюдается
общими механизмами WebFetcher: robots.txt и rate limit на домен.
"""

from __future__ import annotations

import asyncio
import logging

from jarvis.core.tools.base import ToolError
from jarvis.web.fetcher import FetchError, WebFetcher

log = logging.getLogger("jarvis.web.browser")

_DEFAULT_WAIT_MS = 2500


class BrowserError(ToolError):
    pass


class BrowserReader:
    def __init__(self, fetcher: WebFetcher, max_chars_hint: int = 200_000):
        self._fetcher = fetcher  # общий вежливый загрузчик: robots + rate limit
        self._max_chars_hint = max_chars_hint
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def fetch_html(self, url: str, wait_ms: int = _DEFAULT_WAIT_MS) -> str:
        """Загружает страницу в Chromium, прокручивает её и возвращает HTML."""
        await self._fetcher.ensure_allowed(url)  # robots.txt
        await self._fetcher.wait_domain_slot(url)  # rate limit на домен

        try:
            browser = await self._ensure_browser()
        except Exception as e:
            raise BrowserError(
                f"Не удалось запустить браузер: {e}. "
                "Выполните `uv run playwright install chromium`."
            ) from e

        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            # Динамический контент и ленивая загрузка: ждём сеть и прокручиваем.
            await page.wait_for_timeout(wait_ms)
            await self._auto_scroll(page)
            html = await page.content()
        except Exception as e:
            raise BrowserError(f"Браузер не смог открыть {url}: {e}") from e
        finally:
            await page.close()

        if len(html) > self._max_chars_hint:
            html = html[: self._max_chars_hint]
        return html

    async def _auto_scroll(self, page) -> None:
        """Плавная прокрутка до низа: подтягивает ленивые блоки и бесконечные ленты."""
        try:
            for _ in range(6):
                done = await page.evaluate(
                    "window.scrollBy(0, window.innerHeight * 0.8); "
                    "window.innerHeight + window.scrollY >= document.body.scrollHeight"
                )
                await page.wait_for_timeout(400)
                if done:
                    break
            await page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            log.debug("Прокрутка не удалась (страница короткая?)", exc_info=True)

    async def _ensure_browser(self):
        async with self._lock:
            if self._browser is not None:
                return self._browser
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            log.info("Chromium запущен (headless)")
            return self._browser

    async def close(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


# Re-export: ошибки загрузчика и браузера обрабатываются инструментом одинаково.
FETCH_ERRORS = (FetchError, BrowserError)
