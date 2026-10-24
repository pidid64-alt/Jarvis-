"""Синтез речи: Piper (сервер или CLI) плюс аварийный системный вариант."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..core import paths
from ..core.errors import ProviderError
from .http import CircuitBreaker, HttpClient
from .process import ManagedProcess

log = logging.getLogger("jarvis.providers.tts")

#: Замена цифр/сокращений на человеческую речь (порт старого tts_norm)
_MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
               "августа", "сентября", "октября", "ноября", "декабря"]
_SIZE_UNITS = {"K": "килобайт", "M": "мегабайт", "G": "гигабайт", "T": "терабайт",
               "Ki": "килобайт", "Mi": "мегабайт", "Gi": "гигабайт", "Ti": "терабайт"}
_SIZE_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(Ki|Mi|Gi|Ti|[KMGT])B?\b")
_PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def _plural(number: int, one: str, few: str, many: str) -> str:
    number = abs(int(number)) % 100
    if 11 <= number <= 14:
        return many
    digit = number % 10
    if digit == 1:
        return one
    if 2 <= digit <= 4:
        return few
    return many


def normalize_for_speech(text: str) -> str:
    """Готовит текст к озвучке (русские единицы, проценты, даты)."""
    if not text:
        return text

    def size(match: re.Match[str]) -> str:
        value, unit = match.group(1), match.group(2)
        word = _SIZE_UNITS.get(unit, "")
        return f"{value.replace('.', ',')} {word}".strip()

    def date(match: re.Match[str]) -> str:
        day, month, year = int(match.group(3)), int(match.group(2)), match.group(1)
        if 1 <= month <= 12:
            return f"{day} {_MONTHS_GEN[month - 1]} {year} года"
        return match.group(0)

    result = _ISO_DATE_RE.sub(date, text)
    result = _SIZE_RE.sub(size, result)
    result = _PERCENT_RE.sub(
        lambda m: f"{m.group(1).replace('.', ',')} {_plural(round(float(m.group(1).replace(',', '.'))), 'процент', 'процента', 'процентов')}",
        result,
    )
    return result


class PiperTTS:
    """Озвучка через piper: HTTP-сервер по требованию, иначе CLI."""

    def __init__(self, *, url: str = "", voice: str = "", data_dir: str = "",
                 server_command: str = "", mode: str = "on_demand", timeout: float = 20.0,
                 cli_command: str = "", output_file: Path | None = None, idle_timeout: float = 300.0):
        self.url = url
        self.voice = voice
        self.data_dir = data_dir
        self.cli_command = cli_command
        self.output_file = output_file or (paths.state_dir() / "reply.wav")
        self.client = HttpClient(timeout=timeout, retries=0,
                                 breaker=CircuitBreaker("tts", threshold=3, cooldown=45.0))
        self.server = ManagedProcess("tts", server_command, port=self._port_from_url(), mode=mode,
                                     idle_timeout=idle_timeout, log_file=paths.state_dir() / "piper.log")

    def _port_from_url(self) -> int | None:
        try:
            tail = self.url.split("//", 1)[1]
            return int(tail.split("/", 1)[0].split(":")[1])
        except (IndexError, ValueError):
            return None

    # ------------------------------------------------------------------ state
    def available(self) -> tuple[bool, str]:
        if self.server.mode == "off" and not self.cli_command:
            return False, "озвучка выключена в настройках"
        if self.voice and self.data_dir and not self.cli_command:
            voice_file = Path(self.data_dir) / f"{self.voice}.onnx"
            if self.data_dir and not voice_file.exists() and self.voice.endswith(("-medium", "-low", "-high")):
                return False, f"не найден голос: {voice_file}"
        return True, "готов"

    # ------------------------------------------------------------------- call
    def synthesize(self, text: str) -> bytes:
        ready, reason = self.available()
        if not ready:
            raise ProviderError(reason)
        payload_text = normalize_for_speech(text)
        if self.server.command and self.url and self.server.ensure():
            response = self.client.post_json(self.url, {"text": payload_text})
            self.server.mark_used()
            if response.body:
                return response.body
            raise ProviderError("сервер озвучки вернул пустой звук")
        if self.cli_command:
            return self._via_cli(payload_text)
        raise ProviderError("озвучка недоступна")

    def _via_cli(self, text: str) -> bytes:
        from ..platform import get_platform

        safe = text.replace('"', "'").replace("\n", " ")
        command = (self.cli_command
                   .replace("{text}", safe)
                   .replace("{voice}", self.voice)
                   .replace("{data_dir}", self.data_dir)
                   .replace("{out}", str(self.output_file)))
        result = get_platform().run(command, timeout=self.client.timeout)
        if result.returncode != 0 or not self.output_file.exists():
            raise ProviderError(f"CLI-озвучка не сработала (код {result.returncode})")
        data = self.output_file.read_bytes()
        return data

    def speak(self, text: str) -> bool:
        """Синтезирует и проигрывает. False — озвучить не удалось."""
        if not text:
            return True
        from ..platform import get_platform

        try:
            audio = self.synthesize(text)
        except ProviderError as exc:
            log.warning("озвучка недоступна: %s", exc)
            return self._fallback_speak(text)
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        self.output_file.write_bytes(audio)
        return get_platform().play_wav(self.output_file)

    def _fallback_speak(self, text: str) -> bool:
        """Если Piper молчит — пробуем системный синтезатор."""
        from ..platform import get_platform

        platform = get_platform()
        if platform.name == "windows":
            script = ("Add-Type -AssemblyName System.Speech; "
                      "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                      f"$s.Speak('{text.replace(chr(39), chr(39) * 2)}')")
            result = platform.run(f'powershell -NoProfile -Command "{script}"', timeout=60)
            return result.returncode == 0
        import shutil

        if shutil.which("spd-say"):
            return platform.run(f"spd-say {text!r}", timeout=60).returncode == 0
        if shutil.which("espeak-ng"):
            return platform.run(f"espeak-ng -v ru {text!r}", timeout=60).returncode == 0
        return False

    def state(self) -> dict[str, object]:
        ready, reason = self.available()
        return {"available": ready, "reason": reason, "voice": self.voice, "server": self.server.state()}
