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
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import inbox_store
from recognition import SpeechBuffer, command_text, has_negation, normalize_text, phrase_spans

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

# Веб-поиск (ddgs/DuckDuckGo). Опционален: без него action=search говорит
# «модуль поиска не установлен».
try:
    import web_search
    _web_search_available = True
except ImportError:
    _web_search_available = False

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
    "match_threshold": 0.72,
    "match_ambiguity_margin": 0.06,
    "vad_start_speech_seconds": 0.09,
    "vad_min_speech_seconds": 0.18,
    "stt_prompt": "",
    "stt_no_speech_threshold": 0.6,
    # Быстрый путь без LLM: если слов вне найденных точных фраз не больше
    # этого числа — команды исполняются локально и мгновенно.
    "exact_max_extra_words": 6,
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
    после неё, но не дольше max_record_seconds. Возвращает False, если
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

    buffer = SpeechBuffer(
        start_frames=math.ceil(cfg.get("vad_start_speech_seconds", 0.09) * 1000 / frame_ms),
        min_frames=math.ceil(cfg.get("vad_min_speech_seconds", 0.18) * 1000 / frame_ms),
        silence_frames=silence_frames_limit,
        preroll_frames=preroll_frames,
    )
    frames_total = 0
    pending = bytearray()
    # A stalled audio device must not hold the daemon's processing lock forever.
    deadline = time.monotonic() + cfg["max_record_seconds"]
    selector = selectors.DefaultSelector()
    try:
        selector.register(proc.stdout, selectors.EVENT_READ)
        while frames_total < max_frames:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(timeout=min(remaining, 1.0)):
                break
            chunk = os.read(proc.stdout.fileno(), frame_bytes - len(pending))
            if not chunk:
                break
            pending.extend(chunk)
            if len(pending) < frame_bytes:
                continue
            frame = bytes(pending)
            pending.clear()
            frames_total += 1
            if buffer.feed(frame, vad.is_speech(frame, rate)):
                break
            if not buffer.started and frames_total >= wait_frames:
                break
    finally:
        selector.close()
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()

    if not buffer.valid:
        return False
    write_wav(path, bytes(buffer.audio), rate)
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
            "no_speech_thold": str(cfg.get("stt_no_speech_threshold", 0.6)),
            "temperature": "0.0",
            "temperature_inc": "0.0",
        }
        if cfg.get("stt_prompt"):
            data["prompt"] = cfg["stt_prompt"][:500]
        resp = requests.post(
            cfg["whisper_url"], files=files, data=data, timeout=cfg["http_timeout"]
        )
    resp.raise_for_status()
    text = resp.json().get("text", "")
    if not isinstance(text, str):
        return ""
    # Common non-speech captions; never pass them to command matching.
    if normalize_text(text) in {"музыка", "аплодисменты", "тишина", "субтитры"}:
        return ""
    return text.strip()


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


def match_command(text: str, commands: list, threshold: float,
                  ambiguity_margin: float = 0.06):
    """Boundary-aware exact matching, then conservative per-command fuzzy ranking.

    Similar runner-up = no execution. Dangerous commands are exact-only and
    still require confirmation at execution time. Negations go to the LLM.
    """
    text_norm = command_text(text)
    if not text_norm or has_negation(text_norm):
        return None, 0.0
    exact = []
    ranked = []
    for cmd in commands:
        best = 0.0
        for phrase in cmd.get("phrases", []):
            phrase_norm = normalize_text(phrase)
            if not phrase_norm:
                continue
            if any(phrase_spans(text_norm, phrase_norm)):
                exact.append((len(phrase_norm), cmd))
                continue
            if cmd.get("confirm") or "dangerous" in cmd.get("tags", []):
                continue
            if len(text_norm.split()) < len(phrase_norm.split()):
                continue
            best = max(best, difflib.SequenceMatcher(
                None, text_norm, phrase_norm, autojunk=False).ratio())
        ranked.append((best, cmd))
    if exact:
        exact.sort(key=lambda pair: pair[0], reverse=True)
        tied = {c.get("id") for length, c in exact if length == exact[0][0]}
        return (exact[0][1], 1.0) if len(tied) == 1 else (None, 1.0)
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    if not ranked:
        return None, 0.0
    score, cmd = ranked[0]
    runner_up = next((v for v, c in ranked[1:] if c.get("id") != cmd.get("id")), 0)
    if score < cmd.get("min_score", threshold) or score - runner_up < ambiguity_margin:
        return None, score
    return cmd, score


