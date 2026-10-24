"""Навык «Состояние системы»: метрики читаются напрямую из ОС.

Никаких `sensors`, `free` и прочих внешних программ — на слабой машине
важно не плодить процессы.
"""

from __future__ import annotations

from jarvis.platform import metrics


def _answer(ctx, key: str, text_key: str):
    result = metrics.collect(key)
    if not result.text:
        return ctx.t("skill.health.unavailable", reason=key)
    return result.text


HANDLERS = {
    "memory": lambda ctx, intent: _answer(ctx, "memory", "skill.health.memory"),
    "disk": lambda ctx, intent: _answer(ctx, "disk", "skill.health.disk"),
    "battery": lambda ctx, intent: _answer(ctx, "battery", "skill.health.battery"),
    "temperature": lambda ctx, intent: _answer(ctx, "cpu_temp", "skill.health.temperature"),
    "internet": lambda ctx, intent: _answer(ctx, "internet", "skill.health.internet"),
    "summary": lambda ctx, intent: " ".join(
        part
        for part in (
            metrics.memory().text,
            metrics.disk_space().text,
            metrics.battery().text,
        )
        if part
    ),
}
