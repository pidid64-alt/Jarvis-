"""Инструменты напоминаний: создать, посмотреть, удалить.

Напоминания исполняет планировщик: в срок придёт сообщение в Telegram-бота
(или запись в журнал, если бот не запущен и владелец не определён).
"""

from __future__ import annotations

from datetime import datetime

from jarvis.core.tools.base import Tool, ToolResult
from jarvis.memory.store import MemoryStore
from jarvis.scheduler import runner


def create_tools(memory: MemoryStore) -> list[Tool]:
    async def create_reminder(args: dict) -> ToolResult:
        text = str(args.get("text", "")).strip()
        if not text:
            return ToolResult(output="Ошибка: пустый текст напоминания.")
        if args.get("in_minutes") is not None:
            next_run = runner.compute_in_minutes(float(args["in_minutes"]))
            repeat = None
        elif args.get("at"):
            try:
                next_run = runner.compute_at(str(args["at"]))
            except ValueError as e:
                return ToolResult(output=f"Ошибка: {e}")
            repeat = None
        elif args.get("daily_time"):
            next_run = runner.compute_daily(str(args["daily_time"]))
            repeat = runner._DAY_SECONDS
        else:
            return ToolResult(output="Ошибка: укажи in_minutes, at или daily_time.")
        job_id = await memory.add_job("reminder", next_run, interval_seconds=repeat, payload={"text": text})
        when = datetime.fromtimestamp(next_run).strftime("%d.%m.%Y %H:%M")
        return ToolResult(output=f"Напоминание создано (id={job_id}): {when} — {text}")

    async def list_reminders(args: dict) -> ToolResult:
        jobs = [j for j in await memory.list_jobs() if j["kind"] == "reminder"]
        if not jobs:
            return ToolResult(output="Напоминаний нет.")
        lines = [
            f"[id={j['id']}] {datetime.fromtimestamp(j['next_run']):%d.%m %H:%M}"
            f"{' (ежедневно)' if j['interval_seconds'] else ''} — {j['payload'].get('text', '')}"
            for j in jobs[:20]
        ]
        return ToolResult(output="Напоминания:\n" + "\n".join(lines))

    async def delete_reminder(args: dict) -> ToolResult:
        job_id = int(args.get("id", 0))
        jobs = {j["id"]: j for j in await memory.list_jobs()}
        if job_id not in jobs or jobs[job_id]["kind"] != "reminder":
            return ToolResult(output=f"Напоминание id={job_id} не найдено.")
        await memory.delete_job(job_id)
        return ToolResult(output=f"Напоминание id={job_id} удалено.")

    return [
        Tool(
            name="create_reminder",
            description=(
                "Создать напоминание. Один способ указания времени обязателен: "
                "in_minutes (через сколько минут), at ('ГГГГ-ММ-ДД ЧЧ:ММ') или "
                "daily_time ('ЧЧ:ММ' — каждый день)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "О чём напомнить"},
                    "in_minutes": {"type": "number", "description": "Через сколько минут"},
                    "at": {"type": "string", "description": "Точная дата и время, ГГГГ-ММ-ДД ЧЧ:ММ"},
                    "daily_time": {"type": "string", "description": "Ежедневно в ЧЧ:ММ"},
                },
                "required": ["text"],
            },
            handler=create_reminder,
            side_effect="write",
        ),
        Tool(
            name="list_reminders",
            description="Список активных напоминаний.",
            parameters={"type": "object", "properties": {}},
            handler=list_reminders,
            side_effect="read",
        ),
        Tool(
            name="delete_reminder",
            description="Удалить напоминание по id.",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
            },
            handler=delete_reminder,
            side_effect="write",
        ),
    ]
