"""Провайдеры Jarvis: модели, звук, поиск, уведомления.

Все тяжёлые объекты создаются лениво: пока навык не попросит озвучку или
распознавание, ни сервер, ни модель в память не поднимаются.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from ..core import paths
from ..platform import get_platform

log = logging.getLogger("jarvis.providers")


def substitute(text: str) -> str:
    """Подставляет пути в командах из конфига (см. ``core.paths.substitute``)."""
    return paths.substitute(text)


class Providers:
    """Единая точка доступа к внешним возможностям."""

    def __init__(self, config: Any, journal: Any = None):
        self.config = config
        self.journal = journal
        self._llm = None
        self._stt = None
        self._tts = None
        self._search = None
        self._recorder = None

    # ------------------------------------------------------------------- lazy
    @property
    def llm(self):
        if self._llm is None:
            from .llm import LLMProvider

            section = self.config.section("llm")
            self._llm = LLMProvider(
                base_url=section.get("base_url", ""),
                api_key=section.get("api_key", "") or "",
                model=section.get("model", ""),
                timeout=float(section.get("timeout_seconds", 8)),
                retries=int(section.get("max_retries", 1)),
                enabled=bool(section.get("enabled", True)),
                max_tokens=int(section.get("max_tokens", 400)),
                temperature=float(section.get("temperature", 0.2)),
            )
        return self._llm

    @property
    def stt(self):
        if self._stt is None:
            from .stt import WhisperSTT

            section = self.config.section("stt")
            voice = self.config.section("voice")
            self._stt = WhisperSTT(
                url=section.get("url", ""),
                model_path=substitute(section.get("model", "")),
                language=voice.get("language", self.config.get("assistant.language", "ru"))[:2],
                server_command=substitute(section.get("server_command", "")),
                cli_command=substitute(section.get("cli_command", "")),
                mode=section.get("mode", "on_demand"),
                timeout=float(section.get("timeout_seconds", 30)),
                retries=int(section.get("max_retries", 1)),
                idle_timeout=float(section.get("idle_timeout", 300)),
                no_speech_threshold=float(section.get("no_speech_threshold", 0.6)),
                prompt=section.get("prompt", ""),
            )
        return self._stt

    @property
    def tts(self):
        if self._tts is None:
            from .tts import PiperTTS

            section = self.config.section("tts")
            self._tts = PiperTTS(
                url=section.get("url", ""),
                voice=section.get("voice", ""),
                data_dir=substitute(section.get("data_dir", "")),
                server_command=substitute(section.get("server_command", "")),
                cli_command=substitute(section.get("cli_command", "")),
                mode=section.get("mode", "on_demand"),
                timeout=float(section.get("timeout_seconds", 20)),
                idle_timeout=float(section.get("idle_timeout", 300)),
            )
        return self._tts

    @property
    def search(self):
        if self._search is None:
            from .search import WebSearch

            section = self.config.section("search")
            self._search = WebSearch(
                max_results=int(section.get("max_results", 5)),
                timeout=float(section.get("timeout_seconds", 12)),
                enabled=bool(section.get("enabled", True)),
            )
        return self._search

    @property
    def recorder(self):
        if self._recorder is None:
            from .audio import Recorder

            section = self.config.section("voice")
            self._recorder = Recorder(
                sample_rate=int(section.get("sample_rate", 16000)),
                mode=section.get("record_mode", "vad"),
                record_seconds=float(section.get("record_seconds", 4.0)),
                max_record_seconds=float(section.get("max_record_seconds", 12.0)),
                silence_seconds=float(section.get("silence_seconds", 0.9)),
                wait_speech_seconds=float(section.get("wait_speech_seconds", 4.0)),
                start_speech_seconds=float(section.get("start_speech_seconds", 0.09)),
                min_speech_seconds=float(section.get("min_speech_seconds", 0.18)),
                aggressiveness=int(section.get("vad_aggressiveness", 2)),
                beep=bool(section.get("beep", True)),
            )
        return self._recorder

    # --------------------------------------------------------------- shortcuts
    @property
    # ------------------------------------------------------- удобства для навыков
    def search_web(self, query: str, limit: int | None = None) -> list[dict[str, str]]:
        """Поиск в интернете. Ошибка → ProviderError (её покажет ядро)."""
        results = self.search.search(query)
        return results[:limit] if limit else results

    def llm_available(self) -> bool:
        ready, _reason = self.llm.available()
        return ready

    def ask_model(self, prompt: str, *, system: str | None = None) -> str | None:
        """Короткий ответ модели; при сбое — None (ядро скажет «не получилось»)."""
        if not self.llm_available():
            return None
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            return self.llm.chat_text(messages)
        except Exception:  # noqa: BLE001 - сеть и модель бывают недоступны
            log.warning("модель не ответила", exc_info=True)
            return None

    @property
    def voice_enabled(self) -> bool:
        """Включён ли голос (микрофон и озвучка) — спрашивают все интерфейсы."""
        return bool(self.config.get("voice.enabled", False))

    def speak(self, text: str, language: str | None = None) -> bool:
        if not self.voice_enabled or not text:
            return False
        try:
            return self.tts.speak(text)
        except Exception as exc:  # noqa: BLE001 - голос не должен ломать ответ
            log.warning("озвучка не удалась: %s", exc)
            return False

    def transcribe(self, wav_path: Path) -> str:
        return self.stt.transcribe(wav_path)

    def record(self) -> Path | None:
        return self.recorder.record()

    def notify(self, title: str, body: str, urgency: str = "normal") -> bool:
        try:
            return get_platform().notify(title, body, urgency)
        except Exception as exc:  # noqa: BLE001
            log.warning("уведомление не показано: %s", exc)
            return False

    # ------------------------------------------------------------------ upkeep
    def maintain(self) -> None:
        """Останавливает серверы, которые давно не нужны (экономия памяти)."""
        for provider in (self._stt, self._tts):
            if provider is not None:
                provider.server.stop_if_idle()

    def stop(self) -> None:
        for provider in (self._stt, self._tts):
            if provider is not None:
                provider.server.stop()

    def state(self) -> dict[str, Any]:
        result: dict[str, Any] = {"voice_enabled": self.voice_enabled}
        for name in ("llm", "stt", "tts", "search", "recorder"):
            provider = getattr(self, name if name != "recorder" else "recorder")
            try:
                result[name] = provider.state()
            except Exception as exc:  # noqa: BLE001
                result[name] = {"available": False, "reason": str(exc)}
        return result
