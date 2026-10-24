"""Навык «Беседа»: короткие ответы без обращения к сети и системе."""

from __future__ import annotations


def _capabilities(ctx, intent):
    registry = ctx.registry
    if registry is None:
        return ctx.t("skill.dialogue.capabilities_empty")
    names = [skill.name for skill in registry.enabled()]
    if not names:
        return ctx.t("skill.dialogue.capabilities_empty")
    shown = ", ".join(names[:12])
    return ctx.t("skill.dialogue.capabilities", count=len(names), list=shown)


HANDLERS = {
    "hello": lambda ctx, intent: ctx.t("skill.dialogue.hello"),
    "how_are_you": lambda ctx, intent: ctx.t("skill.dialogue.how_are_you"),
    "thanks": lambda ctx, intent: ctx.t("skill.dialogue.thanks"),
    "bye": lambda ctx, intent: ctx.t("skill.dialogue.bye"),
    "capabilities": _capabilities,
}
