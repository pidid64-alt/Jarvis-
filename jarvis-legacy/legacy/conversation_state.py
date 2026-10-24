#!/usr/bin/env python3
"""
conversation_state.py
---------------------
Хранит состояние текущей диалоговой сессии в STATE_DIR/conversation_state.json:

  - last_topic:  о чём говорили (для контекста LLM)
  - history_tail: последние N реплик (user/jarvis), для передачи в LLM
  - pending_action: если LLM вернул ask, следующий голос идёт как уточнение
                    без wake-word, пока сессия не истечёт
  - expires_at:   абсолютный timestamp окончания сессии

TTL по умолчанию 30 секунд (настраивается в config.json).

Файл обновляется атомарно (write через tmp+rename) — без блокировок,
потому что пишет только jarvis.py из одного потока и читает тоже из него.
"""

import json
import logging
import time
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "share" / "jarvis"
CONV_FILE = STATE_DIR / "conversation_state.json"

DEFAULT_TTL = 30.0
MAX_HISTORY = 6  # 3 пары user/assistant


def _atomic_write(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        logging.exception("conversation_state atomic write failed")


def load() -> dict:
    """Возвращает текущее состояние. Пустой dict если файла нет или он истёк."""
    if not CONV_FILE.exists():
        return {}
    try:
        data = json.loads(CONV_FILE.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("conversation_state: битый файл, сброс")
        return {}
    if not data:
        return {}
    expires = data.get("expires_at", 0)
    if expires and time.time() > expires:
        # сессия истекла — стираем
        clear()
        return {}
    return data


def save(state: dict):
    """Атомарно сохраняет state."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write(CONV_FILE, state)


def clear():
    if CONV_FILE.exists():
        try:
            CONV_FILE.unlink()
        except Exception:
            pass


def push_turn(role: str, text: str, ttl_seconds: float = DEFAULT_TTL):
    """Добавляет реплику в историю, обновляет expires_at."""
    state = load()
    history = state.get("history_tail", [])
    history.append({"role": role, "text": text, "ts": time.time()})
    # оставляем хвост
    state["history_tail"] = history[-MAX_HISTORY:]
    state["last_topic"] = text[:120] if role == "user" else state.get("last_topic")
    state["expires_at"] = time.time() + ttl_seconds
    save(state)


def set_pending(topic: str, question: str, ttl_seconds: float = DEFAULT_TTL):
    """LLM вернул ask — следующий голос будет уточнением по этой теме."""
    state = load()
    state["pending_action"] = {
        "topic": topic,
        "question": question,
        "created_at": time.time(),
    }
    state["expires_at"] = time.time() + ttl_seconds
    save(state)


def consume_pending() -> dict | None:
    """Возвращает pending_action и очищает его. None если ничего не было."""
    state = load()
    pending = state.get("pending_action")
    if pending:
        state.pop("pending_action", None)
        save(state)
    return pending


def is_alive() -> bool:
    """Есть ли активная сессия (с pending или просто не истёкшая)."""
    return bool(load())