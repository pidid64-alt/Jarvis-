"""Демон Jarvis: горячая клавиша, слово-активатор и автономные проверки.

Работает одинаково на Linux и Windows: платформенные различия спрятаны в
``jarvis.platform``. Демон ничего не решает сам — он вызывает ядро.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from pathlib import Path

from ..core import paths
from ..core.autonomy import Autonomy
from ..core.errors import JarvisError
from ..core.logging_setup import get_logger
from ..core.router import is_confirmation, is_denial

log = get_logger("interfaces.daemon")

TRIGGER_MAX_AGE = 5.0


class Daemon:
    """Ждёт пробуждения (хоткей/слово/команда `jarvis trigger`) и обслуживает его."""

    def __init__(self, assistant, *, autonomy: bool = True, voice: bool = True):
        self.assistant = assistant
        self.voice = voice
        self.trigger = threading.Event()
        self.stopping = threading.Event()
        self.autonomy = (Autonomy(assistant.config, assistant.providers, assistant.journal,
                                  assistant=assistant) if autonomy else None)
        self._pid_file = paths.pid_file()
        self._trigger_file = paths.state_dir() / "trigger"
        self._hotkey_listener = None

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        paths.ensure_dirs()
        self._pid_file.write_text(str(_current_pid()), encoding="utf-8")
        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, lambda *_: self.trigger.set())
        signal.signal(signal.SIGTERM, lambda *_: self.stopping.set())
        signal.signal(signal.SIGINT, lambda *_: self.stopping.set())
        self._start_hotkey()
        if bool(self.assistant.config.get("voice.wakeword.enabled", False)):
            self._start_wakeword()
        self.assistant.events.publish("daemon_started")
        self.assistant.journal.info("daemon", "Jarvis запущен и ждёт команды")

    def _start_hotkey(self) -> None:
        spec = str(self.assistant.config.get("hotkey.spec", "Super+J"))
        if not bool(self.assistant.config.get("hotkey.enabled", True)):
            return
        from ..platform import get_platform, platform_name

        if platform_name() == "windows":
            from ..platform.windows import WindowsHotkeyListener

            listener = WindowsHotkeyListener(spec, self.trigger.set)
            if listener.start():
                self._hotkey_listener = listener
                return
        ok, hint = get_platform().setup_hotkey(spec, f"{sys.executable} -m jarvis trigger")
        if not ok:
            log.warning("горячую клавишу не удалось назначить: %s", hint)

    def _start_wakeword(self) -> None:
        """Слушатель слова-активатора: отдельный поток, при обнаружении — триггер."""
        try:
            from ..providers.wakeword import WakeWordListener
        except ImportError as exc:  # pragma: no cover
            log.warning("слово-активатор недоступен: %s", exc)
            return
        section = self.assistant.config.section("voice").get("wakeword", {}) or {}
        listener = WakeWordListener(
            model_path=section.get("model", ""),
            threshold=float(section.get("threshold", 0.25)),
            cooldown=float(section.get("cooldown_seconds", 3.0)),
            on_detect=self.trigger.set,
        )
        listener.start()

    # ------------------------------------------------------------------- loop
    def run(self) -> None:
        self.start()
        tick = 0
        while not self.stopping.is_set():
            fired = self._wait_for_trigger(timeout=20.0)
            if self.stopping.is_set():
                break
            if fired:
                self._handle_wake()
            tick += 1
            if self.autonomy is not None and tick % 3 == 0:
                self._run_autonomy()
            if self.assistant.providers is not None:
                self.assistant.providers.maintain()
        self.stop()

    def _wait_for_trigger(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not self.stopping.is_set():
            if self.trigger.is_set():
                self.trigger.clear()
                return True
            if self._trigger_file.exists():
                try:
                    age = time.time() - self._trigger_file.stat().st_mtime
                except OSError:
                    age = 0
                if age <= TRIGGER_MAX_AGE:
                    try:
                        self._trigger_file.unlink()
                    except OSError:
                        pass
                    return True
            if time.monotonic() >= deadline:
                return False
            self.stopping.wait(0.2)
        return False

    def _handle_wake(self) -> None:
        if not self.voice:
            return
        try:
            self.assistant.handle_voice(confirm_callback=self._voice_confirm, source="voice")
        except JarvisError as exc:
            self.assistant.journal.error("daemon", f"ошибка обработки голоса: {exc}")
        except Exception as exc:  # noqa: BLE001 - демон продолжает жить
            log.exception("неожиданная ошибка в голосовом цикле")
            self.assistant.journal.error("daemon", f"внутренняя ошибка: {exc!r}")

    def _voice_confirm(self, question: str) -> bool:
        """Голосовое подтверждение опасного действия: сомнение — отказ."""
        providers = self.assistant.providers
        providers.speak(question)
        try:
            wav = providers.record()
            if wav is None:
                providers.speak(self.assistant.t("confirm.yes_hint"))
                return False
            answer = providers.transcribe(wav)
        except JarvisError as exc:
            log.warning("подтверждение не получено: %s", exc)
            return False
        if is_denial(answer):
            return False
        return is_confirmation(answer)

    def _run_autonomy(self) -> None:
        try:
            self.autonomy.tick()
        except Exception as exc:  # noqa: BLE001
            log.exception("автономные проверки упали")
            self.assistant.journal.error("autonomy", repr(exc))

    # ------------------------------------------------------------------ stop
    def stop(self) -> None:
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()
        self.assistant.shutdown()
        try:
            self._pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        self.assistant.journal.info("daemon", "Jarvis остановлен")


def _current_pid() -> int:
    import os

    return os.getpid()


def fire_trigger() -> bool:
    """Будит запущенный демон: файл-триггер и SIGUSR1 (если он есть)."""
    paths.ensure_dirs()
    trigger = paths.state_dir() / "trigger"
    trigger.write_text(str(time.time()), encoding="utf-8")
    sent = False
    pid_path = paths.pid_file()
    if hasattr(signal, "SIGUSR1") and pid_path.exists():
        try:
            import os

            os.kill(int(pid_path.read_text().strip()), signal.SIGUSR1)
            sent = True
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            sent = False
    return sent or trigger.exists()


def daemon_running() -> bool:
    pid_path = paths.pid_file()
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return False
    import os

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
