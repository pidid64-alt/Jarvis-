"""Управление внешними серверами (распознавание речи, синтез).

Слабая машина не должна держать их в памяти постоянно, поэтому по умолчанию
сервер поднимается по требованию и выключается после простоя.
"""

from __future__ import annotations

import logging
import shlex
import socket
import subprocess
import time
from pathlib import Path

from ..core import paths

log = logging.getLogger("jarvis.providers.process")


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class ManagedProcess:
    """Процесс сервера: запуск по требованию, ожидание порта, остановка по простою."""

    def __init__(self, name: str, command: str, *, port: int | None = None,
                 host: str = "127.0.0.1", startup_timeout: float = 30.0,
                 idle_timeout: float = 300.0, log_file: Path | None = None,
                 mode: str = "on_demand"):
        self.name = name
        self.command = command
        self.port = port
        self.host = host
        self.startup_timeout = startup_timeout
        self.idle_timeout = idle_timeout
        self.log_file = log_file or (paths.state_dir() / f"{name}.log")
        self.mode = mode if mode in {"on_demand", "always", "off"} else "on_demand"
        self._process: subprocess.Popen | None = None
        self._last_use = 0.0

    # ----------------------------------------------------------------- control
    def running(self) -> bool:
        if self.port is not None and port_open(self.host, self.port):
            return True
        return self._process is not None and self._process.poll() is None

    def mark_used(self) -> None:
        self._last_use = time.monotonic()

    def ensure(self) -> bool:
        """Поднимает сервер, если он ещё не запущен. True — сервер готов."""
        if self.mode == "off":
            return False
        if self.running():
            self.mark_used()
            return True
        if not self.command:
            return False
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = self.log_file.open("ab")
        except OSError:
            handle = subprocess.DEVNULL  # type: ignore[assignment]
        from ..platform import get_platform

        platform = get_platform()
        log.info("запускаю %s: %s", self.name, self.command)
        try:
            self._process = platform.spawn(self.command, cwd=paths.PROJECT_ROOT)
        except OSError as exc:
            log.error("не удалось запустить %s: %s", self.name, exc)
            return False
        self.mark_used()

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.port is not None and port_open(self.host, self.port):
                log.info("%s готов (порт %s)", self.name, self.port)
                return True
            if self._process is not None and self._process.poll() is not None:
                log.error("%s завершился сразу (код %s), смотри %s",
                          self.name, self._process.returncode, self.log_file)
                return False
            time.sleep(0.3)
        log.error("%s не поднялся за %.0f с", self.name, self.startup_timeout)
        return False

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def stop_if_idle(self) -> bool:
        """Выключает сервер после простоя (экономия памяти)."""
        if self.mode != "on_demand" or self._process is None:
            return False
        if self._process.poll() is not None:
            self._process = None
            return False
        if self._last_use and time.monotonic() - self._last_use > self.idle_timeout:
            log.info("останавливаю %s после %.0f с простоя", self.name, self.idle_timeout)
            self.stop()
            return True
        return False

    def state(self) -> dict[str, object]:
        return {
            "name": self.name,
            "mode": self.mode,
            "running": self.running(),
            "port": self.port,
            "command": self.command,
        }


def split_command(command: str) -> list[str]:
    """Разбирает команду из конфига (нужно для тестов и doctor)."""
    return shlex.split(command, posix=True)
