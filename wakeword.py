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
  и .tflite/.onnx модель в models/wakeword/<model_name>
  (см. README: обучение модели на "Джарвис" через Colab-ноутбук).

Захват звука:
  Linux — дочерний процесс `arecord`, а не sounddevice/PortAudio: на связке
  Bluetooth HFP-гарнитура + PipeWire PortAudio у части пользователей
  стабильно отдаёт полную цифровую тишину (Max amplitude: 0), тогда как
  `arecord -D pulse` тот же самый микрофон пишет корректно.
  Windows — arecord не существует, там поток идёт через sounddevice
  (PortAudio) — на этой платформе альтернатив нет.

Пробуждение демона: на Linux — SIGUSR1 (как раньше), на Windows —
запись trigger-файла (platform_support.fire_trigger, работает везде).
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import platform_support as plat

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
    напрямую, без парсинга контейнера. Linux-only."""
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


def open_mic(cfg: dict):
    """Открытый источник потокового PCM s16le моно. Linux — arecord (Popen),
    Windows — sounddevice-стрим с интерфейсом .read(n)/.close()."""
    if plat.IS_WINDOWS:
        return plat.WindowsMicStream(cfg["sample_rate"])
    return open_arecord(cfg)


def notify(title: str, body: str, urgency: str = "normal"):
    plat.notify(title, body, urgency)


def wake_jarvis():
    """Разбудить jarvis.py: SIGUSR1 на Linux, trigger-файл — везде."""
    plat.fire_trigger()
    logging.info("Разбудил jarvis.py (windows=%s)", plat.IS_WINDOWS)
    if plat.IS_WINDOWS and not (STATE_DIR / "jarvis.pid").exists():
        notify("Jarvis", "Демон не запущен: install-win.ps1 / jarvis.py",
               urgency="critical")
        return False
    return True


def run_listener(cfg: dict, debug: bool):
    model_path = BASE_DIR / "models" / "wakeword" / cfg["model_path"]
    model_path = plat.wakeword_model_on_windows(model_path)
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
    # На Windows всегда onnx: tflite-runtime там не ставится (Linux-only).
    framework = plat.detect_wakeword_framework(model_path)
    try:
        oww = OWWModel(wakeword_models=[str(model_path)],
                       inference_framework=framework)
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

    logging.info("Слушаю микрофон (%s), порог=%.2f, "
                 "кулдаун=%.1fс, триггер=%d кадра подряд",
                 ("sounddevice" if plat.IS_WINDOWS else f"arecord {cfg['arecord_device']}"),
                 cfg["threshold"],
                 cfg["cooldown_seconds"], trigger_frames)
    notify("Jarvis", "Слушаю фоново: скажи «Джарвис» или нажми Super+J.")

    source = open_mic(cfg)
    try:
        while True:
            raw = source.read(chunk_bytes)
            if not raw:
                # Поток умер (гарнитура отключилась / сменился default
                # source / сбой устройства) — перезапускаем через секунду
                # вместо падения всего процесса.
                logging.warning("микрофон завершился неожиданно, перезапускаю через 1с")
                if isinstance(source, subprocess.Popen):
                    source.wait()
                time.sleep(1.0)
                source = open_mic(cfg)
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
        if isinstance(source, subprocess.Popen):
            source.terminate()
        else:
            source.close()


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
