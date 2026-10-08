"""Навык «Страница сайта»: прочитать страницу и ответить по её содержимому.

Две просьбы:

* «прочитай сайт example.com» — короткий пересказ того, что на странице;
* «найди на сайте example.com контакты» — ответ по содержимому страницы.

Если модель настроена, ответ строит она по тексту страницы; если нет — навык
показывает подходящие куски текста и ссылки. Команды модель не пишет: она только
пересказывает то, что реально есть на странице.
"""

from __future__ import annotations

import re

from jarvis.providers.pages import find_fragments
from jarvis.skills import fail

URL_RE = re.compile(r"(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?", re.IGNORECASE)
#: служебные слова, которые не помогают искать на странице
FILLER = {"сайт", "сайте", "сайта", "страница", "странице", "страницы", "прочитай", "почитай",
          "найди", "поищи", "есть", "ли", "что", "там", "мне", "пожалуйста", "и", "на"}

MAX_MODEL_CHARS = 6000


def _split(target: str) -> tuple[str, str]:
    """Делит фразу на адрес сайта и остальной запрос."""
    target = (target or "").strip()
    match = URL_RE.search(target)
    if not match:
        return "", target
    url = match.group(0).rstrip(".,;:!?)»\"'")
    rest = (target[: match.start()] + " " + target[match.end():]).strip(" ,;:—-")
    return url, rest


def _words(query: str) -> list[str]:
    words = [word for word in re.findall(r"[\w-]+", query.lower()) if word not in FILLER]
    return [word for word in words if len(word) > 2]


def _read_page(ctx, url: str):
    """Общая часть: забирает страницу, ошибки превращает в понятный ответ."""
    try:
        return ctx.providers.read_page(url), None
    except Exception as exc:  # noqa: BLE001 - сеть бывает недоступна, это не падение
        ctx.log.warning("страница не прочитана: %s", exc, exc_info=True)
        reason = str(exc) or "нет связи"
        return None, fail(ctx, "skill.page.failed", reason=reason[:200])


def _ask_model(ctx, system_key: str, prompt: str) -> str:
    if not ctx.providers.llm_available():
        return ""
    answer = ctx.providers.ask_model(prompt, system=ctx.t(system_key))
    return str(answer or "").strip()


def _read(ctx, intent):
    if ctx.providers is None:
        return fail(ctx, "error.provider")
    url, _rest = _split(str(intent.args.get(intent.action.capture_field, "")))
    if not url:
        return fail(ctx, "skill.page.no_url")
    page, error = _read_page(ctx, url)
    if page is None:
        return error
    title = page.get("title") or page["url"]
    text = page.get("text", "")
    ctx.log.info("прочитана страница %s (%d символов)", page["url"], len(text))

    answer = _ask_model(ctx, "skill.page.summary_prompt",
                        f"Адрес: {page['url']}\nЗаголовок: {title}\n\nТекст страницы:\n"
                        f"{text[:MAX_MODEL_CHARS]}")
    if answer:
        return answer
    if not ctx.providers.llm_available():
        return ctx.t("skill.page.summary", title=title, text=text[:400])
    return fail(ctx, "skill.page.failed", reason="модель не ответила")


def _find(ctx, intent):
    if ctx.providers is None:
        return fail(ctx, "error.provider")
    url, rest = _split(str(intent.args.get(intent.action.capture_field, "")))
    if not url:
        return fail(ctx, "skill.page.no_url")
    if not rest:
        return fail(ctx, "skill.page.empty_query")
    page, error = _read_page(ctx, url)
    if page is None:
        return error
    title = page.get("title") or page["url"]
    text = page.get("text", "")
    words = _words(rest)
    ctx.log.info("ищу на странице %s: %s", page["url"], ", ".join(words) or rest)

    answer = _ask_model(ctx, "skill.page.find_prompt",
                        f"Вопрос: {rest}\nАдрес: {page['url']}\n\nТекст страницы:\n"
                        f"{text[:MAX_MODEL_CHARS]}")
    if answer:
        return answer

    fragments = find_fragments(text, words)
    links = [link for link in page.get("links", []) if any(word in link["text"].lower() for word in words)]
    if fragments:
        text_answer = ctx.t("skill.page.found", text=fragments[0])
        if links:
            text_answer += " " + ctx.t("skill.page.links",
                                       links="; ".join(f"{item['text']}: {item['href']}"
                                                       for item in links[:3]))
        return text_answer
    if links:
        return ctx.t("skill.page.links", links="; ".join(
            f"{item['text']}: {item['href']}" for item in links[:5]))
    return fail(ctx, "skill.page.nothing", title=title)


HANDLERS = {"read": _read, "find": _find}
