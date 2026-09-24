#!/usr/bin/env python3
"""
platform_support.py
-------------------
Кросс-платформенный слой Jarvis (Linux и Windows).

Здесь собраны все точки, где раньше проект жёстко полагался на Linux:
уведомления, воспроизведение звука, запись микрофона (parcord/arecord),
открытие URL, файловые блокировки (flock), файловый триггер-проспальщик
(аналог SIGUSR1, которого на Windows нет) и загрузка env-файла.

Модуль обязан импортироваться без сторонних зависимостей: всё тяжёлое
(sounddevice, winsound) подтягивается лениво внутри функций.
"""

import json
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

IS_WINDOWS = os.name == "nt"

BASE_DIR = Path(__file__).resolve().parent
# Один и тот же путь на обеих платформах — проще документировать и скриптам.
STATE_DIR = Path.home() / ".local" / "share" / "jarvis"
TRIGGER_FILE = STATE_DIR / "trigger"
# Свежий trigger живёт меньше секунды: старый файл не будит демон дважды.
TRIGGER_MAX_AGE = 5.0

ENV_FILE = Path.home() / ".config" / "jarvis" / "env"


# ---------------------------------------------------------------------------
# Env-файл (SYSTEMD EnvironmentFile= на Linux, ручная загрузка на Windows)
# ---------------------------------------------------------------------------

def load_env_file(path: Path = ENV_FILE) -> dict:
    """Читает KEY=VALUE строки (как в systemd EnvironmentFile).

    Ничего не перезаписывает: уже заданные переменные окружения имеют
    приоритет. Возвращает применённые пары (для логов/отладки).
    """
    applied = {}
    if not path.exists():
        return applied
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key or key in os.environ:
                continue
            os.environ[key] = value
            applied[key] = value
    except OSError:
        logging.exception("не удалось прочитать env-файл %s", path)
    return applied


# ---------------------------------------------------------------------------
# Уведомления рабочего стола
# ---------------------------------------------------------------------------

_TOAST_PS = r"""
param([string]$Title, [string]$Body, [string]$Urgency)
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$urg = if ($Urgency -eq 'critical') { 'scenario=""alarm""' } else { '' }
$xml.LoadXml("<toast $urg><visual><binding template=""ToastText02""><text id=""1"">$Title</text><text id=""2"">$Body</text></binding></visual></toast>")
$toast = New-Object Windows.UI.Notifications.ToastNotification($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Jarvis').Show($toast)
"""


def notify(title: str, body: str, urgency: str = "normal"):
    """Уведомление рабочего стола: notify-send на Linux, toast на Windows."""
    if IS_WINDOWS:
        try:
            ps1 = STATE_DIR / "notify_toast.ps1"
            if not ps1.exists():
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                ps1.write_text(_TOAST_PS, encoding="utf-8")
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-File", str(ps1),
                 "-Title", str(title), "-Body", str(body), "-Urgency", urgency],
                check=False, timeout=6,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-a", "Jarvis", title, body],
            check=False, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


# ---------------------------------------------------------------------------
# Звук
# ---------------------------------------------------------------------------

def play_wav(path: Path, timeout: float = 30) -> bool:
    """Воспроизводит WAV-файл. Windows — winsound, Linux — paplay/afplay."""
    if IS_WINDOWS:
        try:
            import winsound
            # SND_SYNC == 0 (default, синхронно), в Python 3.13 константа убрана — используем только SND_FILENAME
            flags = getattr(winsound, 'SND_FILENAME', 0x00020000)
            if hasattr(winsound, 'SND_SYNC'):
                flags |= winsound.SND_SYNC
            winsound.PlaySound(str(path), flags)
            return True
        except Exception as e:  # noqa: BLE001 — устройство может быть занято
            logging.warning("winsound не смог проиграть ответ: %s", e)
            return False
    player = "paplay" if _which("paplay") else "aplay"
    if not _which(player):
        logging.warning("нет плеера WAV (paplay/aplay)")
        return False
    try:
        subprocess.run([player, str(path)], check=False, timeout=timeout)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logging.error("%s завис/не найден при воспроизведении ответа", player)
        return False


