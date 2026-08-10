#!/usr/bin/env python3
"""
Jarvis Wake-word Listener
--------------------------
Резидентный процесс: слушает микрофон постоянно, при детекте слова
"Джарвис" (модель openWakeWord) шлёт SIGUSR1 в jarvis.py — то же самое,
что делает jarvis-trigger.sh по Super+J. Хоткей при этом продолжает
работать как есть, это параллельный, а не заменяющий способ триггера.

Ничего в основном контуре (jarvis.py, matching, commands.json) не
меняется: этот процесс умеет ровно одно действие — разбудить демон.

Использование:
  wakeword.py            запуск слушателя (резидент)
  wakeword.py --debug    подробные логи в stdout + печать score каждого окна

Требует:
  venv/bin/pip install openwakeword numpy
  и системную утилиту `arecord` (пакет alsa-utils — обычно уже стоит)
  и .tflite/.onnx модель в models/wakeword/<model_name>
  (см. README: обучение модели на "Джарвис" через Colab-ноутбук).

Захват звука сделан через дочерний процесс `arecord`, а не через
sounddevice/PortAudio: на связке Bluetooth HFP-гарнитура + PipeWire
PortAudio у части пользователей стабильно отдаёт полную цифровую
тишину (Max amplitude: 0), тогда как `arecord -D pulse` тот же самый
микрофон пишет корректно. Раз ALSA-путь через arecord уже подтверждённо
работает на этой машине, используем его напрямую вместо более
"нативного", но ненадёжного здесь sounddevice.
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

try:
    import numpy as np
    from openwakeword.model import Model as OWWModel
except ImportError:
    sys.exit(
        "Не найдены зависимости wake-word. Установи:\n"
        "  venv/bin/pip install openwakeword numpy\n"
        "И скачай/положи модель (см. README, раздел про openWakeWord)."
    )

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = Path.home() / ".local" / "share" / "jarvis"
JARVIS_PID_FILE = STATE_DIR / "jarvis.pid"
WAKEWORD_LOG_FILE = STATE_DIR / "wakeword.log"

DEFAULT_CONFIG = {
    # Имя файла модели в models/wakeword/, без пути. Обученная под
    # "Джарвис" модель кладётся туда установщиком/вручную.
    "model_path": "jarvis.tflite",
    "sample_rate": 16000,
    # Порог уверенности детекта, 0..1. С `trigger_frames` ниже
    # (3 подряд окна по 80мс = 240мс непрерывной уверенности) можно
    # держать порог низким 0.25-0.35 без ложных срабатываний.
    "threshold": 0.25,
    # Гистерезис: требуется N окон подряд выше порога для триггера.
    # Защищает от случайных пиков на чужой речи/шуме и стабилизирует
    # на естественной интонации, где пиковый score гуляет 0.3-0.6.
    "trigger_frames": 3,
    # Не даём слову сработать повторно раньше, чем через столько секунд —
    # защита от дребезга на одном произнесении и от повторного срабатывания
    # пока jarvis.py ещё обрабатывает предыдущую команду.
    "cooldown_seconds": 3.0,
    # Размер окна аудио, читаемого за раз (openWakeWord ожидает чанки
    # по 80мс при 16кГц, это 1280 сэмплов).
    "chunk_size": 1280,
    # ALSA-устройство для arecord. "pulse" маршрутизирует через
    # PulseAudio/PipeWire — тот же default source, что видит
    # `pactl get-default-source` (у тебя Bluetooth-гарнитура).
    "arecord_device": "pulse",
}

CONFIG_FILE = BASE_DIR / "wakeword_config.json"


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        import json
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def open_arecord(cfg: dict) -> subprocess.Popen:
    """Запускает arecord как дочерний процесс, пишущий сырой s16le PCM
    в stdout — без WAV-заголовка (raw), чтобы читать поток чанками
    напрямую, без парсинга контейнера."""
    cmd = [
        "arecord",
        "-D", cfg["arecord_device"],
        "-f", "S16_LE",
        "-r", str(cfg["sample_rate"]),
        "-c", "1",
        "-t", "raw",
        "-q",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def notify(title: str, body: str, urgency: str = "normal"):
    try:
        import subprocess
        subprocess.run(
            ["notify-send", "-u", urgency, "-a", "Jarvis", title, body],
            check=False,
            timeout=5,
        )
    except Exception:
        pass


def wake_jarvis():
    """Шлёт SIGUSR1 в jarvis.py — идентично действию jarvis-trigger.sh."""
    if not JARVIS_PID_FILE.exists():
        logging.warning("jarvis.pid не найден — демон jarvis.py не запущен?")
        notify("Jarvis", "Демон не запущен: systemctl --user status jarvis.service",
               urgency="critical")
        return False
    try:
        pid = int(JARVIS_PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGUSR1)
        logging.info("Разбудил jarvis.py (pid=%d) по wake-word", pid)
        return True
    except (ValueError, ProcessLookupError, PermissionError) as e:
        logging.warning("Не смог разбудить jarvis.py: %s", e)
        return False


def run_listener(cfg: dict, debug: bool):
    model_path = BASE_DIR / "models" / "wakeword" / cfg["model_path"]
    if not model_path.exists():
        sys.exit(
            f"Модель не найдена: {model_path}\n"
            "Обучи модель на слово 'Джарвис' (openWakeWord Colab-ноутбук, "
            "см. README) и положи .tflite/.onnx файл сюда, либо поправь "
            "model_path в wakeword_config.json."
        )

    logging.info("Загружаю модель %s", model_path)
    # openwakeword изменил API конструктора Model между версиями:
    # 0.4.x  -> Model(wakeword_model_paths=[...])                       (без inference_framework)
    # 0.6.x+ -> Model(wakeword_models=[...], inference_framework="tflite")
    # Пробуем новый API, откатываемся на старый при TypeError.
    try:
        oww = OWWModel(wakeword_models=[str(model_path)], inference_framework="tflite")
    except TypeError:
        oww = OWWModel(wakeword_model_paths=[str(model_path)])
    model_name = list(oww.models.keys())[0]

    last_trigger = 0.0
    chunk_size = cfg["chunk_size"]
    # 16 бит = 2 байта на сэмпл, моно.
    chunk_bytes = chunk_size * 2
    # Сколько окон подряд должны превысить порог перед триггером.
    trigger_frames = cfg.get("trigger_frames", 3)
    # Скользящее окно последних score (deque логичнее, но list+append быстрее).
    score_history: list[float] = []

    logging.info("Слушаю микрофон через arecord (device=%s), порог=%.2f, "
                 "кулдаун=%.1fс, триггер=%d кадра подряд",
                 cfg["arecord_device"], cfg["threshold"],
                 cfg["cooldown_seconds"], trigger_frames)
    notify("Jarvis", "Слушаю фоново: скажи «Джарвис» или нажми Super+J.")

    proc = open_arecord(cfg)
    try:
        while True:
            raw = proc.stdout.read(chunk_bytes)
            if not raw:
                # arecord умер (например, гарнитура отключилась/сменился
                # default source) — перезапускаем чтение через секунду
                # вместо падения всего процесса.
                logging.warning("arecord завершился неожиданно, перезапускаю через 1с")
                proc.wait()
                time.sleep(1.0)
                proc = open_arecord(cfg)
                continue
            if len(raw) < chunk_bytes:
                # Неполный чанк на старте потока — просто ждём следующего.
                continue

            audio = np.frombuffer(raw, dtype=np.int16)

            prediction = oww.predict(audio)
            score = prediction.get(model_name, 0.0)

            # Скользящее окно последних N score — нужно для гистерезиса.
            score_history.append(score)
            if len(score_history) > trigger_frames:
                score_history.pop(0)

            if debug:
                logging.debug("score=%.3f (window=%s)", score,
                              [f"{s:.2f}" for s in score_history])

            now = time.monotonic()
            # Триггер: последние N окон ВСЕ выше порога.
            ready = (len(score_history) >= trigger_frames
                     and all(s >= cfg["threshold"] for s in score_history)
                     and (now - last_trigger) >= cfg["cooldown_seconds"])
            if ready:
                last_trigger = now
                peak = max(score_history)
                logging.info("Wake-word обнаружено (score=%.3f, %d кадров)",
                             peak, trigger_frames)
                wake_jarvis()
                # После срабатывания промываем историю и буфер моделей,
                # чтобы хвост "Джарвис" не засчитался повторно.
                score_history.clear()
                oww.reset()
    finally:
        proc.terminate()


def main():
    parser = argparse.ArgumentParser(description="Jarvis wake-word listener")
    parser.add_argument("--debug", action="store_true",
                         help="подробные логи + печать score каждого окна")
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(WAKEWORD_LOG_FILE)]
    if args.debug:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )

    cfg = load_config()
    try:
        run_listener(cfg, args.debug)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
