"""Запись с микрофона с определением речи (VAD).

Без ``webrtcvad`` работает запасной режим — запись фиксированной длины,
как в старой версии. Микрофон читается через платформенный слой, поэтому
Linux (parecord/pw-record) и Windows (sounddevice) выглядят одинаково.
"""

from __future__ import annotations

import contextlib
import logging
import wave
from pathlib import Path

from ..core import paths
from ..core.errors import ProviderError
from ..core.matcher import SpeechBuffer
from ..platform import get_platform

log = logging.getLogger("jarvis.providers.audio")

FRAME_MS = 30  # webrtcvad работает с 10/20/30 мс


class Recorder:
    """Записывает одну фразу и сохраняет её в wav."""

    def __init__(self, *, sample_rate: int = 16000, mode: str = "vad", record_seconds: float = 4.0,
                 max_record_seconds: float = 12.0, silence_seconds: float = 0.9,
                 wait_speech_seconds: float = 4.0, start_speech_seconds: float = 0.09,
                 min_speech_seconds: float = 0.18, aggressiveness: int = 2, beep: bool = True):
        self.sample_rate = sample_rate
        self.mode = mode
        self.record_seconds = record_seconds
        self.max_record_seconds = max_record_seconds
        self.silence_seconds = silence_seconds
        self.wait_speech_seconds = wait_speech_seconds
        self.start_speech_seconds = start_speech_seconds
        self.min_speech_seconds = min_speech_seconds
        self.aggressiveness = max(0, min(3, int(aggressiveness)))
        self.beep = beep

    # ------------------------------------------------------------------ state
    def available(self) -> tuple[bool, str]:
        platform = get_platform()
        if platform.name == "windows":
            try:
                import sounddevice as sd
            except ImportError:
                return False, "не установлен пакет sounddevice (запись микрофона)"
            # Одного наличия пакета мало: если микрофона нет или Windows его не
            # пускает, запись падала непонятной ошибкой уже во время разговора.
            try:
                devices = [item for item in sd.query_devices()
                           if int(item.get("max_input_channels", 0)) > 0]
            except Exception as exc:  # noqa: BLE001 - звуковая система бывает занята
                return False, f"звуковая система недоступна: {exc}"
            if not devices:
                return False, "Windows не видит ни одного микрофона"
            return True, f"готов ({devices[0].get('name', 'микрофон')})"
        import shutil

        for name in ("parecord", "pw-record", "arecord"):
            if shutil.which(name):
                return True, f"готов ({name})"
        return False, "нет программы записи звука (parecord/pw-record/arecord)"

    # ---------------------------------------------------------------- recording
    @contextlib.contextmanager
    def _open_mic(self, sample_rate: int):
        """Открывает поток микрофона и объясняет сбой человеческими словами.

        PortAudio на Windows отвечает своими ошибками (``PortAudioError``,
        «No Default Input Device»), и раньше такая ошибка доходила до окна как
        «внутренняя ошибка» без причины.
        """
        try:
            with get_platform().mic_stream(sample_rate) as stream:
                yield stream
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - звуковая система чужая территория
            raise ProviderError(f"{exc} — проверьте, что микрофон подключён и разрешён "
                                f"в настройках звука") from exc

    def record(self, path: Path | None = None) -> Path | None:
        """Записывает фразу. ``None`` — речи не было."""
        target = Path(path) if path else paths.state_dir() / "command.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        ready, reason = self.available()
        if not ready:
            raise ProviderError(reason)

        if self.beep:
            get_platform().beep()

        vad = self._load_vad()
        frame_bytes = int(self.sample_rate * FRAME_MS / 1000) * 2
        buffer = SpeechBuffer(
            start_frames=max(1, round(self.start_speech_seconds * 1000 / FRAME_MS)),
            min_frames=max(1, round(self.min_speech_seconds * 1000 / FRAME_MS)),
            silence_frames=max(1, round(self.silence_seconds * 1000 / FRAME_MS)),
            preroll_frames=10,
        )
        frames: list[bytes] = []
        total_ms = 0
        waited_ms = 0
        finished = False

        with self._open_mic(self.sample_rate) as stream:
            while total_ms < self.max_record_seconds * 1000:
                chunk = self._read(stream, frame_bytes)
                if not chunk:
                    break
                if len(chunk) < frame_bytes:
                    continue
                total_ms += FRAME_MS
                if vad is not None and self.mode == "vad":
                    is_speech = bool(vad.is_speech(chunk, self.sample_rate))
                    if not buffer.started:
                        waited_ms += FRAME_MS
                        if waited_ms > self.wait_speech_seconds * 1000:
                            log.info("речь не началась за %.1f с — отмена", self.wait_speech_seconds)
                            return None
                    if buffer.feed(chunk, is_speech):
                        frames = buffer.frames
                        finished = True
                        break
                else:
                    frames.append(chunk)

        if self.mode == "vad" and vad is not None:
            if not finished or not buffer.valid():
                return None
            frames = buffer.frames
        else:
            frames = frames[: int(self.record_seconds * 1000 / FRAME_MS)]

        if not frames:
            return None
        self._write_wav(target, b"".join(frames))
        return target

    def _read(self, stream, size: int) -> bytes:
        try:
            data = stream.read(size)
        except AttributeError:  # sounddevice-совместимый поток
            import numpy as np

            audio, overflowed = stream.read(size // 2)
            if overflowed:
                log.debug("переполнение буфера звука")
            data = np.asarray(audio, dtype="int16").tobytes() if audio is not None else b""
        if isinstance(data, tuple):
            data = data[0]
        return data or b""

    def _load_vad(self):
        if self.mode != "vad":
            return None
        try:
            import webrtcvad  # type: ignore
        except ImportError:
            log.warning("webrtcvad не установлен — записываю фиксированные %.1f с", self.record_seconds)
            return None
        return webrtcvad.Vad(self.aggressiveness)

    def _write_wav(self, path: Path, pcm: bytes) -> None:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.sample_rate)
            handle.writeframes(pcm)

    def state(self) -> dict[str, object]:
        ready, reason = self.available()
        return {"available": ready, "reason": reason, "mode": self.mode, "sample_rate": self.sample_rate}
