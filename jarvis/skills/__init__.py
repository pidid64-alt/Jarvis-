"""Навыки Jarvis.

Каждый навык — папка с ``skill.json`` (описание, фразы, права) и, если нужна
логика, ``handler.py`` с словарём ``HANDLERS``:

    HANDLERS = {"volume_up": lambda ctx, intent: ...}

Обработчик получает ``ctx`` (доступ к провайдерам, правам, журналу) и
``intent`` (выбранное действие, аргументы, исходный текст) и возвращает
строку, словарь или ``jarvis.core.types.Reply``.
"""

from __future__ import annotations

import shutil
from typing import Iterable

from ..core.matcher import normalize_text

__all__ = ["fail", "has_program", "quote", "strip_prefix"]


def strip_prefix(text: str, phrases: Iterable[str]) -> str:
    """Убирает из фразы начало, совпавшее с одним из триггеров («найди …»)."""
    normalized = normalize_text(text)
    for phrase in sorted(phrases, key=len, reverse=True):
        trigger = normalize_text(phrase)
        if normalized == trigger:
            return ""
        if normalized.startswith(trigger + " "):
            return normalized[len(trigger):].strip()
    return normalized


def fail(ctx, key: str, **values) -> "object":
    """Ответ «не получилось» с понятным текстом.

    Строка, возвращённая навыком, означает успех; когда выполнить действие
    нельзя (нет программы, пустой буфер, нет сети), навык возвращает этот
    ответ — тогда ядро и GUI показывают ошибку, а не «готово».
    """
    from ..core.types import Reply

    return Reply(text=ctx.t(key, **values), ok=False)


def has_program(*names: str) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"
