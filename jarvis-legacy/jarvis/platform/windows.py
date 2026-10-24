"""Windows-реализация платформенного слоя."""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .base import CommandResult, decode_output, no_window_kwargs

log = logging.getLogger("jarvis.platform.windows")


class WindowsPlatform:
    name = "windows"

    # ------------------------------------------------------------------ shell
    def run(self, command: str, *, timeout: float = 15.0, cwd: Path | None = None) -> CommandResult:
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=timeout,
                cwd=str(cwd) if cwd else None,
                **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired:
            log.warning("команда не уложилась в %.0f с: %s", timeout, command[:120])
            return CommandResult(returncode=124, output="")
        return CommandResult(
            returncode=result.returncode,
            output=decode_output(result.stdout or result.stderr).strip(),
            raw_stdout=result.stdout or b"",
            raw_stderr=result.stderr or b"",
        )

    def spawn(self, command: str, *, cwd: Path | None = None) -> subprocess.Popen:
        return subprocess.Popen(
            command,
            shell=True,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )

    # ------------------------------------------------------------------- ui
    def notify(self, title: str, body: str, urgency: str = "normal") -> bool:
        """Всплывающее уведомление Windows через PowerShell (без сторонних пакетов)."""
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
            "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$texts = $template.GetElementsByTagName('text');"
            f"$texts.Item(0).AppendChild($template.CreateTextNode('{self._escape(title)}')) > $null;"
            f"$texts.Item(1).AppendChild($template.CreateTextNode('{self._escape(body)}')) > $null;"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($template);"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Jarvis').Show($toast);"
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           timeout=15, check=False, **no_window_kwargs())
            return True
        except (OSError, subprocess.TimeoutExpired):
            log.info("уведомление не показано: %s — %s", title, body)
            return False

    @staticmethod
    def _escape(text: str) -> str:
        return text.replace("'", "''")

    def play_wav(self, path: Path) -> bool:
        try:
            import winsound  # ленивый импорт: только на Windows

            winsound.PlaySound(str(path), winsound.SND_FILENAME)
            return True
        except Exception as exc:  # noqa: BLE001 - звук не должен ронять ассистента
            log.warning("не смог проиграть звук: %s", exc)
            return False

    def open_url(self, url: str) -> bool:
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        except OSError:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", url], **no_window_kwargs())
                return True
            except OSError:
                return False

    def beep(self) -> None:
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:  # noqa: BLE001
            print("\a", end="", flush=True)

    # ---------------------------------------------------------------- audio
    @contextmanager
    def mic_stream(self, sample_rate: int) -> Iterator[object]:
        """Поток микрофона через sounddevice (PortAudio)."""
        try:
            import sounddevice as sd
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("для записи звука на Windows нужен пакет sounddevice") from exc

        stream = sd.RawInputStream(samplerate=sample_rate, channels=1, dtype="int16", blocksize=0)
        stream.start()
        try:
            yield stream
        finally:
            stream.stop()
            stream.close()

    # --------------------------------------------------------------- hotkey
    def setup_hotkey(self, spec: str, command: str) -> tuple[bool, str]:
        """На Windows горячую клавишу держит сам Jarvis (RegisterHotKey)."""
        return True, f"Jarvis сам слушает {spec}"

    def hotkey_hint(self, spec: str, command: str) -> str:
        return f"Горячая клавиша {spec} назначается самим Jarvis"

    def tray_supported(self) -> bool:
        return True


# ---------------------------------------------------------------------- hotkey


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312


def parse_hotkey(spec: str) -> tuple[int, int]:
    """«Win+J» → (модификаторы, виртуальный код клавиши)."""
    parts = [part.strip().lower() for part in spec.replace("+", " ").split()]
    if not parts:
        raise ValueError("пустая горячая клавиша")
    modifiers = 0
    key_name = parts[-1]
    for part in parts[:-1]:
        if part in {"win", "super", "meta", "cmd"}:
            modifiers |= MOD_WIN
        elif part in {"ctrl", "control"}:
            modifiers |= MOD_CONTROL
        elif part == "alt":
            modifiers |= MOD_ALT
        elif part == "shift":
            modifiers |= MOD_SHIFT
    if len(key_name) == 1 and key_name.isalpha():
        vk = ord(key_name.upper())
    elif key_name.startswith("f") and key_name[1:].isdigit():
        vk = 0x70 + int(key_name[1:]) - 1
    else:
        raise ValueError(f"не понимаю клавишу {key_name!r}")
    return modifiers | MOD_NOREPEAT, vk


class WindowsHotkeyListener:
    """Слушает одну горячую клавишу Win32 в отдельном потоке."""

    def __init__(self, spec: str, callback):
        self.spec = spec
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._registered = False

    def start(self) -> bool:
        if os.name != "nt":
            return False
        self._thread = threading.Thread(target=self._loop, name="jarvis-hotkey", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        user32 = ctypes.windll.user32
        try:
            modifiers, vk = parse_hotkey(self.spec)
        except ValueError as exc:
            log.error("горячая клавиша не разобрана: %s", exc)
            return
        if not user32.RegisterHotKey(None, 1, modifiers, vk):
            log.error("не удалось занять горячую клавишу %s (занята другой программой?)", self.spec)
            return
        self._registered = True
        log.info("слушаю горячую клавишу %s", self.spec)
        from ctypes import wintypes

        message = wintypes.MSG()
        try:
            while not self._stop.is_set():
                if user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    if message.message == WM_HOTKEY:
                        try:
                            self.callback()
                        except Exception:  # noqa: BLE001
                            log.exception("обработчик горячей клавиши упал")
                else:
                    self._stop.wait(0.05)
        finally:
            user32.UnregisterHotKey(None, 1)
