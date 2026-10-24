"""Журнал действий агента: кто, что, когда вызвал и с каким результатом."""

from __future__ import annotations

import json
import time

from jarvis.memory.db import Database


class AuditLog:
    def __init__(self, db: Database):
        self._db = db

    async def log(
        self,
        tool: str,
        args: dict,
        decision: str,
        status: str,
        error: str | None = None,
        confirmed_by: str | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO audit_log (ts, tool, args, decision, status, error, confirmed_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                tool,
                json.dumps(args, ensure_ascii=False, default=str)[:4000],
                decision,
                status,
                error,
                confirmed_by,
            ),
        )

    async def recent(self, limit: int = 30) -> list[dict]:
        rows = await self._db.fetchall(
            "SELECT ts, tool, decision, status, error, confirmed_by FROM audit_log "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]