def match_commands_exact(text: str, commands: list, max_extra_words: int) -> list:
    """
    Все команды с точными вхождениями фраз в текст — для быстрого пути
    без хода в LLM (главный выигрыш по задержке ответа).

    Выбираются непересекающиеся вхождения (жадно от самой длинной фразы,
    чтобы специфичная не глоталась общей), дедуп по id — одна команда один
    запуск. Порядок результата — по позиции в тексте, как произнёс пользователь.

    Защита от «проглатывания»: если слов вне найденных фраз больше
    max_extra_words, реплика считается не-командной (болтовня, где случайно
    мелькнула команда) и возвращается пустой список — её разберёт LLM.
    """
    text_norm = normalize_text(text)
    if not text_norm or has_negation(text_norm):
        return []

    candidates = []  # (start, end, длина фразы, cmd)
    for cmd in commands:
        for phrase in cmd.get("phrases", []):
            phrase_norm = normalize_text(phrase)
            if not phrase_norm:
                continue
            for start, end in phrase_spans(text_norm, phrase_norm):
                candidates.append((start, end, len(phrase_norm), cmd))

    if not candidates:
        return []

    # The same phrase assigned to different IDs is ambiguous, not file order.
    spans = {}
    for start, end, _, cmd in candidates:
        spans.setdefault((start, end), set()).add(cmd.get("id"))
    if any(len(ids) > 1 for ids in spans.values()):
        return []

    # жадный выбор непересекающихся вхождений, от самой длинной фразы
    candidates.sort(key=lambda c: (-c[2], c[0]))
    chosen = []  # (start, end, cmd)
    for s, e, _ln, cmd in candidates:
        if any(not (e <= cs or s >= ce) for cs, ce, _ in chosen):
            continue
        chosen.append((s, e, cmd))

    # дедуп по id, порядок по позиции в тексте
    seen_ids = set()
    picked = []
    for s, e, cmd in sorted(chosen, key=lambda c: c[0]):
        cid = cmd.get("id")
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        picked.append((s, e, cmd))

    # лимит «лишних» слов вне командных фраз
    words_total = len(text_norm.split())
    matched_words = sum(len(text_norm[s:e].split()) for s, e, _ in chosen)
    extra = words_total - matched_words
    if extra > max_extra_words:
        logging.debug("exact fast-path: %d лишних слов > %d — передаю в LLM",
                      extra, max_extra_words)
        return []

    # Never swallow a second, unrecognized request after one exact command.
    remainder = list(text_norm)
    for start, end, _ in chosen:
        remainder[start:end] = " " * (end - start)
    fillers = {"джарвис", "jarvis", "пожалуйста", "а", "и", "потом", "затем",
               "скажи", "ну", "будет"}
    if set("".join(remainder).split()) - fillers or len(picked) > 4:
        return []
    return [cmd for _, _, cmd in picked]


