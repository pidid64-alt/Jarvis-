"""Чтение веб-страниц: заголовок, текст и ссылки — без внешних библиотек.

Нужно, чтобы можно было сказать «прочитай сайт … и найди …»: Jarvis забирает
страницу, вытаскивает из неё чистый текст и ссылки, а дальше навык отвечает по
содержимому (через модель, если она настроена, иначе — куском текста).

Внешних зависимостей нет: разбор HTML — стандартным ``html.parser``, запрос —
общим ``HttpClient`` (таймауты, предохранитель, никаких повторов).
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from ..core.errors import ProviderError
from .http import CircuitBreaker, HttpClient

#: сколько текста страницы вообще держим в памяти (страницы бывают гигантские)
MAX_TEXT_CHARS = 20000
#: сколько символов отдаём модели или показываем человеку
SUMMARY_CHARS = 8000

SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "form", "button"}
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section",
              "article", "header", "footer", "table", "ul", "ol", "blockquote", "pre"}

_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MANY_NEWLINES_RE = re.compile(r"\n{2,}")


class _TextExtractor(HTMLParser):
    """Собирает текст и ссылки, выбрасывая служебные теги."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._skip_depth = 0
        self._in_title = False
        self._link_href: str | None = None
        self._link_text: list[str] = []

    # ------------------------------------------------------------- разбор тегов
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "a":
            self._link_href = data.get("href") or ""
            self._link_text = []
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            return
        if tag == "a" and self._link_href is not None:
            text = _collapse(" ".join(self._link_text))
            if text and self._link_href:
                self.links.append({"text": text[:120], "href": self._link_href})
            self._link_href = None
            self._link_text = []
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._link_href is not None:
            self._link_text.append(data)
        self.parts.append(data)


def _collapse(text: str) -> str:
    text = _WS_RE.sub(" ", text.replace("\xa0", " "))
    lines = [line.strip() for line in text.splitlines()]
    return _MANY_NEWLINES_RE.sub("\n", "\n".join(line for line in lines if line))


def normalize_url(target: str) -> str:
    """``example.com/page`` → ``https://example.com/page``."""
    target = (target or "").strip().strip("«»\"'").replace(" ", "")
    if not target:
        raise ProviderError("не указан адрес сайта")
    if not urlparse(target).scheme:
        target = "https://" + target
    parsed = urlparse(target)
    if not parsed.netloc or "." not in parsed.netloc:
        raise ProviderError(f"«{target}» не похоже на адрес сайта")
    return target


def extract(html_text: str, base_url: str = "") -> dict[str, Any]:
    """Разбирает HTML: заголовок, текст, ссылки."""
    parser = _TextExtractor()
    try:
        parser.feed(html_text)
    except Exception:  # noqa: BLE001 - кривая разметка не должна ломать чтение
        pass
    title = _collapse(" ".join(parser.title_parts))[:200]
    text = _collapse("".join(parser.parts))[:MAX_TEXT_CHARS]
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in parser.links:
        href = urljoin(base_url, link["href"]) if base_url else link["href"]
        if href.startswith(("javascript:", "mailto:", "#")) or href in seen:
            continue
        seen.add(href)
        links.append({"text": link["text"], "href": href})
        if len(links) >= 200:
            break
    return {"title": title, "text": text, "links": links}


def find_fragments(text: str, words: list[str], *, limit: int = 3, width: int = 300) -> list[str]:
    """Куски текста, где встречаются нужные слова (все — если получится, иначе любые)."""
    words = [word for word in (words or []) if len(word) > 2]
    if not words:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lowered = [line.lower() for line in lines]

    def collect(required: list[str]) -> list[str]:
        found: list[str] = []
        for line, low in zip(lines, lowered):
            if any(word in low for word in required):
                index = low.find(required[0]) if required[0] in low else 0
                start = max(0, index - width // 3)
                found.append(line[start:start + width].strip())
            if len(found) >= limit:
                break
        return found

    return collect(words) or collect(words[:1])


class PageReader:
    """Забирает страницу и отдаёт её текст и ссылки."""

    def __init__(self, *, timeout: float = 15.0, max_chars: int = SUMMARY_CHARS):
        self.timeout = timeout
        self.max_chars = max_chars
        self.client = HttpClient(timeout=timeout, retries=0,
                                 breaker=CircuitBreaker("pages", threshold=3, cooldown=30.0))

    def available(self) -> tuple[bool, str]:
        return True, "готов"

    def fetch(self, target: str) -> dict[str, Any]:
        """Возвращает ``{url, title, text, links}`` или поднимает понятную ошибку."""
        url = normalize_url(target)
        try:
            response = self.client.get(url, headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru,en",
            })
        except ProviderError as exc:
            raise ProviderError(f"не удалось открыть {url}: {exc}") from exc
        content_type = str(response.headers.get("Content-Type", "text/html")).lower()
        body = response.body
        if "text/html" not in content_type and "xml" not in content_type:
            raise ProviderError(f"по ссылке {url} не страница ({content_type.split(';')[0]})")
        page = extract(body.decode("utf-8", "replace"), base_url=url)
        page["url"] = url
        page["text"] = page["text"][: self.max_chars]
        page["links"] = page["links"][:50]
        if not page["text"]:
            raise ProviderError(f"на странице {url} не нашлось текста")
        return page

    def state(self) -> dict[str, Any]:
        return {"available": True, "reason": "готов"}
