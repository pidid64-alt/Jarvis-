"""Запуск окна Jarvis: локальный интерфейс открывается в браузере.

Почему так, а не библиотека окон: у Jarvis задача — быть лёгким на слабой
машине. Qt/PySide — это сотни мегабайт и системные библиотеки, Tkinter не даёт
сделать современный вид. Поэтому интерфейс — обычные html/css/js, которые
раздаёт сам Jarvis по адресу 127.0.0.1, а окном служит браузер в режиме
приложения (`--app`): без вкладок, без адресной строки, со своей иконкой.
Ни одной новой зависимости при этом не появляется.

Если браузера с режимом `--app` нет, открываем обычную вкладку: интерфейс
работает одинаково.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from ..core.logging_setup import get_logger

log = get_logger("interfaces.webapp")

#: секунды ожидания, пока ядро поднимется и напишет файл обнаружения
START_TIMEOUT = 20.0

WINDOWS_BROWSERS = (
    ("edge", ("ProgramFiles(x86)", "ProgramFiles"), r"Microsoft\Edge\Application\msedge.exe"),
    ("chrome", ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"), r"Google\Chrome\Application\chrome.exe"),
    ("brave", ("ProgramFiles", "ProgramFiles(x86)"), r"BraveSoftware\Brave-Browser\Application\brave.exe"),
    ("vivaldi", ("LocalAppData",), r"Vivaldi\Application\vivaldi.exe"),
    ("opera", ("LocalAppData", "ProgramFiles"), r"Program\opera.exe"),
)

LINUX_BROWSERS = (
    ("chromium", ("chromium", "chromium-browser")),
    ("chrome", ("google-chrome", "google-chrome-stable")),
    ("brave", ("brave-browser", "brave")),
    ("vivaldi", ("vivaldi", "vivaldi-stable")),
    ("edge", ("microsoft-edge", "microsoft-edge-stable")),
)

#: браузеры, которые понимают режим приложения (без вкладок и адресной строки)
APP_MODE = {"edge", "chrome", "chromium", "brave", "vivaldi", "opera"}


def find_browsers() -> list[tuple[str, str]]:
    """Найти установленные браузеры: [(имя, путь к программе)]."""
    found: list[tuple[str, str]] = []
    if os.name == "nt":
        for name, roots, tail in WINDOWS_BROWSERS:
            for root in roots:
                base = os.environ.get(root)
                if not base:
                    continue
                candidate = Path(base) / tail
                if candidate.is_file():
                    found.append((name, str(candidate)))
                    break
    else:
        for name, commands in LINUX_BROWSERS:
            for command in commands:
                path = shutil.which(command)
                if path:
                    found.append((name, path))
                    break
    return found


def pick_browser(preferred: str = "") -> tuple[str, str] | None:
    """Выбрать браузер для окна: сначала тот, что умеет режим приложения."""
    browsers = find_browsers()
    if preferred:
        for name, path in browsers:
            if name == preferred.lower():
                return name, path
    for name, path in browsers:
        if name in APP_MODE:
            return name, path
    return browsers[0] if browsers else None


def open_window(url: str, *, preferred: str = "", app_mode: bool = True,
                dry_run: bool = False) -> str:
    """Открыть интерфейс Jarvis; возвращает описание того, что сделано.

    ``dry_run`` — только сказать, что было бы запущено (для тестов и для
    подсказок в терминале).
    """
    choice = pick_browser(preferred)
    if choice is None:
        if dry_run:
            return "браузер не найден"
        webbrowser.open(url)
        return "открыто в браузере по умолчанию"
    name, path = choice
    if app_mode and name in APP_MODE:
        command = [path, f"--app={url}", "--new-window", "--disable-features=Translate"]
        description = f"окно приложения {name}"
    else:
        command = [path, url]
        description = f"вкладка {name}"
    if dry_run:
        return " ".join(command)
    try:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)
        return description
    except OSError as exc:
        log.warning("браузер %s не запустился: %s", name, exc)
        webbrowser.open(url)
        return "открыто в браузере по умолчанию"


# ------------------------------------------------------------------- демон


def daemon_log_path() -> Path:
    from ..core import paths

    return paths.state_dir() / "daemon.log"


def start_daemon(*, debug: bool = False, voice: bool = False) -> bool:
    """Поднять демон Jarvis отдельным процессом (окно открывается к нему).

    Демон живёт сам по себе: окно можно закрыть и открыть заново, ядро и
    горячая клавиша при этом продолжают работать.
    """
    from ..core import paths

    paths.ensure_dirs()
    command = [sys.executable, "-m", "jarvis", "run", "--no-autonomy"]
    if not voice:
        command.append("--no-voice")
    if debug:
        command.append("--debug")
    log_path = daemon_log_path()
    try:
        with open(log_path, "ab") as stream:
            stream.write(f"\n--- запуск {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
            stream.flush()
            kwargs: dict = {"stdout": stream, "stderr": stream, "stdin": subprocess.DEVNULL, "cwd": str(paths.PROJECT_ROOT)}
            if os.name == "nt":
                kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                                           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(command, **kwargs)
        return True
    except OSError as exc:
        log.error("демон не запустился: %s", exc)
        return False


def wait_for_api(timeout: float = START_TIMEOUT):
    """Дождаться, пока демон поднимет API, и вернуть адрес с токеном."""
    from .client import read_target

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        target = read_target(verify=True)
        if target is not None:
            return f"{target.url}/ui/?token={target.token}"
        time.sleep(0.25)
    return None


def open_ui(*, preferred: str = "", debug: bool = False, start: bool = True,
            app_mode: bool = True, voice: bool = False) -> int:
    """Полный путь: поднять ядро (если нужно) и открыть окно интерфейса."""
    from .client import read_target

    target = read_target(verify=True)
    url = f"{target.url}/ui/?token={target.token}" if target else None
    if url is None:
        if not start:
            print("Jarvis не запущен: сначала выполните «jarvis run», потом «jarvis gui»",
                  file=sys.stderr)
            return 1
        if not start_daemon(debug=debug, voice=voice):
            print("Не удалось запустить ядро Jarvis. Подробности — в журнале.", file=sys.stderr)
            return 1
        url = wait_for_api()
        if url is None:
            print("Ядро запустилось, но не ответило за отведённое время. "
                  f"Смотрите журнал: {daemon_log_path()}", file=sys.stderr)
            return 1
    opened = open_window(url, preferred=preferred, app_mode=app_mode)
    print(f"Jarvis открыт ({opened}). Адрес: {url.split('?')[0]}")
    print("Окно можно закрыть: Jarvis продолжит работать, горячая клавиша снова его откроет.")
    return 0
