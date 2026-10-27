"""Хранилище памяти: долгосрочные факты + краткосрочные диалоги.

Поиск фактов комбинирует FTS5 и (при наличии) векторный поиск через RRF.
Любую запись можно посмотреть, исправить или удалить — из CLI или самим
ассистентом; методы update/delete используются командой `jarvis memory`.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time

import numpy as np

from jarvis.memory.db import Database
from jarvis.memory.vector import EmbeddingEngine, cosine_top_k

_KINDS = ("fact", "contact", "task", "note")

# Обрезка tool-результатов при воспроизведении истории: экономия контекста.
_TOOL_REPLAY_LIMIT = 4000


class MemoryStore:
    def __init__(self, db: Database, engine: EmbeddingEngine):
        self._db = db
        self._engine = engine

    # ---------- долгосрочная память: факты ----------

    async def save_fact(self, text: str, kind: str = "fact", meta: dict | None = None) -> int:
        if kind not in _KINDS:
            kind = "fact"
        now = time.time()
        fact_id = await self._db.execute(
            "INSERT INTO facts (kind, text, meta, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (kind, text, json.dumps(meta, ensure_ascii=False) if meta else None, now, now),
        )
        await self._db.execute("INSERT INTO facts_fts (text, fact_id) VALUES (?, ?)", (text, fact_id))
        await self._store_vector(fact_id, text)
        return fact_id

    async def update_fact(self, fact_id: int, text: str) -> None:
        await self._db.execute(
            "UPDATE facts SET text = ?, updated_at = ? WHERE id = ?", (text, time.time(), fact_id)
        )
        await self._db.execute("DELETE FROM facts_fts WHERE fact_id = ?", (fact_id,))
        await self._db.execute("INSERT INTO facts_fts (text, fact_id) VALUES (?, ?)", (text, fact_id))
        await self._db.execute("DELETE FROM vectors WHERE fact_id = ?", (fact_id,))
        await self._store_vector(fact_id, text)

    async def delete_fact(self, fact_id: int) -> None:
        await self._db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        await self._db.execute("DELETE FROM facts_fts WHERE fact_id = ?", (fact_id,))
        await self._db.execute("DELETE FROM vectors WHERE fact_id = ?", (fact_id,))

    async def get_fact(self, fact_id: int) -> dict | None:
        row = await self._db.fetchone("SELECT id, kind, text, created_at, updated_at FROM facts WHERE id = ?", (fact_id,))
        return dict(row) if row else None

    async def list_facts(self, kind: str | None = None, limit: int = 500) -> list[dict]:
        if kind:
            rows = await self._db.fetchall(
                "SELECT id, kind, text, created_at, updated_at FROM facts WHERE kind = ? ORDER BY id DESC LIMIT ?",
                (kind, limit),
            )
        else:
            rows = await self._db.fetchall(
                "SELECT id, kind, text, created_at, updated_at FROM facts ORDER BY id DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in rows]

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Гибридный поиск: FTS5 + векторы, слияние через RRF."""
        rrf: dict[int, float] = {}
        found: dict[int, dict] = {}

        fts_rows = await self._fts_search(query, top_k)
        for rank, row in enumerate(fts_rows):
            fact_id = row["id"]
            rrf[fact_id] = rrf.get(fact_id, 0.0) + 1.0 / (60 + rank)
            found[fact_id] = row

        vec_rows = await self._vector_search(query, top_k)
        for rank, row in enumerate(vec_rows):
            fact_id = row["id"]
            rrf[fact_id] = rrf.get(fact_id, 0.0) + 1.0 / (60 + rank)
            found.setdefault(fact_id, row)

        ordered = sorted(rrf.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [{**found[fid], "score": score} for fid, score in ordered]

    async def _fts_search(self, query: str, top_k: int) -> list[dict]:
        tokens = re.findall(r"\w{2,}", query, re.UNICODE)[:8]
        if not tokens:
            return []
        match = " OR ".join(f'"{t}"' for t in tokens)
        try:
            rows = await self._db.fetchall(
                "SELECT f.id, f.kind, f.text FROM facts_fts t JOIN facts f ON f.id = t.fact_id "
                "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, top_k),
            )
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]

    async def _vector_search(self, query: str, top_k: int) -> list[dict]:
        # Внимание: .ready при первом обращении синхронно грузит ONNX-модель —
        # поэтому даже проверку готовности делаем в потоке.
        if not await asyncio.to_thread(lambda: self._engine.ready):
            return []
        # ONNX-инференс тяжёлый: считаем в потоке, иначе замораживаем
        # весь цикл событий (чат, статусы, остановка перестают отвечать).
        q = await asyncio.to_thread(self._engine.embed_query, query)
        if q is None:
            return []
        rows = await self._db.fetchall("SELECT fact_id, dim, vec FROM vectors")
        if not rows:
            return []
        vectors = [(r["fact_id"], np.frombuffer(r["vec"], dtype=np.float32)) for r in rows]
        top = await asyncio.to_thread(cosine_top_k, q, vectors, top_k)
        result = []
        for fact_id, _score in top:
            fact = await self.get_fact(fact_id)
            if fact:
                result.append(fact)
        return result

    async def _store_vector(self, fact_id: int, text: str) -> None:
        if not await asyncio.to_thread(lambda: self._engine.ready):
            return
        embeddings = await asyncio.to_thread(self._engine.embed_documents, [text])
        if not embeddings:
            return
        vec = embeddings[0]
        await self._db.execute(
            "INSERT OR REPLACE INTO vectors (fact_id, dim, vec) VALUES (?, ?, ?)",
            (fact_id, int(vec.size), vec.tobytes()),
        )

    # ---------- краткосрочная память: диалоги ----------

    async def append_dialog(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO dialogs (ts, session_id, role, content, tool_calls, tool_call_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                session_id,
                role,
                content,
                json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                tool_call_id,
            ),
        )

    async def recent_dialog(self, session_id: str, limit: int) -> list[dict]:
        """Последние сообщения сессии в формате сообщений модели (без system)."""
        rows = await self._db.fetchall(
            "SELECT role, content, tool_calls, tool_call_id FROM dialogs WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
        messages = []
        for row in reversed(rows):
            content = row["content"] or ""
            if row["role"] == "tool":
                content = content[:_TOOL_REPLAY_LIMIT]
                messages.append(
                    {"role": "tool", "tool_call_id": row["tool_call_id"], "content": content}
                )
            elif row["role"] == "assistant" and row["tool_calls"]:
                # В БД храним вызовы компактно {"id","name","arguments"};
                # для провайдера нужно строгое поле wire-формата OpenAI.
                wire_calls = []
                for tc in json.loads(row["tool_calls"]):
                    args = tc.get("arguments", {})
                    wire_calls.append(
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc.get("name", ""),
                                "arguments": (
                                    json.dumps(args, ensure_ascii=False)
                                    if isinstance(args, dict)
                                    else str(args or "{}")
                                ),
                            },
                        }
                    )
                messages.append(
                    {"role": "assistant", "content": content or None, "tool_calls": wire_calls}
                )
            elif row["role"] == "assistant":
                messages.append({"role": "assistant", "content": content})
            else:
                messages.append({"role": "user", "content": content})
        return messages

    async def clear_dialog(self, session_id: str) -> None:
        await self._db.execute("DELETE FROM dialogs WHERE session_id = ?", (session_id,))

    # ---------- этап 2: стиль, задания планировщика, мониторинг ----------

    async def add_style_example(self, text: str, source: str = "manual") -> int:
        return await self._db.execute(
            "INSERT INTO style_examples (text, source, created_at) VALUES (?, ?, ?)",
            (text, source, time.time()),
        )

    async def list_style_examples(self, limit: int = 50) -> list[dict]:
        rows = await self._db.fetchall(
            "SELECT id, text, source, created_at FROM style_examples ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    async def delete_style_example(self, example_id: int) -> None:
        await self._db.execute("DELETE FROM style_examples WHERE id = ?", (example_id,))

    async def add_job(self, kind: str, next_run: float, interval_seconds: int | None = None, payload: dict | None = None) -> int:
        return await self._db.execute(
            "INSERT INTO jobs (kind, next_run, interval_seconds, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (kind, next_run, interval_seconds, json.dumps(payload, ensure_ascii=False) if payload else None, time.time()),
        )

    async def due_jobs(self, now: float) -> list[dict]:
        rows = await self._db.fetchall(
            "SELECT id, kind, next_run, interval_seconds, payload FROM jobs WHERE next_run <= ? ORDER BY next_run",
            (now,),
        )
        result = []
        for r in rows:
            job = dict(r)
            job["payload"] = json.loads(job["payload"]) if job["payload"] else {}
            result.append(job)
        return result

    async def claim_job(self, job_id: int, interval_seconds: int | None, now: float) -> bool:
        """Забирает задание на исполнение: повторяющее переносится вперёд,
        одноразовое удаляется. False — задание уже забрал другой процесс."""
        if interval_seconds:
            rowcount = await self._db.execute_rowcount(
                "UPDATE jobs SET next_run = ? WHERE id = ? AND next_run <= ?",
                (now + interval_seconds, job_id, now),
            )
        else:
            rowcount = await self._db.execute_rowcount(
                "DELETE FROM jobs WHERE id = ? AND next_run <= ?", (job_id, now)
            )
        return rowcount > 0

    async def list_jobs(self) -> list[dict]:
        rows = await self._db.fetchall(
            "SELECT id, kind, next_run, interval_seconds, payload FROM jobs ORDER BY next_run"
        )
        result = []
        for r in rows:
            job = dict(r)
            job["payload"] = json.loads(job["payload"]) if job["payload"] else {}
            result.append(job)
        return result

    async def delete_job(self, job_id: int) -> None:
        await self._db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    async def job_exists(self, kind: str) -> bool:
        row = await self._db.fetchone("SELECT 1 FROM jobs WHERE kind = ? LIMIT 1", (kind,))
        return row is not None

    async def get_monitor_state(self, url: str) -> dict | None:
        row = await self._db.fetchone(
            "SELECT content_hash, title, changed_at FROM monitoring_state WHERE url = ?", (url,)
        )
        return dict(row) if row else None

    async def set_monitor_state(self, url: str, content_hash: str, title: str | None) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO monitoring_state (url, content_hash, title, changed_at) VALUES (?, ?, ?, ?)",
            (url, content_hash, title, time.time()),
        )

    async def recent_changes(self, since_seconds: float) -> list[dict]:
        rows = await self._db.fetchall(
            "SELECT url, title, changed_at FROM monitoring_state WHERE changed_at >= ? ORDER BY changed_at DESC",
            (time.time() - since_seconds,),
        )
        return [dict(r) for r in rows]
