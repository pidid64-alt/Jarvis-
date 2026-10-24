"""История диалога и состояние «жду уточнения».

Две вещи в одном месте:

* ``conversation.json`` — контекст для LLM (последние реплики, незакрытый вопрос);
* ``history.jsonl`` — лента диалога для интерфейса (её показывает GUI и CLI).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import paths

DEFAULT_TTL = 30.0
MAX_HISTORY = 8
HISTORY_FILE_LIMIT = 2000


def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class ConversationState:
    """Короткая память диалога с истечением по времени."""

    def __init__(self, path: Path | None = None, *, ttl: float = DEFAULT_TTL, max_history: int = MAX_HISTORY):
        self.path = Path(path) if path else paths.state_dir() / "conversation.json"
        self.ttl = ttl
        self.max_history = max_history
        self._lock = threading.RLock()

    # ------------------------------------------------------------------- load
    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._read()

    # ------------------------------------------------------------------ turns
    def push_turn(self, role: str, text: str) -> None:
        with self._lock:
            data = self._read()
            turns = data.get("history_tail") or []
            turns.append({"role": role, "text": text, "ts": time.time()})
            data["history_tail"] = turns[-self.max_history :]
            data["expires_at"] = time.time() + self.ttl
            _atomic_write(self.path, data)

    def history_tail(self, limit: int | None = None) -> list[dict[str, Any]]:
        data = self.snapshot()
        turns = data.get("history_tail") or []
        limit = limit or self.max_history
        return turns[-limit:]

    def alive(self) -> bool:
        data = self.snapshot()
        return bool(data.get("expires_at", 0) > time.time())

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    self.path.unlink()
                except OSError:
                    pass

    # ---------------------------------------------------------------- pending
    def set_pending(self, topic: str, question: str) -> None:
        with self._lock:
            data = self._read()
            data["pending"] = {"topic": topic, "question": question, "ts": time.time()}
            data["expires_at"] = time.time() + self.ttl
            _atomic_write(self.path, data)

    def consume_pending(self) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            pending = data.get("pending")
            if not pending:
                return None
            if pending.get("ts", 0) + self.ttl < time.time():
                data.pop("pending", None)
                _atomic_write(self.path, data)
                return None
            data.pop("pending", None)
            _atomic_write(self.path, data)
            return pending


class DialogLog:
    """Лента диалога для интерфейса (JSON Lines, с подрезкой)."""

    def __init__(self, path: Path | None = None, *, compact_at: int = HISTORY_FILE_LIMIT):
        self.path = Path(path) if path else paths.history_file()
        self.compact_at = compact_at
        self._lock = threading.RLock()

    def append(self, role: str, text: str, **extra: Any) -> dict[str, Any]:
        record = {"ts": time.time(), "role": role, "text": text, **extra}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._maybe_compact()
        return record

    def read(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            records = self._read_all()
        return records[-limit:]

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records

    def _maybe_compact(self) -> None:
        try:
            if self.path.stat().st_size < 256 * 1024:
                return
            records = self._read_all()[-self.compact_at // 2 :]
            body = "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n"
            self.path.write_text(body, encoding="utf-8")
        except OSError:
            pass