def match_commands_local(text: str, commands: list, threshold: float,
                         ambiguity_margin: float = 0.06) -> list:
    """Offline fuzzy multi-command plan. Resolve ALL clauses before executing any."""
    if has_negation(text):
        return []
    clauses = re.split(r"\b(?:и затем|и потом|а потом|а затем|затем|потом|и)\b",
                       normalize_text(text))
    if len(clauses) > 4 or any(not clause.strip() for clause in clauses):
        return []
    plan = []
    seen = set()
    for clause in clauses:
        cmd, score = match_command(clause, commands, threshold, ambiguity_margin)
        if cmd is None:
            return []
        if score == 1.0 and not match_commands_exact(clause, commands, 6):
            return []
        if cmd.get("id") not in seen:
            seen.add(cmd.get("id"))
            plan.append((cmd, score))
    return plan


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

    answer_norm = normalize_text(answer)
    logging.info("Ответ на подтверждение: %r", answer)
    if answer_norm in {normalize_text(x) for x in CONFIRM_YES}:
        return True
    speak(cfg, "Отменяю.")
    return False


def handle_command(cfg: dict, cmd: dict, score: float):
    logging.info("Выполняю: %s (score=%.2f)", cmd.get("id"), score)

    if (cmd.get("confirm") or "dangerous" in cmd.get("tags", [])) and not ask_confirmation(cfg, cmd):
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


