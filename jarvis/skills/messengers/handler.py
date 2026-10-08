"""Навык «Сообщения»: отправить в Telegram/почту/WhatsApp и проверить входящие.

Правила взяты у скилла OpenClaw (messaging): получатель и текст обязательны,
перед отправкой Jarvis всегда спрашивает подтверждение и показывает, кому и что
именно уйдёт, а при неясности — уточняет, а не угадывает.

Получатель ищется в адресной книге ``contacts.toml``; телефоны, адреса почты и
chat_id принимаются как есть. Секреты (токен бота, пароль приложения) навык
только читает из настроек, в ответы и журнал они не попадают.
"""

from __future__ import annotations

import re

from jarvis.core.messengers import MessengerService
from jarvis.core.errors import JarvisError
from jarvis.core.messengers import names_match
from jarvis.skills import fail

#: по каким словам узнаём канал, если его не назвала модель
CHANNEL_WORDS = {
    "telegram": ("телеграм", "телеграмм", "телеге", "тг", "telegram", "телеграмы"),
    "mail": ("почт", "письм", "мейл", "email", "e-mail", "мейлом", "gmail"),
    "whatsapp": ("ватсап", "вотсап", "ватцап", "whatsapp", "вотсапе", "ватсапе"),
}

#: служебные слова, которые не являются ни получателем, ни текстом
FILLER = {"напиши", "отправь", "скажи", "сообщение", "сообщением", "письмо", "письмом", "ему",
          "ей", "им", "мне", "в", "на", "по", "и", "что", "чтобы", "пожалуйста", "сейчас"}

PHONE_RE = re.compile(r"\+?[\d][\d\s()\-]{4,}\d")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


# --------------------------------------------------------------- вспомогательное
def _service(ctx) -> MessengerService:
    """Общий сервис из ядра, а если навык запущен отдельно — свой."""
    service = (ctx.extra or {}).get("messengers")
    if service is None:
        service = MessengerService(ctx.config, ctx.journal)
        ctx.extra["messengers"] = service
    return service


def _strip_lead(text: str) -> str:
    """Убирает служебные слова в начале: «на», «в», «к», «отправь»…"""
    words = (text or "").split()
    while words and words[0].casefold().strip(".,:;!?—") in FILLER:
        words.pop(0)
    return " ".join(words).strip()


def _channel_from_text(text: str) -> str:
    lowered = (text or "").casefold().replace("ё", "е")
    for channel, words in CHANNEL_WORDS.items():
        if any(word in lowered for word in words):
            return channel
    if EMAIL_RE.search(text or ""):
        return "mail"
    return ""


def _window_matches(window: list[str], contact_name: str) -> bool:
    """Совпадает ли окно слов со именем из книги (без угадывания по длине)."""
    parts = [part for part in contact_name.split() if part]
    if len(window) == len(parts):
        return all(names_match(left, right) for left, right in zip(window, parts))
    if len(window) == 1 and len(parts) > 1:
        # «Люба» для «Люба Петрова» — так тоже зовут
        return any(names_match(window[0], part) for part in parts)
    return False


def _find_recipient(service: MessengerService, tail: str, channel: str):
    """Ищет получателя в хвосте фразы.

    Возвращает ``(recipient, message, вопрос)``: либо получатель и остаток текста,
    либо вопрос пользователю — ничего не угадываем.
    """
    tail = (tail or "").strip()
    if not tail:
        return "", "", "Кому и что отправить? Например: «отправь в телеграм Артёму: привет»"

    # «получатель: текст» — самый понятный случай
    if ":" in tail:
        head, _, body = tail.partition(":")
        head, body = head.strip(), body.strip()
        if head and body:
            # «на vasya@example.com: готово» — берём адрес, служебные слова не нужны
            found = EMAIL_RE.search(head) or PHONE_RE.search(head)
            if found:
                return found.group(0).strip(), body, ""
            head = _strip_lead(head)
            if head:
                return head, body, ""
        if not head:
            return "", "", "Кому отправить? Назовите имя из адресной книги или адрес"

    contacts = service.address_book.load()
    # сначала явные адреса: телефон, почта
    for pattern in (EMAIL_RE, PHONE_RE):
        match = pattern.search(tail)
        if match:
            value = match.group(0).strip()
            message = (tail[: match.start()] + " " + tail[match.end():]).strip(" ,;:—-")
            return value, message, ""
    # затем имена из адресной книги: ищем самое длинное совпадение по словам.
    # окно из нескольких слов сравниваем со именем той же длины, иначе «Артём
    # привет как» целиком считалось бы именем «Артём»
    words = tail.split()
    best: tuple[int, int, str] = (-1, -1, "")
    for size in (3, 2, 1):
        for start in range(0, max(0, len(words) - size + 1)):
            window = words[start:start + size]
            for contact in contacts:
                if _window_matches(window, contact.name) and size > best[1] - best[0]:
                    best = (start, start + size, contact.name)
    if best[0] >= 0:
        start, end, _name = best
        message = " ".join(words[:start] + words[end:]).strip(" ,;:—-")
        return " ".join(words[start:end]), message, ""
    if channel == "telegram" and words and words[0].lstrip("-").isdigit():
        return words[0], " ".join(words[1:]).strip(" ,;:—-"), ""
    if len(words) == 1 and words[0].casefold() in FILLER:
        return "", "", "Кому и что отправить? Например: «отправь в телеграм Артёму: привет»"
    known = ", ".join(contact.name for contact in contacts) or "пока пусто"
    return "", "", (f"Не понял, кому отправлять. В адресной книге: {known}. "
                    f"Скажите: «отправь в телеграм Артёму: привет» или назовите "
                    f"номер/адрес")


