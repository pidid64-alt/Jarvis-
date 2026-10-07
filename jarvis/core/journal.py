"""Журнал событий: то, что видит пользователь на странице «Журнал».

Пишется в ``journal.jsonl`` (JSON Lines), файл ограничен по размеру и
автоматически подрезается. Все записи проходят через маскирование секретов,
поэтому «скопировать для отчёта» безопасно.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from . import paths, secrets

LEVELS = ("debug", "info", "warning", "error")


class Journal:
    """Кольцевой журнал в файле + в памяти (чтобы GUI отвечал мгновенно)."""

    def __init__(self, path: Path | None = None, *, max_bytes: int = 512 * 1024, memory: int = 300):
        self.path = Path(path) if path else paths.journal_file()
        self.max_bytes = max_bytes
        self._memory: list[dict[str, Any]] = []
        self._memory_limit = memory
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ write
    def add(self, level: str, source: str, message: str, **extra: Any) -> dict[str, Any]:
        record = {
            "ts": time.time(),
            "level": level if level in LEVELS else "info",
            "source": source,
            "message": secrets.mask_text(str(message)),
        }
        if extra:
            record["data"] = {key: secrets.mask_text(str(value)) for key, value in extra.items()}
        with self._lock:
            self._memory.append(record)
            if len(self._memory) > self._memory_limit:
                del self._memory[:-self._memory_limit]
            try:
                self._append_file(record)
            except OSError:
                pass  # журнал не должен ломать работу ассистента
        return record

    def info(self, source: str, message: str, **extra: Any) -> dict[str, Any]:
        return self.add("info", source, message, **extra)

    def warning(self, source: str, message: str, **extra: Any) -> dict[str, Any]:
        return self.add("warning", source, message, **extra)

    def error(self, source: str, message: str, **extra: Any) -> dict[str, Any]:
        return self.add("error", source, message, **extra)

    def _append_file(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        try:
            if self.path.stat().st_size > self.max_bytes:
                self._trim()
        except OSError:
            pass

    def _trim(self) -> None:
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        keep = lines[len(lines) // 3 :]
        self.path.write_text("\n".join(keep) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------- read
    def read(self, limit: int = 200, level: str | None = None, search: str = "") -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._memory)
        if not records and self.path.exists():
            records = self._read_file(limit=limit)
        if level:
            order = {name: index for index, name in enumerate(LEVELS)}
            minimum = order.get(level, 0)
            records = [item for item in records if order.get(item.get("level", "info"), 0) >= minimum]
        if search:
            needle = search.casefold()
            records = [item for item in records if needle in str(item.get("message", "")).casefold()]
        return records[-limit:]

    def _read_file(self, limit: int = 200) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            return []
        return records

    def report(self, limit: int = 300, extra_lines: Iterable[str] = ()) -> str:
        """Текст для кнопки «скопировать для отчёта» (секреты уже вырезаны)."""
        from .. import __version__  # локальный импорт, чтобы не плодить циклы

        lines = [f"Jarvis {__version__} — отчёт о работе", ""]
        lines.extend(extra_lines)
        if extra_lines:
            lines.append("")
        for record in self.read(limit=limit):
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.get("ts", 0)))
            data = record.get("data")
            suffix = f" {data}" if data else ""
            lines.append(f"{stamp} [{record.get('level', 'info').upper()}] {record.get('source', '?')}: "
                         f"{record.get('message', '')}{suffix}")
        return secrets.mask_text("\n".join(lines))