def default_beep_command() -> str:
    """Короткий сигнал «говори»: командная строка под текущую платформу."""
    if IS_WINDOWS:
        # winsound проиграет системный MB_ICONASTERISK из коробки, без файлов.
        return ""
    return "paplay /usr/share/sounds/freedesktop/stereo/message.oga"


def beep(cfg: dict):
    """Сигнал «говори» — чтобы не начинать фразу раньше записи."""
    if IS_WINDOWS:
        # config.json приходит из Linux-установки, где beep_command = paplay;
        # на Windows надёжнее системный winsound, без внешних файлов.
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:  # noqa: BLE001
            pass
        return
    beep_cmd = cfg.get("beep_command", "")
    if not beep_cmd:
        return
    try:
        subprocess.run(beep_cmd, shell=True, check=False, timeout=3,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        pass


# ---------------------------------------------------------------------------
# Открытие URL/приложений
# ---------------------------------------------------------------------------

def open_url(url: str) -> bool:
    """Открывает URL в браузере по умолчанию."""
    try:
        if IS_WINDOWS:
            os.startfile(url)  # noqa: S606 — стандартный способ на Windows
            return True
        subprocess.Popen(["xdg-open", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:  # noqa: BLE001
        logging.exception("не удалось открыть %s", url)
        return False


def _which(name: str) -> bool:
    from shutil import which
    return which(name) is not None


def decode_output(raw) -> str:
    """Декодирует stdout/stderr внешней команды.

    На Linux почти всегда UTF-8. На Windows PowerShell-скрипты форсируют
    UTF-8, но cmd.exe и старые утилиты отдают OEM (cp866 на русской
    локали) или ANSI (cp1251) — пробуем по порядку.
    """
    if isinstance(raw, str):
        return raw
    if not raw:
        return ""
    encodings = ["utf-8"]
    if IS_WINDOWS:
        encodings += ["cp866", "cp1251"]
    for enc in encodings:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Файловые блокировки (flock есть не везде)
# ---------------------------------------------------------------------------

@contextmanager
def file_lock(lock_path: Path, exclusive: bool = True, blocking: bool = True):
    """Эквивалент flock(LOCK_EX/LOCK_NB) кроссплатформенно.

    POSIX — fcntl.flock, Windows — msvcrt.locking (блокирующая запись).
    На Windows блокировка межпроцессная для файлов, открытых в том же
    разделяемом режиме; вся наша логика (inbox, autonomy) укладывается
    в эти гарантии.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115 — управляется контекстом
    try:
        if IS_WINDOWS:
            import msvcrt
            flags = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            if blocking:
                # LK_LOCK ретраит ~10 секунд и потом падает — ловим.
                try:
                    msvcrt.locking(fh.fileno(), flags, 1)
                except OSError as e:
                    raise BlockingIOError(str(e)) from e
            else:
                msvcrt.locking(fh.fileno(), flags, 1)
        else:
            import fcntl
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            if not blocking:
                operation |= fcntl.LOCK_NB
            fcntl.flock(fh.fileno(), operation)
        yield fh
    finally:
        try:
            if IS_WINDOWS:
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


# ---------------------------------------------------------------------------
# Файловый триггер — «SIGUSR1» для Windows (работает и на Linux)
# ---------------------------------------------------------------------------

def fire_trigger():
    """Просит демон проснуться: пишет trigger-файл (+ SIGUSR1, если можно)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TRIGGER_FILE.write_text(str(time.time()), encoding="utf-8")
    if not IS_WINDOWS and (STATE_DIR / "jarvis.pid").exists():
        try:
            pid = int((STATE_DIR / "jarvis.pid").read_text().strip())
            import signal
            os.kill(pid, signal.SIGUSR1)
        except (ValueError, OSError, ImportError):
            pass


def consume_trigger() -> bool:
    """Демон: True, если есть свежий trigger-файл (и он забран)."""
    try:
        if not TRIGGER_FILE.exists():
            return False
        age = time.time() - TRIGGER_FILE.stat().st_mtime
        if age > TRIGGER_MAX_AGE:
            TRIGGER_FILE.unlink(missing_ok=True)
            return False
        TRIGGER_FILE.unlink(missing_ok=True)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Запись микрофона
# ---------------------------------------------------------------------------

def windows_mic_available() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import sounddevice  # noqa: F401
        return True
    except ImportError:
        return False


def record_windows_fixed(path: Path, sample_rate: int, seconds: float) -> bool:
    """Фиксированная запись через sounddevice (Windows fallback без VAD)."""
    import wave

    import numpy as np
    import sounddevice as sd
    try:
        frames = sd.rec(int(seconds * sample_rate), samplerate=sample_rate,
                        channels=1, dtype="int16")
        sd.wait()
        pcm = frames.reshape(-1).tobytes()
    except Exception as e:  # noqa: BLE001
        logging.error("запись микрофона не удалась: %s", e)
        return False
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return True


class WindowsMicStream:
    """Потоковый захват PCM s16le моно через sounddevice (аналог parecord --raw).

    read() возвращает ровно запрошенное число байтов либо b"" при закрытии.
    Зависший device не должен держить демон вечно — вызывающий код держит
    общий дедлайн записи.
    """

    def __init__(self, sample_rate: int):
        import sounddevice as sd
        self._stream = sd.RawInputStream(
            samplerate=sample_rate, channels=1, dtype="int16",
            blocksize=sample_rate // 100,  # ~10мс
        )
        self._stream.start()

    def read(self, size_bytes: int) -> bytes:
        import numpy as np
        needed = size_bytes // 2  # int16
        chunks = []
        got = 0
        while got < needed:
            data, overflowed = self._stream.read(needed - got)
            if overflowed:
                logging.debug("mic: overflow")
            raw = bytes(data)
            chunks.append(raw)
            got += len(raw) // 2
        return b"".join(chunks)[:size_bytes]

    def close(self):
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Мелочи, различающиеся по платформам
# ---------------------------------------------------------------------------

def commands_file_name() -> str:
    """Какой whitelist команд читать."""
    return "commands-win.json" if IS_WINDOWS else "commands.json"


def substitute_placeholders(command: str, base_dir: Path, state_dir: Path) -> str:
    """Подставляет {base}/{state} в статических командах из commands*.json.

    На Windows без этого не вытащить пути к scripts_win: $HOME/jarvis
    из Linux-версии туда не годится. .format() не используется намеренно —
    в командах встречаются фигурные скобки (awk и т.п.).
    """
    return (command
            .replace("{base}", str(base_dir))
            .replace("{state}", str(state_dir)))


def platform_send_to_window(window_id, text: str) -> bool:
    """Ввод текста в окно терминала (режим кодинга).

    Linux — xdotool. На Windows автоматизация конкретного окна по HWND
    через чистый stdlib ненадёжна (UIPI, разные архитектуры), поэтому
    режим кодинга здесь честно недоступен, а не «работает иногда».
    """
    if IS_WINDOWS:
        return False
    try:
        subprocess.run(
            ["xdotool", "getwindowname", str(window_id)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    import time as _time
    subprocess.run(["xdotool", "windowactivate", "--sync", str(window_id)],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _time.sleep(0.2)
    subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+Page_Down"],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _time.sleep(0.2)
    subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "10", text],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["xdotool", "key", "--clearmodifiers", "Return"],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def detect_wakeword_framework(model_path: Path) -> str:
    """openWakeWord: на Windows tflite-runtime не ставится — используем ONNX.

    Модель одинаковая, у репозитория openWakeWord лежат обе версии
    (.tflite и .onnx) с одинаковыми именами.
    """
    if str(model_path).endswith(".onnx"):
        return "onnx"
    if IS_WINDOWS:
        return "onnx"
    return "tflite"


def wakeword_model_on_windows(model_path: Path) -> Path:
    """На Windows тянем .onnx-вариант той же модели, если есть рядом."""
    if not IS_WINDOWS or model_path.suffix == ".onnx":
        return model_path
    onnx = model_path.with_suffix(".onnx")
    return onnx if onnx.exists() else model_path
