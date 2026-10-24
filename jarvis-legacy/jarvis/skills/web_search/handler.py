"""Навык «Поиск в интернете»: сначала ищем, потом коротко объясняем (через модель)."""

from __future__ import annotations

import re

from jarvis.skills import fail

URL_RE = re.compile(r"^(?:https?://)?([\w-]+\.)+[a-z]{2,}(/\S*)?$", re.IGNORECASE)


def _query(intent) -> str:
    return str(intent.args.get(intent.action.capture_field, "")).strip()


def _looks_like_site(target: str) -> bool:
    return bool(URL_RE.match(target.replace(" ", "")))


def _search(ctx, intent):
    if ctx.providers is None:
        return fail(ctx, "error.provider")
    query = _query(intent)
    if not query:
        return fail(ctx, "skill.search.empty_query")
    try:
        results = ctx.providers.search_web(query, limit=5)
    except Exception as exc:  # noqa: BLE001 - сеть бывает недоступна, это не падение
        ctx.log.warning("поиск не сработал: %s", exc, exc_info=True)
        reason = str(exc) or "нет связи"
        # Раньше здесь было просто «Сервис не ответил» — по такому тексту нельзя
        # понять, что чинить. Теперь видно причину, а в журнале есть подробности.
        return fail(ctx, "skill.search.failed", reason=reason[:200])
    if not results:
        return fail(ctx, "skill.search.nothing", query=query)
    snippets = "\n".join(f"- {item['title']}: {item['body'][:200]}" for item in results[:5])
    answer = None
    if ctx.providers.llm_available():
        answer = ctx.providers.ask_model(
            f"Вопрос: {query}\n\nВот что нашёл поиск:\n{snippets}\n\n"
            "Ответь коротко по-русски, 2-3 предложения, без ссылок."
        )
    if answer:
        return str(answer).strip()
    return ctx.t("skill.search.short", query=query, text=results[0]["body"][:300])


def _open_site(ctx, intent):
    target = _query(intent).replace(" ", "")
    if not target:
        return fail(ctx, "skill.search.empty_query")
    if not _looks_like_site(target):
        target = "https://duckduckgo.com/?q=" + target
    if not target.startswith("http"):
        target = "https://" + target
    from jarvis.platform import get_platform

    try:
        opened = get_platform().open_url(target)
    except Exception as exc:  # noqa: BLE001 - браузера может не быть вовсе
        ctx.log.warning("браузер не открылся: %s", exc, exc_info=True)
        return fail(ctx, "skill.search.open_failed", reason=str(exc)[:200])
    if not opened:
        return fail(ctx, "skill.search.open_failed", reason="браузер не отозвался")
    return ctx.t("skill.search.opened", url=target)


HANDLERS = {"search": _search, "open_site": _open_site}
