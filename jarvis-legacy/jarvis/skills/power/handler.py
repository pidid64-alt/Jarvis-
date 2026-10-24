"""Навык «Питание и сессия»: команды подобраны под окружение, опасные — с подтверждением."""

from __future__ import annotations

import shutil

LINUX = {
    "lock": ("loginctl lock-session || xdg-screensaver lock", True),
    "suspend": ("systemctl suspend", True),
    "logout": ("xfce4-session-logout --logout || gnome-session-quit --logout --no-prompt || loginctl terminate-user $USER", True),
    "reboot": ("systemctl reboot", True),
    "poweroff": ("systemctl poweroff", True),
}
WINDOWS = {
    "lock": ("rundll32.exe user32.dll,LockWorkStation", False),
    "suspend": ("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", False),
    "logout": ("shutdown /l /f", False),
    "reboot": ("shutdown /r /f /t 5", False),
    "poweroff": ("shutdown /s /f /t 5", False),
}


def handle(ctx, intent):
    action_id = intent.action.id
    command, use_shell = (WINDOWS if ctx.platform_name() == "windows" else LINUX)[action_id]
    if (ctx.platform_name() != "windows" and not shutil.which("systemctl")
            and action_id in {"suspend", "reboot", "poweroff"}):
        # без systemd остаются только loginctl-подобные пути
        command = {"suspend": "loginctl suspend", "reboot": "loginctl reboot",
                   "poweroff": "loginctl poweroff"}[action_id]
    ctx.run(command, timeout=15, background=use_shell, allow_failure=True)
    return ctx.t(f"skill.power.{action_id}")


HANDLERS = {key: handle for key in LINUX}
