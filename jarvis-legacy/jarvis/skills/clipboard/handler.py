"""Навык «Буфер обмена»: чтение и очистка, без вывода лишнего в журнал."""

from __future__ import annotations

import shutil

from jarvis.skills import fail

MAX_CHARS = 500


def _linux_command(action_id: str) -> str | None:
    if shutil.which("wl-paste"):
        return "wl-paste --no-newline" if action_id == "read" else "wl-copy --clear"
    if shutil.which("xclip"):
        return ("xclip -selection clipboard -o" if action_id == "read"
                else "xclip -selection clipboard /dev/null")
    return None


def handle(ctx, intent):
    action_id = intent.action.id
    if ctx.platform_name() == "windows":
        command = ("powershell -NoProfile -Command \"Get-Clipboard -Raw\"" if action_id == "read"
                   else "powershell -NoProfile -Command \"Set-Clipboard -Value $null\"")
        output = ctx.run(command, timeout=10)
        if action_id == "clear":
            return ctx.t("skill.clipboard.cleared")
        text = output.strip()
        if not text:
            return fail(ctx, "skill.clipboard.empty")
        return ctx.t("skill.clipboard.content", text=text[:MAX_CHARS],
                     truncated=" …" if len(text) > MAX_CHARS else "")

    command = _linux_command(action_id)
    if command is None:
        return fail(ctx, "skill.clipboard.no_tool")
    output = ctx.run(command, timeout=10)
    if action_id == "clear":
        return ctx.t("skill.clipboard.cleared")
    text = output.strip()
    if not text:
        return fail(ctx, "skill.clipboard.empty")
    return ctx.t("skill.clipboard.content", text=text[:MAX_CHARS],
                 truncated=" …" if len(text) > MAX_CHARS else "")


HANDLERS = {"read": handle, "clear": handle}