def handle_search(cfg: dict, query: str, open_browser: bool,
                  client=None) -> tuple[str, str]:
    """
    Веб-поиск (action=search). open_browser=true — открыть страницу
    результатов в браузере; иначе — суммаризировать выдачу LLM и сказать
    голосом (fallback при сбое суммаризации — заголовки сниппетов).

    Возвращает (тип_для_маршрута, озвученный_текст): "speak" продолжает
    диалог, "command" закрывает.
    """
    # открытие браузера — сети за сниппетами не нужно вовсе
    if open_browser:
        url = ("https://duckduckgo.com/?q="
               + urllib.parse.quote_plus(query))
        try:
            subprocess.Popen(["xdg-open", url],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            said = f"Открываю результаты по запросу {query}."
        except Exception:
            logging.exception("не удалось открыть браузер")
            said = "Не смог открыть браузер."
        speak(cfg, said)
        return "command", said

    try:
        snippets = web_search.search_snippets(query)
    except web_search.SearchError as e:
        logging.warning("search failed: %s", e)
        speak(cfg, "Поиск недоступен.")
        return "speak", "Поиск недоступен."

    answer = ""
    if client is not None:
        try:
            sys_prompt = (
                "Ты Jarvis. Тебе дали результаты веб-поиска. Ответь кратко "
                "по-русски (2-4 предложения), только по делу, без упоминания "
                "источников и без markdown. Если в выдаче нет ответа — так и "
                "скажи коротко."
            )
            raw = client.chat(
                [{"role": "system", "content": sys_prompt},
                 {"role": "user",
                  "content": f"Запрос: {query}\n\nРезультаты поиска:\n"
                             f"{web_search.snippets_to_context(snippets)}"}],
                max_tokens=200, temperature=0.3)
            answer = raw.strip()[:500]
        except LLMError as e:
            logging.warning("суммаризация поиска не удалась: %s", e)

    if not answer:
        # fallback без LLM: заголовки топ-сниппетов
        answer = ". ".join(s["title"] for s in snippets[:3])[:500]
    speak(cfg, answer)
    return "speak", answer


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

    commands = load_commands()

    # Быстрый путь: точные фразы команд в тексте — исполняем локально и
    # мгновенно, без хода в облако. Известные фразы снова отвечают за
    # миллисекунды, как до LLM-маршрута.
    exact_cmds = match_commands_exact(
        text, commands, cfg.get("exact_max_extra_words", 6))
    if exact_cmds:
        logging.info("Exact fast-path: %s", [c.get("id") for c in exact_cmds])
        for c in exact_cmds:
            handle_command(cfg, c, 1.0)
        return False

    # Точных фраз нет — пробуем LLM-маршрут (мультикоманды, свободная речь).
    action_type = try_llm_route(cfg, text)
    if action_type:
        return action_type in ("speak", "ask")

    plan = match_commands_local(text, commands, cfg["match_threshold"],
                                cfg.get("match_ambiguity_margin", 0.06))
    if not plan:
        logging.info("Нет однозначного локального плана для %r", text)
        notify("Jarvis", f"Не понял: {text}")
        speak(cfg, "Не уверен, что правильно понял. Повтори команду точнее.")
        return False

    for cmd, score in plan:
        handle_command(cfg, cmd, score)
    return False


def try_llm_route(cfg: dict, text: str) -> str | None:
    """Пробует распарсить текст через LLM. Возвращает тип последнего
    значимого действия ("command"/"speak"/"ask") или None — fallback.

    Безопасно: любые ошибки ловятся, ничего не падает.

    LLM может вернуть НЕСКОЛЬКО действий (мультикоманды) — исполняются
    последовательно, каждое озвучивает свой ответ. При успешном маршруте
    также обновляет диалоговое состояние: push_turn для user, реплики
    ассистента для speak/ask; ask ставит pending, чисто командный батч
    диалог закрывает.
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
        actions = parse_intent(
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

    logging.info("LLM actions: %d шт.", len(actions))

    # Диалоговое состояние: реплика пользователя один раз, дальше по действиям.
    ttl = float(llm_cfg.get("conversation_window_seconds", 30))
    if _conv_available:
        conv.push_turn("user", text, ttl_seconds=ttl)

    saw_conversational = False  # был speak/ask — демон продолжает слушать
    last_ask_text = None

    for action in actions:
        act = action["action"]
        if act == "command":
            cmd = next((c for c in commands if c.get("id") == action["id"]), None)
            if cmd is None:
                # Батч уже частично исполнен — целиком на fallback отдавать
                # нельзя (риск повторного запуска), просто пропускаем действие.
                logging.error("LLM вернул несуществующий id %s — пропуск",
                              action["id"])
                continue
            # Принудительная установка confirm, если LLM потребовал
            if action.get("needs_confirmation") and not cmd.get("confirm"):
                cmd = dict(cmd)
                cmd["confirm"] = True
            handle_command(cfg, cmd, 1.0)
        elif act in ("speak", "ask"):
            speak(cfg, action["text"])
            saw_conversational = True
            if _conv_available:
                conv.push_turn("assistant", action["text"], ttl_seconds=ttl)
            if act == "ask":
                last_ask_text = action["text"]
        elif act == "search":
            if not _web_search_available:
                logging.warning("action=search, но web_search недоступен")
                speak(cfg, "Модуль поиска не установлен.")
                saw_conversational = True
                continue
            rtype, said = handle_search(
                cfg, action["query"], action.get("open", False), client=client)
            if rtype == "speak":
                # голосовой ответ на поиск — диалог продолжается
                saw_conversational = True
                if _conv_available:
                    conv.push_turn("assistant", said, ttl_seconds=ttl)
        else:
            # Неизвестный action — пропускаем, остальные исполняются
            logging.warning("LLM вернул неизвестный action: %r", act)

    # ask важнее: диалог продолжается; чисто командный батч его закрывает
    if _conv_available:
        if last_ask_text is not None:
            conv.set_pending(topic=text, question=last_ask_text,
                             ttl_seconds=ttl)
        elif any(a["action"] == "command" for a in actions):
            conv.clear()

    if last_ask_text is not None:
        return "ask"
    return "speak" if saw_conversational else "command"


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
    """Speak one message, then acknowledge its ID without losing concurrent writes."""
    if not INBOX_FILE.exists():
        return 0
    try:
        from autonomy import in_quiet_hours, load_autonomy_config
        if in_quiet_hours(load_autonomy_config().get("quiet_hours")):
            return 0
        item = inbox_store.peek(INBOX_FILE)
        if item is None:
            return 0
        text = (item.get("text") or "").strip()
        if text:
            logging.info("inbox: сообщение от %s", item.get("source", "autonomy"))
            notify("Jarvis — состояние системы", text)
            speak(cfg, text)
        inbox_store.acknowledge(INBOX_FILE, item["id"])
        return int(bool(text))
    except Exception:
        logging.exception("inbox: не удалось обработать сообщение; сохраняю для повтора")
        return 0


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
