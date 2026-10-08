"""Распознавание речи: whisper.cpp (сервер или CLI) с ленивым запуском."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ..core import paths
from ..core.errors import ProviderError, ProviderTimeoutError
from .http import CircuitBreaker, HttpClient
from .process import ManagedProcess

log = logging.getLogger("jarvis.providers.stt")

NON_SPEECH = {"музыка", "аплодисменты", "тишина", "субтитры", "[музыка]", "[аплодисменты]"}


class WhisperSTT:
    """Берёт звук из wav-файла и возвращает текст."""

    def __init__(self, *, url: str, model_path: str = "", language: str = "ru",
                 server_command: str = "", mode: str = "on_demand", timeout: float = 30.0,
                 retries: int = 1, idle_timeout: float = 300.0, cli_command: str = "",
                 no_speech_threshold: float = 0.6, prompt: str = ""):
        self.url = url
        self.model_path = model_path
        self.language = language
        self.cli_command = cli_command
        self.no_speech_threshold = no_speech_threshold
        self.prompt = prompt
        self.client = HttpClient(timeout=timeout, retries=retries,
                                 breaker=CircuitBreaker("stt", threshold=3, cooldown=45.0))
        self.server = ManagedProcess(
            "stt",
            server_command,
            port=self._port_from_url(),
            mode=mode,
            idle_timeout=idle_timeout,
            log_file=paths.state_dir() / "whisper.log",
        )

    def _port_from_url(self) -> int | None:
        try:
            tail = self.url.split("//", 1)[1]
            return int(tail.split("/", 1)[0].split(":")[1])
        except (IndexError, ValueError):
            return None

    # ------------------------------------------------------------------ state
    def available(self) -> tuple[bool, str]:
        if self.server.mode == "off":
            return False, "распознавание речи выключено в настройках"
        if self.model_path and not Path(self.model_path).exists() and not self.cli_command:
            return False, f"нет файла модели: {self.model_path}"
        if self.server.mode == "on_demand" and not self.server.command and not self.cli_command:
            return False, "не задана команда запуска распознавания"
        return True, "готов"

    # ------------------------------------------------------------------- call
    def transcribe(self, wav_path: Path) -> str:
        if not Path(wav_path).exists():
            raise ProviderError("файл записи не найден")
        ready, reason = self.available()
        if not ready:
            raise ProviderError(reason)

        if self.server.command and self.server.mode != "off":
            if self.server.ensure():
                try:
                    return self._via_server(wav_path)
                except ProviderError as exc:
                    log.warning("сервер распознавания не ответил (%s), пробую CLI", exc)
                    if not self.cli_command:
                        raise
            elif not self.cli_command:
                raise ProviderError("сервер распознавания не поднялся")

        if self.cli_command:
            return self._via_cli(wav_path)
        raise ProviderError("распознавание недоступно")

    def _via_server(self, wav_path: Path) -> str:
        fields = {
            "language": self.language,
            "response_format": "json",
            "temperature": "0.0",
            "temperature_inc": "0.0",
            "no_speech_thold": str(self.no_speech_threshold),
        }
        if self.prompt:
            fields["prompt"] = self.prompt[:500]
        self.server.mark_used()
        try:
            response = self.client.post_multipart(
                self.url, fields, "file", wav_path.name, wav_path.read_bytes()
            )
        except ProviderTimeoutError as exc:
            raise ProviderTimeoutError("распознавание не уложилось в таймаут") from exc
        data = response.json() if response.body else {}
        text = (data or {}).get("text", "") if isinstance(data, dict) else ""
        return self._clean(text)

    def _via_cli(self, wav_path: Path) -> str:
        from ..platform import get_platform

        command = (self.cli_command
                   .replace("{wav}", str(wav_path))
                   .replace("{model}", self.model_path)
                   .replace("{language}", self.language))
        started = time.monotonic()
        result = get_platform().run(command, timeout=self.client.timeout)
        if result.returncode != 0:
            raise ProviderError(f"распознавание вернуло код {result.returncode}")
        log.debug("CLI-распознавание заняло %.2f с", time.monotonic() - started)
        return self._clean(result.output)

    @staticmethod
    def _clean(text: str) -> str:
        text = (text or "").strip()
        if text.startswith("{"):
            try:
                text = json.loads(text).get("text", "") or ""
            except ValueError:
                pass
        text = text.strip()
        if text.lower().strip("[]") in NON_SPEECH:
            return ""
        return text

    def state(self) -> dict[str, object]:
        ready, reason = self.available()
        return {"available": ready, "reason": reason, "server": self.server.state(),
                "model": Path(self.model_path).name if self.model_path else ""}
