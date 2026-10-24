"""Слово-активатор («Джарвис»): слушает микрофон и дёргает демон.

Логика перенесена из старой версии (она себя оправдала), но живёт теперь
внутри демона, а не отдельным сервисом: меньше процессов — меньше памяти.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.providers.wakeword")

CHUNK_SAMPLES = 1280  # ~80 мс при 16 кГц — родной размер окна openWakeWord
CHUNK_BYTES = CHUNK_SAMPLES * 2
SAMPLE_RATE = 16000


class WakeWordListener:
    """Постоянно слушает микрофон в отдельном потоке."""

    def __init__(self, *, model_path: str, threshold: float = 0.25, cooldown: float = 3.0,
                 trigger_frames: int = 3, on_detect=None):
        self.model_path = Path(model_path) if model_path else None
        self.threshold = threshold
        self.cooldown = cooldown
        self.trigger_frames = max(1, trigger_frames)
        self.on_detect = on_detect
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_trigger = 0.0

    # ------------------------------------------------------------------ state
    def available(self) -> tuple[bool, str]:
        if not self.model_path or not self.model_path.exists():
            return False, f"нет модели слова-активатора: {self.model_path}"
        try:
            import openwakeword  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as exc:
            return False, f"не установлен пакет {exc.name}"
        return True, "готов"

    def start(self) -> bool:
        ready, reason = self.available()
        if not ready:
            log.warning("слово-активатор не запущен: %s", reason)
            return False
        self._thread = threading.Thread(target=self._loop, name="jarvis-wakeword", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------- loop
    def _loop(self) -> None:
        import numpy as np
        from openwakeword.model import Model

        framework = "onnx" if self.model_path.suffix == ".onnx" else "tflite"
        try:
            model = Model(wakeword_models=[str(self.model_path)], inference_framework=framework)
        except TypeError:  # старые версии openWakeWord
            model = Model(wakeword_model_paths=[str(self.model_path)])
        name = list(model.models.keys())[0]
        log.info("слушаю слово-активатор %s (порог %.2f)", self.model_path.name, self.threshold)

        from ..core import paths
        from ..platform import get_platform

        platform = get_platform()
        scores: list[float] = []
        while not self._stop.is_set():
            try:
                with platform.mic_stream(SAMPLE_RATE) as stream:
                    while not self._stop.is_set():
                        chunk = stream.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        if len(chunk) < CHUNK_BYTES:
                            continue
                        audio = np.frombuffer(chunk, dtype=np.int16)
                        score = float(model.predict(audio).get(name, 0.0))
                        scores.append(score)
                        if len(scores) > self.trigger_frames:
                            scores.pop(0)
                        now = time.monotonic()
                        if (len(scores) >= self.trigger_frames
                                and all(value >= self.threshold for value in scores)
                                and now - self._last_trigger >= self.cooldown):
                            self._last_trigger = now
                            log.info("услышал слово-активатор (score=%.2f)", max(scores))
                            scores.clear()
                            model.reset()
                            if self.on_detect:
                                try:
                                    self.on_detect()
                                except Exception:  # noqa: BLE001
                                    log.exception("обработчик слова-активатора упал")
            except Exception as exc:  # noqa: BLE001 - микрофон может отвалиться
                log.warning("микрофон недоступен для слова-активатора (%s), повтор через 3 с", exc)
                time.sleep(3)
