"""Навык «Громкость». Linux — pactl/wpctl, Windows — системные клавиши через PowerShell."""

from __future__ import annotations

from jarvis.skills import fail, has_program


def _linux(action_id: str) -> str | None:
    if has_program("pactl"):
        if action_id == "up":
            return "pactl set-sink-volume @DEFAULT_SINK@ +10%"
        if action_id == "down":
            return "pactl set-sink-volume @DEFAULT_SINK@ -10%"
        if action_id == "mute":
            return "pactl set-sink-mute @DEFAULT_SINK@ 1"
        if action_id == "unmute":
            return "pactl set-sink-mute @DEFAULT_SINK@ 0"
    if has_program("wpctl"):
        if action_id == "up":
            return "wpctl set-volume @DEFAULT_AUDIO_SINK@ 10%+"
        if action_id == "down":
            return "wpctl set-volume @DEFAULT_AUDIO_SINK@ 10%-"
        if action_id == "mute":
            return "wpctl set-mute @DEFAULT_AUDIO_SINK@ 1"
        if action_id == "unmute":
            return "wpctl set-mute @DEFAULT_AUDIO_SINK@ 0"
    return None


def _windows(action_id: str) -> str:
    keys = {"up": 175, "down": 174, "mute": 173, "unmute": 173}
    code = keys[action_id]
    return ("powershell -NoProfile -Command \"$w = New-Object -ComObject WScript.Shell; "
            f"$w.SendKeys([char]{code})\"")


def handle(ctx, intent):
    action_id = intent.action.id
    command = _windows(action_id) if ctx.providers and _is_windows() else _linux(action_id)
    if not command:
        return fail(ctx, "error.provider")
    result = ctx.run(command, timeout=10)
    if result.strip():
        return result.strip()
    return ctx.t("reply.done")


def _is_windows() -> bool:
    from jarvis.platform import platform_name

    return platform_name() == "windows"


HANDLERS = {"up": handle, "down": handle, "mute": handle, "unmute": handle}
