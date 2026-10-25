"""Единая SQLite-база: факты, диалоги, кеш страниц, аудит, векторы.

Один файл — весь состояние ассистента; бэкап = копия файла.
FTS5 для полнотекстового поиска (встроен в CPython), векторы — BLOB + numpy.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger("jarvis.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'fact',
    text TEXT NOT NULL,
    meta TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(text, fact_id UNINDEXED);

CREATE TABLE IF NOT EXISTS vectors (
    fact_id INTEGER PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS dialogs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT,
    tool_call_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_dialogs_session ON dialogs(session_id, id);

CREATE TABLE IF NOT EXISTS page_cache (
    url TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    status INTEGER NOT NULL,
    content_type TEXT,
    body BLOB
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    tool TEXT NOT NULL,
    args TEXT,
    decision TEXT,
    status TEXT,
    error TEXT,
    confirmed_by TEXT
);

-- Фоновые задания планировщика (этап 2): напоминания, брифинги, мониторинг.
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,             -- reminder | briefing | monitor
    next_run REAL NOT NULL,         -- unix-время следующего запуска
    interval_seconds INTEGER,       -- NULL = одноразовое задание
    payload TEXT,                   -- JSON с параметрами (текст напоминания и т.п.)
    created_at REAL NOT NULL
);

-- Примеры сообщений пользователя для стиля черновиков (few-shot).
CREATE TABLE IF NOT EXISTS style_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    source TEXT DEFAULT 'manual',   -- manual | telegram_history | ...
    created_at REAL NOT NULL
);

-- Состояние отслеживаемых страниц (детектор изменений).
CREATE TABLE IF NOT EXISTS monitoring_state (
    url TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    title TEXT,
    changed_at REAL NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.commit()
        log.info("База данных готова: %s", self._path)

    async def execute(self, sql: str, params: tuple = ()) -> int:
        assert self._conn is not None, "connect() не вызван"
        cur = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cur.lastrowid or 0

    async def execute_rowcount(self, sql: str, params: tuple = ()) -> int:
        """Для UPDATE/DELETE: сколько строк затронуто (база для атомарных «захватов»)."""
        assert self._conn is not None, "connect() не вызван"
        cur = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cur.rowcount

    async def fetchone(self, sql: str, params: tuple = ()):
        assert self._conn is not None, "connect() не вызван"
        cur = await self._conn.execute(sql, params)
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()):
        assert self._conn is not None, "connect() не вызван"
        cur = await self._conn.execute(sql, params)
        return list(await cur.fetchall())

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
