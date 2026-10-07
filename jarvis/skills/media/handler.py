"""Навык «Медиаплеер». Linux — playerctl, Windows — системные клавиши мультимедиа."""

from __future__ import annotations

from jarvis.skills import fail, has_program

_LINUX_KEYS = {"play_pause": "play-pause", "next": "next", "prev": "previous", "stop": "stop"}
_WIN_KEYS = {"play_pause": 179, "next": 176, "prev": 177, "stop": 178}


def handle(ctx, intent):
    action_id = intent.action.id
    if ctx.platform_name() == "windows":
        code = _WIN_KEYS[action_id]
        return ctx.run(
            "powershell -NoProfile -Command \"$w = New-Object -ComObject WScript.Shell; "
            f"$w.SendKeys([char]{code})\"",
            timeout=10,
        ) or ctx.t("reply.done")

    if has_program("playerctl"):
        output = ctx.run(f"playerctl {_LINUX_KEYS[action_id]}", timeout=10, allow_failure=True)
        return output.strip() or ctx.t("reply.done")

    # Запасной путь: клавиши XF86 через xdotool, если playerctl не установлен
    key = {"play_pause": "XF86AudioPlay", "next": "XF86AudioNext",
           "prev": "XF86AudioPrev", "stop": "XF86AudioStop"}[action_id]
    if has_program("xdotool"):
        ctx.run(f"xdotool key {key}", timeout=10)
        return ctx.t("reply.done")
    return fail(ctx, "error.provider")


HANDLERS = {key: handle for key in _LINUX_KEYS}
