#!/usr/bin/env python3
"""
Jarvis Control Core
--------------------
Голосовой контур управления системой:

  триггер (хоткей/сигнал) -> запись -> whisper.cpp (STT) -> сопоставление
  с белым списком команд -> выполнение -> Piper (TTS) -> ответ голосом

Ничего из распознанного текста не попадает в shell-команду напрямую —
голос только ВЫБИРАЕТ, какая из заранее описанных в commands.json команд
выполнится. Сама команда всегда statically задана в конфиге.

Использование:
  jarvis.py            запуск демона (ждёт SIGUSR1 от jarvis-trigger.sh)
  jarvis.py --once      разовый прогон в консоли, без демона — для отладки
  jarvis.py --debug     то же + подробные логи в stdout
"""

import argparse
import difflib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit(
        "Не найден пакет 'requests'. Установи зависимости:\n"
        "  venv/bin/pip install requests piper-tts[http]"
    )

try:
    import webrtcvad
except ImportError:
    # VAD опционален: без него запись падает обратно на фиксированную
    # длительность record_seconds, как раньше.
    webrtcvad = None

# LLM-парсер опционален. Модули импортируются лениво, чтобы jarvis.py
# мог стартовать без llm_client/llm_parser (например, в --once-режиме
# или когда venv неполный).
try:
    from llm_client import LLMClient, LLMError
    from llm_parser import parse_intent
    _llm_available = True
except ImportError:
    _llm_available = False

# Состояние диалога (фаза 2): импорт опциональный — без него jarvis.py
# продолжает работать в режиме "один wake-word = одна реплика".
try:
    import conversation_state as conv
    _conv_available = True
except ImportError:
    _conv_available = False

# Нормализация текста для TTS (цифры/размеры/даты по-русски). Опционален:
# без модуля speak() работает как раньше.
try:
    from tts_norm import normalize_for_tts
    _tts_norm_available = True
except ImportError:
    _tts_norm_available = False

# ---------------------------------------------------------------------------
# Пути и конфиг
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = Path.home() / ".local" / "share" / "jarvis"
PID_FILE = STATE_DIR / "jarvis.pid"
LOG_FILE = STATE_DIR / "jarvis.log"
REC_FILE = STATE_DIR / "command.wav"
REPLY_FILE = STATE_DIR / "reply.wav"
CODING_SESSION_FILE = STATE_DIR / "coding_session.json"

DEFAULT_CONFIG = {
    "whisper_url": "http://127.0.0.1:8081/inference",
    "piper_url": "http://127.0.0.1:5000/synthesize",
    "language": "ru",
    "sample_rate": 16000,
    "record_seconds": 4,
    "match_threshold": 0.6,
    "http_timeout": 30,
    # --- VAD (webrtcvad). Если пакета нет — фиксированные record_seconds. ---
    # Агрессивность отсечения не-речи, 0..3: выше — жёстче режет шум,
    # но может резать тихую речь.
    "vad_aggressiveness": 2,
    # Сколько ждать НАЧАЛА речи после сигнала, прежде чем сдаться.
    "vad_wait_speech_seconds": 4.0,
    # Сколько тишины после речи считается концом фразы.
    "vad_silence_seconds": 0.9,
    # Потолок записи с VAD — страховка от бесконечной записи на шумном фоне.
    "max_record_seconds": 12.0,
    # Короткий сигнал «говори» после триггера (пустая строка — без сигнала).
    "beep_command": "paplay /usr/share/sounds/freedesktop/stereo/message.oga",
    # --- LLM-парсер (OmniRoute, локальный OpenAI-совместимый endpoint) ---
    "llm": {
        # Главный выключатель. Если false — сразу fallback на match_command.
        "enabled": True,
        "base_url": "http://localhost:20128/v1",
        # Имя env-переменной, где лежит API-ключ. Если env не задан —
        # fallback на match_command (без падений).
        "api_key_env": "OMNIROUTE_API_KEY",
        # Имя модели — пустое по умолчанию, заполняется в config.json.
        "model": "",
        "max_tokens": 300,
        "timeout_seconds": 8,
        "max_retries": 1,
        # Если True — любая ошибка LLM валится на match_command.
        "fallback_to_match": True,
    },
}


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(BASE_DIR / "config.json", {}) or {})
    return cfg


