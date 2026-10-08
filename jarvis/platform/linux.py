"""Linux-реализация платформенного слоя."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .base import CommandResult, decode_output, no_window_kwargs

log = logging.getLogger("jarvis.platform.linux")

#: Пробуем по очереди: PipeWire/PulseAudio, потом ALSA
AUDIO_PLAYERS = ("paplay", "pw-play", "aplay")
AUDIO_RECORDERS = ("parecord", "pw-record", "arecord")


def _which(name: str) -> str | None:
    return shutil.which(name)


def _first_available(candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        path = _which(name)
        if path:
            return path
    return None


class LinuxPlatform:
    name = "linux"

    # ------------------------------------------------------------------ shell
    def run(self, command: str, *, timeout: float = 15.0, cwd: Path | None = None) -> CommandResult:
        try:
            result = subprocess.run(
                command,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                timeout=timeout,
                cwd=str(cwd) if cwd else None,
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
            executable="/bin/bash",
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # ------------------------------------------------------------------- ui
    def notify(self, title: str, body: str, urgency: str = "normal") -> bool:
        binary = _which("notify-send")
        if not binary:
            log.info("уведомление (нет notify-send): %s — %s", title, body)
            return False
        try:
            subprocess.run(
                [binary, "-u", urgency if urgency in {"low", "normal", "critical"} else "normal", title, body],
                timeout=5,
                check=False,
            )
            return True
        except OSError:
            return False

    def play_wav(self, path: Path) -> bool:
        player = _first_available(AUDIO_PLAYERS)
        if not player:
            log.warning("не найден проигрыватель (%s)", ", ".join(AUDIO_PLAYERS))
            return False
        try:
            subprocess.run([player, str(path)], timeout=60, check=False)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    def open_url(self, url: str) -> bool:
        for opener in ("xdg-open", "gio"):
            binary = _which(opener)
            if not binary:
                continue
            try:
                args = [binary, url] if opener == "xdg-open" else [binary, "open", url]
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except OSError:
                continue
        log.warning("не нашёл, чем открыть ссылку")
        return False

    def beep(self) -> None:
        candidates = [
            "/usr/share/sounds/freedesktop/stereo/message.oga",
            "/usr/share/sounds/freedesktop/stereo/bell.oga",
        ]
        for candidate in candidates:
            if Path(candidate).exists():
                player = _first_available(AUDIO_PLAYERS)
                if player:
                    subprocess.Popen([player, candidate], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    return
        # запасной вариант: терминальный «звоночек»
        print("\a", end="", flush=True)

    # ---------------------------------------------------------------- audio
    @contextmanager
    def mic_stream(self, sample_rate: int) -> Iterator[object]:
        """Поток микрофона: дочерний процесс записи с чтением сырого PCM."""
        recorder = _first_available(AUDIO_RECORDERS)
        if not recorder:
            raise RuntimeError("нет программы записи звука (parecord/pw-record/arecord)")
        name = Path(recorder).name
        if name == "arecord":
            args = [recorder, "-f", "S16_LE", "-r", str(sample_rate), "-c", "1", "-t", "raw", "-q"]
        elif name == "pw-record":
            args = [recorder, "--format", "s16", "--rate", str(sample_rate), "--channels", "1", "-"]
        else:
            args = [recorder, "--raw", "--rate", str(sample_rate), "--channels", "1",
                    "--format", "s16le", "--latency-msec", "60"]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            yield process.stdout
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()

    # --------------------------------------------------------------- hotkey
    def setup_hotkey(self, spec: str, command: str) -> tuple[bool, str]:
        """На XFCE вешаем горячую клавишу через xfconf; иначе подсказываем вручную."""
        if not _which("xfconf-query"):
            return False, self.hotkey_hint(spec, command)
        xfce_spec = self._xfce_spec(spec)
        try:
            result = subprocess.run(
                ["xfconf-query", "-c", "xfce4-keyboard-shortcuts", "-p", f"/commands/custom/{xfce_spec}",
                 "-n", "-t", "string", "-s", command],
                capture_output=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False, self.hotkey_hint(spec, command)
        if result.returncode == 0:
            return True, f"Горячая клавиша {spec} назначена"
        return False, self.hotkey_hint(spec, command)

    def _xfce_spec(self, spec: str) -> str:
        mapping = {"Super": "<Super>", "super": "<Super>", "Ctrl": "<Primary>", "Ctrl+": "<Primary>",
                   "Control": "<Primary>", "Alt": "<Alt>", "Shift": "<Shift>"}
        parts = [part.strip() for part in spec.replace("+", " ").split()]
        if not parts:
            return "<Super>j"
        mods = "".join(mapping.get(part, f"<{part}>") for part in parts[:-1])
        return f"{mods}{parts[-1].lower()}"

    def hotkey_hint(self, spec: str, command: str) -> str:
        return (f"Назначьте {spec} вручную: Настройки → Клавиатура → Ярлыки приложений → "
                f"команда «{command}»")

    def tray_supported(self) -> bool:
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
