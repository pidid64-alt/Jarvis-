"""Навык «Снимок экрана»: файл кладётся в папку состояния, путь сообщается голосом."""

from __future__ import annotations

import shutil
import time

from jarvis.skills import fail

LINUX_TOOLS = (
    ("gnome-screenshot", "gnome-screenshot -f {path}"),
    ("spectacle", "spectacle -b -n -o {path}"),
    ("scrot", "scrot {path}"),
    ("import", "import -window root {path}"),
    ("grim", "grim {path}"),
)


def _target(ctx, name: str):
    folder = ctx.state_dir / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def handle(ctx, intent):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if ctx.platform_name() == "windows":
        path = _target(ctx, f"screen-{stamp}.png")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
            "$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
            "$i = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
            "$g = [System.Drawing.Graphics]::FromImage($i); "
            "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
            f"$i.Save('{path}'); $g.Dispose(); $i.Dispose()"
        )
        ctx.run(f'powershell -NoProfile -Command "{script}"', timeout=30)
        if path.exists():
            return ctx.t("skill.screenshot.saved", path=str(path))
        return fail(ctx, "skill.screenshot.failed")

    for program, template in LINUX_TOOLS:
        if shutil.which(program):
            path = _target(ctx, f"screen-{stamp}.png")
            ctx.run(template.format(path=path), timeout=30)
            if path.exists():
                return ctx.t("skill.screenshot.saved", path=str(path))
            return fail(ctx, "skill.screenshot.failed")
    return fail(ctx, "skill.screenshot.no_tool")


HANDLERS = {"full": handle}
