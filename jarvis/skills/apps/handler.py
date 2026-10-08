"""Навык «Программы»: открывает то, что установлено, и честно говорит, если нет."""

from __future__ import annotations

import shutil
from pathlib import Path

from jarvis.skills import fail

LINUX_APPS = {
    "browser": ("xdg-open https://duckduckgo.com", ("firefox", "chromium", "google-chrome", "brave")),
    "terminal": ("", ("xfce4-terminal", "gnome-terminal", "konsole", "alacritty", "kitty", "xterm")),
    "files": ("", ("thunar", "nautilus", "dolphin", "pcmanfm", "nemo")),
    "home": ("xdg-open {home}", ()),
}
WINDOWS_APPS = {
    "browser": "start https://duckduckgo.com",
    "terminal": "start wt || start cmd",
    "files": "start explorer",
    "home": "start explorer %USERPROFILE%",
}


def handle(ctx, intent):
    action_id = intent.action.id
    if ctx.platform_name() == "windows":
        if ctx.run(WINDOWS_APPS[action_id], timeout=15, background=True, allow_failure=True) is None:
            return ctx.t("reply.done")
        return ctx.t("reply.done")

    command, candidates = LINUX_APPS[action_id]
    if candidates:
        picked = next((program for program in candidates if shutil.which(program)), None)
        if picked is None:
            return fail(ctx, "skill.apps.not_found", app=ctx.t(f"skill.apps.{action_id}"))
        command = f"{picked} >/dev/null 2>&1 &" if action_id != "browser" else f"{picked}"
    command = ctx.substitute(command).replace("{home}", str(Path.home()))
    ctx.run(command, timeout=15, background=True, allow_failure=True)
    return ctx.t("skill.apps.opened", app=ctx.t(f"skill.apps.{action_id}"))


HANDLERS = {key: handle for key in LINUX_APPS}