def load_commands() -> list:
    data = load_json(BASE_DIR / "commands.json", {"commands": []})
    return data.get("commands", [])


# ---------------------------------------------------------------------------
# Мелкие утилиты
# ---------------------------------------------------------------------------


def notify(title: str, body: str, urgency: str = "normal"):
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-a", "Jarvis", title, body],
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def beep(cfg: dict):
    """Короткий сигнал «говори» — чтобы не начинать фразу раньше записи."""
    beep_cmd = cfg.get("beep_command", "")
    if not beep_cmd:
        return
    try:
        subprocess.run(beep_cmd, shell=True, check=False, timeout=3,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        pass


def write_wav(path: Path, pcm: bytes, sample_rate: int):
    """Оборачивает сырой s16le-моно PCM в WAV-контейнер."""
    import wave
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def load_coding_session() -> dict:
    """Загружает состояние сессии кодинга."""
    if not CODING_SESSION_FILE.exists():
        return {"active": False, "window_id": None}
    try:
        import json
        return json.loads(CODING_SESSION_FILE.read_text())
    except Exception:
        return {"active": False, "window_id": None}


def save_coding_session(active: bool, window_id: int = None):
    """Сохраняет состояние сессии кодинга."""
    import json
    CODING_SESSION_FILE.write_text(json.dumps({"active": active, "window_id": window_id}))


def clear_coding_session():
    """Очищает состояние сессии кодинга."""
    if CODING_SESSION_FILE.exists():
        CODING_SESSION_FILE.unlink()


def send_to_claude_window(window_id: int, text: str) -> bool:
    """Отправляет текст в окно терминала с claude."""
    try:
        # Проверяем, что окно существует
        subprocess.run(
            ["xdotool", "getwindowname", str(window_id)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        return False

    # Активируем окно
    subprocess.run(
        ["xdotool", "windowactivate", "--sync", str(window_id)],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(0.2)

    # Переключаемся на вкладку claude (ctrl+Page_Down)
    subprocess.run(
        ["xdotool", "key", "--clearmodifiers", "ctrl+Page_Down"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(0.2)

    # Вводим текст
    subprocess.run(
        ["xdotool", "type", "--clearmodifiers", "--delay", "10", text],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # Enter
    subprocess.run(
        ["xdotool", "key", "--clearmodifiers", "Return"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return True


def record_audio_fixed(cfg: dict, path: Path):
    """Старый способ: фиксированные record_seconds. Используется как
    fallback, когда webrtcvad не установлен."""
    cmd = [
        "parecord",
        f"--rate={cfg['sample_rate']}",
        "--channels=1",
        "--format=s16le",
        "--file-format=wav",
        str(path),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=cfg["record_seconds"])
    except subprocess.TimeoutExpired:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def record_audio_vad(cfg: dict, path: Path) -> bool:
    """
    Запись с VAD: ждём начала речи, пишем до vad_silence_seconds тишины
    после неё, но дольше max_record_seconds. Возвращает False, если
    речь так и не началась за vad_wait_speech_seconds.

    PCM читается напрямую из stdout parecord (--raw), кадры по 30мс
    прогоняются через webrtcvad. В файл попадает всё с небольшим
    преролом до первого речевого кадра, чтобы не срезать начало слова.
    """
    rate = cfg["sample_rate"]
    frame_ms = 30
    frame_bytes = rate * frame_ms // 1000 * 2  # s16le mono
    vad = webrtcvad.Vad(cfg["vad_aggressiveness"])

    wait_frames = int(cfg["vad_wait_speech_seconds"] * 1000 / frame_ms)
    silence_frames_limit = int(cfg["vad_silence_seconds"] * 1000 / frame_ms)
    max_frames = int(cfg["max_record_seconds"] * 1000 / frame_ms)
    preroll_frames = 10  # ~300мс до начала речи

    cmd = [
        "parecord",
        f"--rate={rate}",
        "--channels=1",
        "--format=s16le",
        "--raw",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    from collections import deque
    preroll = deque(maxlen=preroll_frames)
    voiced = bytearray()
    speech_started = False
    silence_run = 0
    frames_total = 0

    try:
        while frames_total < max_frames:
            frame = proc.stdout.read(frame_bytes)
            if not frame or len(frame) < frame_bytes:
                logging.warning("parecord оборвался во время записи")
                break
            frames_total += 1
            is_speech = vad.is_speech(frame, rate)

            if not speech_started:
                preroll.append(frame)
                if is_speech:
                    speech_started = True
                    voiced.extend(b"".join(preroll))
                elif frames_total >= wait_frames:
                    break  # речь так и не началась
            else:
                voiced.extend(frame)
                silence_run = 0 if is_speech else silence_run + 1
                if silence_run >= silence_frames_limit:
                    break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not speech_started:
        return False
    write_wav(path, bytes(voiced), rate)
    return True


def record_audio(cfg: dict, path: Path) -> bool:
    """Возвращает True, если что-то записано (при VAD — если была речь)."""
    notify("Jarvis", "Слушаю…")
    beep(cfg)
    if webrtcvad is None:
        record_audio_fixed(cfg, path)
        return True
    return record_audio_vad(cfg, path)


def transcribe(cfg: dict, path: Path) -> str:
    with open(path, "rb") as f:
        files = {"file": (path.name, f, "audio/wav")}
        data = {
            "language": cfg["language"],
            "response_format": "json",
            "no_speech_thold": "0.6",
        }
        resp = requests.post(
            cfg["whisper_url"], files=files, data=data, timeout=cfg["http_timeout"]
        )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


def speak(cfg: dict, text: str):
    if not text:
        return
    # Цифры, размеры (3.6Gi), даты и проценты переводим в членораздельную речь.
    if _tts_norm_available:
        try:
            text = normalize_for_tts(text)
        except Exception as e:
            logging.warning("tts_norm failed: %s", e)
    try:
        resp = requests.post(
            cfg["piper_url"], json={"text": text}, timeout=cfg["http_timeout"]
        )
        resp.raise_for_status()
        REPLY_FILE.write_bytes(resp.content)
        subprocess.run(["paplay", str(REPLY_FILE)], check=False, timeout=30)
    except requests.exceptions.RequestException as e:
        logging.error("TTS недоступен: %s", e)
        notify("Jarvis", "TTS-сервер не отвечает", urgency="critical")
    except subprocess.TimeoutExpired:
        logging.error("paplay завис при воспроизведении ответа")


# ---------------------------------------------------------------------------
# Сопоставление и выполнение команд
# ---------------------------------------------------------------------------


def match_command(text: str, commands: list, threshold: float):
    """
    Сопоставляет распознанный текст с белым списком.

    Среди всех фраз, целиком встретившихся внутри сказанного, выбирается
    САМАЯ ДЛИННАЯ/специфичная, а не первая попавшаяся по порядку в
    commands.json — иначе общая фраза одной команды может "проглотить"
    более специфичную фразу другой (например "есть обновления" внутри
    "есть обновления из аур" перебивало бы аур-специфичную команду).

    Если полного совпадения нет — fuzzy-скор, но только если в сказанном
    НЕ МЕНЬШЕ слов, чем в целевой фразе (отсекает обрубленные команды
    без объекта, вроде голого "перезагрузи").

    cmd["min_score"] — свой (обычно повышенный) порог для конкретной
    команды поверх общего match_threshold.
    """
    text_norm = text.lower().strip()
    if not text_norm:
        return None, 0.0
    text_word_count = len(text_norm.split())

    exact_cmd, exact_len = None, -1
    fuzzy_cmd, fuzzy_score = None, 0.0

    for cmd in commands:
        cmd_threshold = cmd.get("min_score", threshold)
        for phrase in cmd.get("phrases", []):
            phrase_norm = phrase.lower().strip()
            if not phrase_norm:
                continue

            if phrase_norm in text_norm:
                if len(phrase_norm) > exact_len:
                    exact_len, exact_cmd = len(phrase_norm), cmd
                continue

            if text_word_count < len(phrase_norm.split()):
                # сказано меньше слов, чем в целевой фразе — похоже на
                # обрубленную команду без объекта, не считаем совпадением
                continue

            score = difflib.SequenceMatcher(None, text_norm, phrase_norm).ratio()
            if score >= cmd_threshold and score > fuzzy_score:
                fuzzy_score, fuzzy_cmd = score, cmd

    if exact_cmd is not None:
        return exact_cmd, 1.0
    if fuzzy_cmd is not None:
        return fuzzy_cmd, fuzzy_score
    return None, fuzzy_score


def run_background(cfg: dict, cmd: dict):
    proc = subprocess.Popen(
        cmd["command"], shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    def watcher():
        proc.wait()
        msg = cmd.get("done_message", f"{cmd.get('id', 'команда')}: готово.")
        notify("Jarvis", msg)
        speak(cfg, msg)

    threading.Thread(target=watcher, daemon=True).start()


def execute_foreground(cmd: dict) -> str:
    try:
        result = subprocess.run(
            cmd["command"],
            shell=True,
            capture_output=True,
            text=True,
            timeout=cmd.get("timeout", 15),
        )
        return (result.stdout or result.stderr or "").strip()
    except subprocess.TimeoutExpired:
        logging.error("команда %s не уложилась в таймаут", cmd.get("id"))
        return ""
    except Exception:
        logging.exception("сбой при выполнении команды %s", cmd.get("id"))
        return ""


CONFIRM_YES = {"да", "да да", "давай", "точно", "подтверждаю", "выполняй", "ага", "угу"}


def ask_confirmation(cfg: dict, cmd: dict) -> bool:
    """
    Голосовое подтверждение для команд с confirm: true.

    Джарвис переспрашивает, записывает второй ответ и выполняет только
    если в нём прозвучало «да»/«подтверждаю» и т.п. Любой другой ответ,
    тишина или недоступность STT трактуются как отказ — сомнение всегда
    в пользу НЕвыполнения.
    """
    question = cmd.get("confirm_prompt") or f"Точно {cmd.get('id')}? Скажи да."
    speak(cfg, question)

    if not record_audio(cfg, REC_FILE):
        speak(cfg, "Не услышал подтверждения, отменяю.")
        return False
    try:
        answer = transcribe(cfg, REC_FILE)
    except requests.exceptions.RequestException as e:
        logging.error("STT недоступен при подтверждении: %s", e)
        speak(cfg, "Не могу распознать ответ, отменяю.")
        return False

    answer_norm = answer.lower().strip().strip(".,!?…")
    logging.info("Ответ на подтверждение: %r", answer)
    if answer_norm in CONFIRM_YES or answer_norm.startswith("да"):
        return True
    speak(cfg, "Отменяю.")
    return False


def handle_command(cfg: dict, cmd: dict, score: float):
    logging.info("Выполняю: %s (score=%.2f)", cmd.get("id"), score)

    if cmd.get("confirm") and not ask_confirmation(cfg, cmd):
        logging.info("Команда %s не подтверждена, отмена", cmd.get("id"))
        notify("Jarvis", f"Отменено: {cmd.get('id')}")
        return

    notify("Jarvis", f"Выполняю: {cmd.get('id')}")

    if cmd.get("background"):
        speak(cfg, cmd.get("response", "Запускаю в фоне."))
        run_background(cfg, cmd)
        return

    if cmd.get("speak_before"):
        speak(cfg, cmd.get("response", ""))
        subprocess.Popen(cmd["command"], shell=True,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    output = execute_foreground(cmd)
    response = cmd.get("response", "")
    if cmd.get("speak_output") and output:
        text = f"{response} {output}".strip()
    else:
        text = response or "Готово."
    speak(cfg, text)

    # Dictation mode: после команды делаем вторую запись и передаём текст в скрипт
    if cmd.get("dictation"):
        handle_dictation(cfg, cmd)


def handle_dictation(cfg: dict, cmd: dict):
    """Вторая запись для dictation-команд (передача текста в скрипт)."""
    dictation_script = cmd.get("dictation_script")
    if not dictation_script:
        logging.warning("dictation: true, но dictation_script не задан")
        return

    speak(cfg, cmd.get("dictation_prompt", "Слушаю задачу."))

    if not record_audio(cfg, REC_FILE):
        logging.info("Dictation: речь не началась")
        speak(cfg, "Не услышал задачу.")
        return

    try:
        text = transcribe(cfg, REC_FILE)
    except requests.exceptions.RequestException as e:
        logging.error("Dictation STT недоступен: %s", e)
        speak(cfg, "Не могу распознать речь.")
        return

    logging.info("Dictation услышал: %r", text)
    if not text:
        speak(cfg, "Пусто, отменяю.")
        return

    # Запускаем скрипт с текстом как аргументом
    try:
        subprocess.run(
            [dictation_script, text],
            shell=False,
            timeout=cmd.get("dictation_timeout", 10),
        )
    except subprocess.TimeoutExpired:
        logging.error("Dictation script timeout")
        speak(cfg, "Скрипт завис.")
    except Exception as e:
        logging.exception("Dictation script error: %s", e)
        speak(cfg, "Ошибка выполнения.")


# ---------------------------------------------------------------------------
# Основной цикл
# ---------------------------------------------------------------------------


def handle_voice_command(cfg: dict) -> bool:
    # Проверяем, активна ли сессия кодинга
    session = load_coding_session()
    if session.get("active") and session.get("window_id"):
        # Режим кодинга: всё голосовое отправляем в claude
        handle_coding_dictation(cfg, session["window_id"])
        return False

    if not record_audio(cfg, REC_FILE):
        logging.info("VAD: речь не началась, отмена")
        notify("Jarvis", "Тишина — отменяю.")
        return False
    notify("Jarvis", "Распознаю…")

    try:
        text = transcribe(cfg, REC_FILE)
    except requests.exceptions.RequestException as e:
        logging.error("STT недоступен: %s", e)
        notify("Jarvis", "STT-сервер не отвечает", urgency="critical")
        speak(cfg, "Не могу связаться с распознаванием речи.")
        return False

    logging.info("Услышал: %r", text)
    if not text:
        speak(cfg, "Не расслышал, повтори.")
        return False

    # Сначала пробуем LLM-маршрут.
    action_type = try_llm_route(cfg, text)
    if action_type:
        return action_type in ("speak", "ask")

    commands = load_commands()
    cmd, score = match_command(text, commands, cfg["match_threshold"])

    if cmd is None:
        logging.info("Нет совпадений (best score=%.2f) для %r", score, text)
        notify("Jarvis", f"Не понял: {text}")
        speak(cfg, "Такой команды не знаю.")
        return False

    handle_command(cfg, cmd, score)
    return False


def try_llm_route(cfg: dict, text: str) -> str | None:
    """Пробует распарсить текст через LLM. Возвращает тип action или None, если fallback.

    Безопасно: любые ошибки ловятся, ничего не падает.

    При успешном LLM-маршруте также обновляет диалоговое состояние:
    push_turn для user, при action=ask — set_pending.
    """
    llm_cfg = cfg.get("llm") or {}
    if not (llm_cfg.get("enabled") and _llm_available):
        return None
    model = llm_cfg.get("model", "")
    if not model:
        logging.debug("LLM: model не задан в config.json, fallback")
        return None
    api_key_env = llm_cfg.get("api_key_env", "OMNIROUTE_API_KEY")
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        logging.debug("LLM: env %s не задан, fallback", api_key_env)
        return None

    client = LLMClient(
        base_url=llm_cfg["base_url"],
        api_key=api_key,
        model=model,
        timeout=llm_cfg.get("timeout_seconds", 8),
        max_retries=llm_cfg.get("max_retries", 1),
    )

    # Контекст диалога (если доступен)
    history_tail = []
    pending = None
    if _conv_available:
        state = conv.load()
        history_tail = state.get("history_tail", [])
        pending = state.get("pending_action")

    try:
        commands = load_commands()
        action = parse_intent(
            client, text, commands,
            history_tail=history_tail,
            pending=pending,
        )
    except LLMError as e:
        if llm_cfg.get("fallback_to_match", True):
            logging.warning("LLM parse failed, fallback: %s", e)
            return None
        logging.error("LLM parse failed (no fallback): %s", e)
        speak(cfg, "Не удалось связаться с мозгом.")
        return "speak"

    logging.info("LLM action: %s", action)

    # Обновляем диалоговое состояние
    if _conv_available:
        ttl = float(llm_cfg.get("conversation_window_seconds", 30))
        conv.push_turn("user", text, ttl_seconds=ttl)
        if action["action"] == "speak":
            conv.push_turn("assistant", action["text"], ttl_seconds=ttl)
        elif action["action"] == "ask":
            conv.push_turn("assistant", action["text"], ttl_seconds=ttl)
            conv.set_pending(topic=text, question=action["text"],
                             ttl_seconds=ttl)
        elif action["action"] == "command":
            # после команды диалог затухает — закрываем pending
            conv.clear()

    if action["action"] == "command":
        cmd = next((c for c in commands if c.get("id") == action["id"]), None)
        if cmd is None:
            logging.error("LLM вернул несуществующий id %s — whitelist-баг",
                           action["id"])
            return None
        # Принудительная установка confirm, если LLM потребовал
        if action.get("needs_confirmation") and not cmd.get("confirm"):
            cmd = dict(cmd)
            cmd["confirm"] = True
        handle_command(cfg, cmd, 1.0)
        return "command"

    if action["action"] in ("speak", "ask"):
        speak(cfg, action["text"])
        return action["action"]

    # Неизвестный action — fallback
    logging.warning("LLM вернул неизвестный action: %r", action)
    return None


def handle_coding_dictation(cfg: dict, window_id: int):
    """Обработка голосового ввода в режиме кодинга — отправляет в claude."""
    if not record_audio(cfg, REC_FILE):
        logging.info("Coding dictation: речь не началась")
        notify("Jarvis", "Тишина — отменяю.")
        return
    notify("Jarvis", "Слушаю задачу…")

    try:
        text = transcribe(cfg, REC_FILE)
    except requests.exceptions.RequestException as e:
        logging.error("Coding STT недоступен: %s", e)
        notify("Jarvis", "STT-сервер не отвечает", urgency="critical")
        speak(cfg, "Не могу распознать речь.")
        return

    logging.info("Coding услышал: %r", text)
    if not text:
        speak(cfg, "Не расслышал, повтори.")
        return

    # Проверяем команды выхода из режима кодинга
    text_lower = text.lower().strip()
    if text_lower in {"стоп кодинг", "стоп кодить", "выход из кодинга", "завершить кодинг", "хватит кодить"}:
        clear_coding_session()
        speak(cfg, "Сессия кодинга завершена.")
        notify("Режим кодинга выключен.")
        return

    # Отправляем в claude
    if send_to_claude_window(window_id, text):
        logging.info("Отправлено в claude: %s", text)
        # Можно озвучить подтверждение, но лучше тихо для плавности
    else:
        logging.warning("Окно claude недоступно, выключаю режим кодинга")
        clear_coding_session()
        speak(cfg, "Окно терминала потеряно, сессия кодинга завершена.")
        notify("Терминал не найден, режим кодинга выключен.", urgency="normal")


trigger_event = threading.Event()
processing_lock = threading.Lock()

INBOX_FILE = STATE_DIR / "inbox.json"
INBOX_CHECK_INTERVAL = 120  # секунд между проверками inbox


def drain_inbox(cfg: dict) -> int:
    """Забирает записи из inbox.json (от autonomy.py) и озвучивает.

    Возвращает количество обработанных записей. Файл удаляется
    только если удалось прочитать и распарсить — никакой потери.
    """
    if not INBOX_FILE.exists():
        return 0
    try:
        data = json.loads(INBOX_FILE.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("inbox: битый файл, не трогаю")
        return 0
    if not isinstance(data, list) or not data:
        return 0
    # Берём первую запись, остальные оставляем
    item = data[0]
    text = (item.get("text") or "").strip()
    if not text:
        # пустые пропускаем — перезаписываем файл с пустым списком
        try:
            INBOX_FILE.write_text(json.dumps(data[1:], ensure_ascii=False),
                                   encoding="utf-8")
        except Exception:
            pass
        return 0
    src = item.get("source", "autonomy")
    logging.info("inbox: сообщение от %s", src)
    speak(cfg, text)
    # атомарно обновим файл — убираем обработанную запись
    try:
        tmp = INBOX_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data[1:], ensure_ascii=False),
                        encoding="utf-8")
        tmp.replace(INBOX_FILE)
    except Exception:
        logging.exception("inbox: не удалось обновить файл")
    return 1


def on_signal(signum, frame):
    if processing_lock.locked():
        return
    trigger_event.set()


def run_daemon(cfg: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    signal.signal(signal.SIGUSR1, on_signal)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    logging.info("Демон запущен, pid=%d", os.getpid())
    notify("Jarvis", "Ядро управления запущено.")

    last_inbox_check = 0.0
    try:
        while True:
            # Ждём wake-word с таймаутом, чтобы периодически проверять inbox
            triggered = trigger_event.wait(timeout=INBOX_CHECK_INTERVAL)
            if triggered:
                trigger_event.clear()
                if not processing_lock.acquire(blocking=False):
                    continue
                try:
                    while True:
                        if not handle_voice_command(cfg):
                            break
                except Exception:
                    logging.exception("Необработанная ошибка в цикле обработки")
                finally:
                    processing_lock.release()
                # обработка могла занять время — сбросить счётчик
                last_inbox_check = time.monotonic()
            else:
                # Таймаут — пора проверить inbox от autonomy.py
                now = time.monotonic()
                if now - last_inbox_check >= INBOX_CHECK_INTERVAL:
                    last_inbox_check = now
                    if not processing_lock.acquire(blocking=False):
                        continue
                    try:
                        drain_inbox(cfg)
                    except Exception:
                        logging.exception("inbox: ошибка при обработке")
                    finally:
                        processing_lock.release()
    finally:
        PID_FILE.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Jarvis control core")
    parser.add_argument("--once", action="store_true",
                         help="разовый прогон в консоли, без демона")
    parser.add_argument("--debug", action="store_true",
                         help="подробные логи в stdout")
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(LOG_FILE)]
    if args.once or args.debug:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )

    cfg = load_config()

    if args.once:
        handle_voice_command(cfg)
        return

    run_daemon(cfg)


if __name__ == "__main__":
    main()
