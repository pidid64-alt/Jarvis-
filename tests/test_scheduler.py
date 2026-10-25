"""Тесты этапа 2: задания планировщика, стиль, время напоминаний, брифинг."""

import time
from datetime import datetime, timedelta

import pytest

from jarvis.config import SchedulerConfig
from jarvis.memory.db import Database
from jarvis.memory.store import MemoryStore
from jarvis.memory.vector import EmbeddingEngine
from jarvis.scheduler import runner


@pytest.fixture
async def memory(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    yield MemoryStore(db, EmbeddingEngine(enabled=False))
    await db.close()


# ---------- время ----------

def test_compute_at_parses_formats():
    ts = runner.compute_at("2026-10-26 09:00")
    assert datetime.fromtimestamp(ts) == datetime(2026, 10, 26, 9, 0)
    assert datetime.fromtimestamp(runner.compute_at("26.10.2026 09:30")) == datetime(2026, 10, 26, 9, 30)


def test_compute_at_rejects_garbage():
    with pytest.raises(ValueError):
        runner.compute_at("завтра")


def test_compute_daily_next_occurrence():
    hhmm = (datetime.now() + timedelta(minutes=1)).strftime("%H:%M")
    ts = runner.compute_daily(hhmm)
    assert ts > time.time()
    assert datetime.fromtimestamp(ts).strftime("%H:%M") == hhmm


# ---------- задания ----------

async def test_reminder_job_lifecycle(memory):
    job_id = await memory.add_job("reminder", time.time() - 1, payload={"text": "выпить воды"})
    due = await memory.due_jobs(time.time())
    assert len(due) == 1 and due[0]["payload"]["text"] == "выпить воды"

    # Одноразовое задание: после захвата — удаляется.
    assert await memory.claim_job(job_id, None, time.time()) is True
    assert await memory.due_jobs(time.time()) == []
    assert await memory.claim_job(job_id, None, time.time()) is False


async def test_repeating_job_claims_once(memory):
    job_id = await memory.add_job("briefing", time.time() - 1, interval_seconds=86400)
    assert await memory.claim_job(job_id, 86400, time.time()) is True
    # Повторный захват до наступления нового срока невозможен.
    assert await memory.claim_job(job_id, 86400, time.time()) is False
    jobs = {j["id"]: j for j in await memory.list_jobs()}
    assert jobs[job_id]["next_run"] > time.time()


# ---------- стиль ----------

async def test_style_examples_crud(memory):
    example_id = await memory.add_style_example("Привет! Как дела?)")
    examples = await memory.list_style_examples()
    assert len(examples) == 1 and "Как дела" in examples[0]["text"]
    await memory.delete_style_example(example_id)
    assert await memory.list_style_examples() == []


# ---------- мониторинг ----------

async def test_monitor_state_diff(memory):
    await memory.set_monitor_state("https://x.test", "hash1", "Заголовок")
    state = await memory.get_monitor_state("https://x.test")
    assert state["content_hash"] == "hash1"
    await memory.set_monitor_state("https://x.test", "hash2", "Заголовок")
    assert (await memory.get_monitor_state("https://x.test"))["content_hash"] == "hash2"
    assert len(await memory.recent_changes(since_seconds=3600)) == 1


# ---------- брифинг ----------

async def test_briefing_composes_without_crash(memory):
    from jarvis.scheduler.notify import TelegramNotifier

    notifier = TelegramNotifier(token="", data_dir=memory._db._path.parent)
    scheduler = runner.Scheduler(memory, notifier, config=SchedulerConfig())
    text = await scheduler.compose_briefing()
    assert "Брифинг" in text
    await notifier.close()


async def test_briefing_includes_today_reminder(memory):
    from jarvis.scheduler.notify import TelegramNotifier

    await memory.add_job(
        "reminder", datetime.now().replace(hour=23, minute=59).timestamp(), payload={"text": "лечь спать"}
    )
    notifier = TelegramNotifier(token="", data_dir=memory._db._path.parent)
    scheduler = runner.Scheduler(memory, notifier, config=SchedulerConfig())
    text = await scheduler.compose_briefing()
    assert "лечь спать" in text
    await notifier.close()
