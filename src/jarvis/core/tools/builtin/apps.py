"""Управление приложениями Windows: открыть, закрыть, показать запущенные.

Открытие — безопасное действие. Закрытие процесса может потерять несохранённые
данные, поэтому close_app имеет класс outbound: перед убийством процесса
ассистент спросит подтверждение (кнопки в боте / y-n в CLI).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

from jarvis.core.tools.base import Tool, ToolResult

# Человекочитаемые имена → исполняемые файлы Windows.
_APP_ALIASES = {
    "блокнот": "notepad",
    "notepad": "notepad",
    "калькулятор": "calc",
    "calc": "calc",
    "paint": "mspaint",
    "paint.net": "paintdotnet",
    "браузер": "msedge",
    "edge": "msedge",
    "chrome": "chrome",
    "chromium": "chromium",
    "firefox": "firefox",
    "проводник": "explorer",
    "explorer": "explorer",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "терминал": "wt",
    "cmd": "cmd",
    "powershell": "powershell",
    "диспетчер задач": "taskmgr",
    "taskmgr": "taskmgr",
    "telegram": "telegram",
    "discord": "discord",
    "vscode": "code",
    "steam": "steam",
    "spotify": "spotify",
}

# Что показываем в list_apps (иначе tasklist вернёт сотни системных процессов).
_INTERESTING = {
    "explorer", "notepad", "calc", "mspaint", "winword", "excel", "powerpnt",
    "msedge", "chrome", "firefox", "opera", "brave", "vivaldi", "telegram",
    "discord", "code", "idea64", "pycharm64", "steam", "spotify", "obs64",
    "photoshop", "vlc", "winrar", "7zfm", "wt", "cmd", "powershell", "taskmgr",
}


def _normalize(name: str) -> str:
    key = name.strip().lower()
    exe = _APP_ALIASES.get(key, key)
    return exe.removesuffix(".exe") + ".exe"


def _running_processes() -> dict[str, list[str]]:
    """Имя.exe → список PID. Вывод tasklist в OEM-кодировке консоли."""
    output = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"], check=False, capture_output=True, text=False
    ).stdout.decode("cp866", errors="replace")
    processes: dict[str, list[str]] = {}
    for line in output.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].endswith(".exe"):
            processes.setdefault(parts[0].lower(), []).append(parts[1])
    return processes


def _open_sync(name: str, exe: str, target: str) -> None:
    import os

    bare = exe.removesuffix(".exe")
    if target:
        # «открой хром сайт.ру» — приложение с аргументом.
        if shutil.which(bare) or shutil.which(exe):
            subprocess.Popen([bare, target], shell=False)
        else:
            os.startfile(target)  # приложения нет — Windows сама выберет ассоциацию
    elif shutil.which(bare) or shutil.which(exe):
        subprocess.Popen([bare], shell=False)
    else:
        # Системные имена (calc, notepad) могут не быть в PATH — через start.
        subprocess.Popen(f'start /B "" {exe}', shell=True)


def _close_sync(exe: str) -> tuple[bool, str]:
    result = subprocess.run(["taskkill", "/IM", exe], check=False, capture_output=True, text=True)
    detail = (result.stderr or result.stdout or "").strip()
    return result.returncode == 0, detail


def create_tools() -> list[Tool]:
    async def open_app(args: dict) -> ToolResult:
        name = str(args.get("name", "")).strip()
        target = str(args.get("target", "")).strip()
        if not name:
            return ToolResult(output="Ошибка: укажи имя приложения (например, блокнот, chrome).")
        exe = _normalize(name)
        try:
            await asyncio.to_thread(_open_sync, name, exe, target)
        except Exception as e:
            return ToolResult(output=f"Не удалось открыть {name!r}: {e}")
        return ToolResult(output=f"Команда на открытие {name!r} выполнена.")

    async def close_app(args: dict) -> ToolResult:
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolResult(output="Ошибка: укажи имя приложения.")
        exe = _normalize(name)
        processes = await asyncio.to_thread(_running_processes)
        pids = processes.get(exe)
        if not pids:
            return ToolResult(output=f"Приложение {exe} не запущено.")
        # «Мягкое» закрытие (WM_CLOSE), не убийство — данные сохраняются.
        ok, detail = await asyncio.to_thread(_close_sync, exe)
        if ok:
            return ToolResult(output=f"Приложение {exe} закрыто (PID: {', '.join(pids)}).")
        return ToolResult(output=f"Не удалось закрыть {exe}: {detail}")

    async def list_apps(args: dict) -> ToolResult:
        processes = await asyncio.to_thread(_running_processes)
        found = []
        for exe, pids in sorted(processes.items()):
            base = exe.removesuffix(".exe")
            if base in _INTERESTING or base.lower() in _INTERESTING:
                found.append(f"- {base} ({len(pids)} шт.)")
        if not found:
            return ToolResult(output="Из известных приложений ничего не запущено.")
        return ToolResult(output="Запущенные приложения:\n" + "\n".join(found))

    return [
        Tool(
            name="open_app",
            description=(
                "Открыть приложение Windows (блокнот, калькулятор, chrome, edge, word, проводник…) "
                "или файл/ссылку. target — что открыть в нём (необязательно)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Приложение по-русски или exe-имя"},
                    "target": {"type": "string", "description": "Файл или URL (необязательно)"},
                },
                "required": ["name"],
            },
            handler=open_app,
            side_effect="write",
        ),
        Tool(
            name="close_app",
            description=(
                "Закрыть приложение Windows по имени. Требует подтверждения: "
                "несохранённые данные могут пропасть."
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Приложение, например блокнот"}},
                "required": ["name"],
            },
            handler=close_app,
            side_effect="outbound",  # подтверждение кнопками перед закрытием
        ),
        Tool(
            name="list_apps",
            description="Какие известные приложения сейчас запущены.",
            parameters={"type": "object", "properties": {}},
            handler=list_apps,
            side_effect="read",
        ),
    ]