def _send(ctx, intent):
    service = _service(ctx)
    if not service.enabled:
        return fail(ctx, "skill.messengers.off")

    model_args = dict(intent.args.get("model_arguments") or {})
    tail = str(intent.args.get(intent.action.capture_field, "") or "")
    channel_name = str(model_args.get("channel", "") or "")
    channel = service.channels.get(channel_name) if channel_name else None
    if channel is None:
        channel = service.channels.get(_channel_from_text(intent.text or ""))
    if channel is None:
        channel = service.channels.get(_channel_from_text(tail))
    if channel is None:
        prepared = ", ".join(item.title for item in service.channels.all())
        return fail(ctx, "skill.messengers.no_channel", channels=prepared)

    recipient = str(model_args.get("recipient", "") or "")
    message = str(model_args.get("text", "") or "")
    if not (recipient and message) or not intent.args.get("model_arguments"):
        found, rest, question = _find_recipient(service, tail or f"{recipient} {message}", channel.name)
        if question:
            reply = fail(ctx, "skill.messengers.need_recipient")
            reply.text = question
            reply.continue_dialog = True
            return reply
        recipient = recipient or found
        message = message or rest
    if not (recipient and message):
        reply = fail(ctx, "skill.messengers.need_recipient")
        reply.continue_dialog = True
        return reply

    target, display, problem = service.resolve(channel.name, recipient)
    if problem:
        reply = fail(ctx, "skill.messengers.bad_recipient")
        reply.text = problem
        reply.continue_dialog = True
        return reply

    question = ctx.t("skill.messengers.confirm", channel=channel.title, to=display, text=message)
    if not ctx.confirm(question):
        ctx.log.info("отправка отменена пользователем")
        return ctx.t("skill.messengers.cancelled")
    try:
        result = service.send(channel.name, recipient, message)
    except JarvisError as exc:
        ctx.log.warning("сообщение не ушло: %s", exc)
        return fail(ctx, "skill.messengers.failed", reason=str(exc)[:200])
    ctx.log.info("отправлено в %s → %s", channel.title, result.display)
    return ctx.t("skill.messengers.sent", channel=channel.title, to=result.display,
                 text=message)


def _inbox(ctx, intent):
    service = _service(ctx)
    if not service.enabled:
        return fail(ctx, "skill.messengers.off")
    try:
        found = service.check_new(limit=service.mail_limit)
    except Exception as exc:  # noqa: BLE001 - канал не должен ронять навык
        ctx.log.warning("проверка входящих не удалась: %s", exc)
        return fail(ctx, "skill.messengers.failed", reason=str(exc)[:200])
    if not found:
        return ctx.t("skill.messengers.empty")
    lines = [f"• {message.describe()}" for message in found[:10]]
    return ctx.t("skill.messengers.new", count=len(found), lines="\n".join(lines))


def _contacts(ctx, intent):
    service = _service(ctx)
    contacts = service.address_book.load()
    if not contacts:
        return ctx.t("skill.messengers.no_contacts",
                     path=str(service.address_book.path))
    lines = []
    for contact in contacts:
        channels = ", ".join(name for name in ("telegram", "mail", "whatsapp")
                             if contact.target(name))
        lines.append(f"• {contact.name} — {channels or 'нет адресов'}")
    return ctx.t("skill.messengers.contacts", path=str(service.address_book.path),
                 lines="\n".join(lines))


def _channels(ctx, intent):
    service = _service(ctx)
    state = service.state(probe=True)
    lines = []
    for name, channel in state["channels"].items():
        mark = "готов" if channel.get("available") else "не готов"
        lines.append(f"• {name}: {mark} — {channel.get('reason', '')}")
    text = ctx.t("skill.messengers.channels", lines="\n".join(lines),
                 path=str(service.address_book.path),
                 enabled="включены" if state["enabled"] else "выключены")
    if not state["enabled"]:
        text += " " + ctx.t("skill.messengers.enable_hint")
    return text


def _connect(ctx, intent):
    service = _service(ctx)
    whatsapp = service.channels.get("whatsapp")
    return ctx.t("skill.messengers.connect", command=whatsapp.qr_hint(),
                 risk=ctx.t("skill.messengers.risk"))


HANDLERS = {
    "send": _send,
    "inbox": _inbox,
    "contacts": _contacts,
    "channels": _channels,
    "connect": _connect,
}
