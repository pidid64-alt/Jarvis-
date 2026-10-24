"""Общий контракт платформенного слоя.

Всё, что зависит от операционной системы (команды, звук, уведомления,
микрофон, хоткей, трей), живёт здесь. Ядро вызывает эти методы и о
различиях Linux/Windows ничего не знает.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Protocol


@dataclass
class CommandResult:
    returncode: int
    output: str
    raw_stdout: bytes = b""
    raw_stderr: bytes = b""


class Platform(Protocol):
    """Интерфейс, который реализуют linux.py и windows.py."""

    name: str  # "linux" | "windows"

    def run(self, command: str, *, timeout: float = 15.0, cwd: Path | None = None) -> CommandResult:
        ...

    def spawn(self, command: str, *, cwd: Path | None = None) -> subprocess.Popen:
        ...

    def notify(self, title: str, body: str, urgency: str = "normal") -> bool:
        ...

    def play_wav(self, path: Path) -> bool:
        ...

    def open_url(self, url: str) -> bool:
        ...

    def beep(self) -> None:
        ...

    @contextmanager
    def mic_stream(self, sample_rate: int) -> Iterator[object]:
        ...

    def setup_hotkey(self, spec: str, command: str) -> tuple[bool, str]:
        ...

    def hotkey_hint(self, spec: str, command: str) -> str:
        ...

    def tray_supported(self) -> bool:
        ...


def decode_output(raw: bytes) -> str:
    """Вывод дочерних процессов на Windows бывает в cp866/cp1251."""
    if not raw:
        return ""
    for encoding in ("utf-8", "cp866", "cp1251", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def no_window_kwargs() -> dict:
    """Windows: не показывать мерцающие консольные окна."""
    if os.name != "nt":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {"creationflags": flags, "startupinfo": startup}


def not_implemented(feature: str) -> Callable[[], None]:
    def _raise() -> None:
        raise NotImplementedError(feature)

    return _raise
