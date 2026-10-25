"""Планировщик этапа 2: напоминания, утренний брифинг, мониторинг страниц.

Собственный лёгкий цикл поверх таблицы jobs в SQLite: каждые 30 секунд
забирает созревшие задания (атомарный захват — два процесса не удвоят
отправку). Уведомления уходят владельцу в Telegram-бота; без бота —
только в журнал.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from jarvis.config import SchedulerConfig
from jarvis.connectors.base import Connector
from jarvis.core.llm.base import LLMProvider
from jarvis.memory.store import MemoryStore
from jarvis.scheduler.notify import TelegramNotifier
from jarvis.web.fetcher import WebFetcher
from jarvis.web.sites.base import SiteRegistry

log = logging.getLogger("jarvis.scheduler")

_TICK_SECONDS = 30
_DAY_SECONDS = 86400


# ---------- вычисление времени (используются и инструментами, и CLI) ----------

def compute_in_minutes(minutes: float) -> float:
    import time

    return time.time() + minutes * 60


def compute_at(moment: str) -> float:
    """«2026-10-26 09:00» или «2026-10-26T09:00» → unix-время (локальное)."""
    normalized = moment.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(normalized, fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(f"Не понял время {moment!r} — формат ГГГГ-ММ-ДД ЧЧ:ММ")


def compute_daily(hhmm: str) -> float:
    """Следующее наступление «ЧЧ:ММ» по местному времени."""
    hour, minute = map(int, hhmm.strip().split(":"))
    now = datetime.now()
    today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if today <= now:
        today += timedelta(days=1)
    return today.timestamp()


@dataclass
class MonitorPage:
    url: str
    note: str = ""


def load_monitoring_pages(path: Path) -> list[MonitorPage]:
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        log.exception("Не удалось прочитать %s", path)
        return []
    pages = []
    for item in data.get("pages", []):
        if isinstance(item, dict) and item.get("url"):
            pages.append(MonitorPage(url=str(item["url"]), note=str(item.get("note", ""))))
        elif isinstance(item, str):
            pages.append(MonitorPage(url=item))
    return pages


# ---------- планировщик ----------

class Scheduler:
    def __init__(
        self,
        store: MemoryStore,
        notifier: TelegramNotifier,
        *,
        fetcher: WebFetcher | None = None,
        sites: SiteRegistry | None = None,
        llm: LLMProvider | None = None,
        telegram: Connector | None = None,
        config: SchedulerConfig | None = None,
        monitoring_path: Path | None = None,
    ):
        self._store = store
        self._notifier = notifier
        self._fetcher = fetcher
        self._sites = sites
        self._llm = llm
        self._telegram = telegram
        self._cfg = config or SchedulerConfig()
        self._monitoring_path = monitoring_path
        self._task: asyncio.Task | None = None

    # ---------- жизненный цикл ----------

    async def start(self) -> None:
        if not self._cfg.enabled:
            log.info("Планировщик отключён в конфиге")
            return
        await self._ensure_system_jobs()
        self._task = asyncio.create_task(self._loop(), name="jarvis-scheduler")
        log.info("Планировщик запущен (тик %s с)", _TICK_SECONDS)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._notifier.close()

    async def _ensure_system_jobs(self) -> None:
        """Регулярные задания создаются, если их ещё нет (переживают перезапуск)."""
        if self._cfg.briefing.enabled and not await self._store.job_exists("briefing"):
            await self._store.add_job("briefing", compute_daily(self._cfg.briefing.time), _DAY_SECONDS)
            log.info("Брифинг запланирован на %s", self._cfg.briefing.time)
        if self._cfg.monitoring.enabled and not await self._store.job_exists("monitor"):
            await self._store.add_job("monitor", compute_in_minutes(1), self._cfg.monitoring.interval_min * 60)
            log.info("Мониторинг страниц: каждые %s мин", self._cfg.monitoring.interval_min)

    # ---------- основной цикл ----------

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Тик планировщика упал — продолжаю")
            await asyncio.sleep(_TICK_SECONDS)

    async def _tick(self) -> None:
        import time

        now = time.time()
        for job in await self._store.due_jobs(now):
            if not await self._store.claim_job(job["id"], job["interval_seconds"], now):
                continue  # забрал другой процесс
            try:
                await self._dispatch(job)
            except Exception:
                log.exception("Задание %s (%s) упало", job["id"], job["kind"])

    async def _dispatch(self, job: dict) -> None:
        kind = job["kind"]
        if kind == "reminder":
            text = job["payload"].get("text", "(пустое напоминание)")
            await self._notifier.send(f"⏰ Напоминание: {text}")
        elif kind == "briefing":
            await self._notifier.send(await self.compose_briefing())
        elif kind == "monitor":
            await self.check_monitored_pages()
        else:
            log.warning("Неизвестный тип задания: %s", kind)

    # ---------- брифинг ----------

    async def compose_briefing(self) -> str:

        from datetime import datetime

        sections: list[str] = [f"Доброе утро! Брифинг на {datetime.now():%d.%m.%Y}."]

        if self._telegram is not None:
            try:
                unread = await self._telegram.unread_summary(limit_dialogs=30)
                if "Непрочитанных сообщений нет" not in unread:
                    sections.append("📬 Telegram:\n" + unread)
            except Exception as e:
                log.debug("Непрочитанное недоступно: %s", e)

        changes = await self._store.recent_changes(since_seconds=_DAY_SECONDS)
        if changes:
            lines = [f"📄 {c['title'] or c['url']}: {c['url']}" for c in changes[:10]]
            sections.append("🌐 Изменилось за сутки:\n" + "\n".join(lines))

        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        reminders_today = [
            j for j in await self._store.list_jobs()
            if j["kind"] == "reminder" and day_start <= j["next_run"] <= day_start + _DAY_SECONDS
        ]
        if reminders_today:
            lines = [
                f"⏰ {datetime.fromtimestamp(j['next_run']):%H:%M} — {j['payload'].get('text', '')}"
                for j in reminders_today[:10]
            ]
            sections.append("📅 Напоминания на сегодня:\n" + "\n".join(lines))

        tasks = [f for f in await self._store.list_facts(kind="task", limit=10)]
        if tasks:
            sections.append("✅ Открытые задачи:\n" + "\n".join(f"- {t['text'][:120]}" for t in tasks))

        if len(sections) == 1:
            sections.append("Пока без новостей: непрочитанного нет, страницы не менялись, задач нет.")
        return "\n\n".join(sections)

    # ---------- мониторинг страниц ----------

    async def check_monitored_pages(self) -> None:
        if self._fetcher is None or self._sites is None or self._monitoring_path is None:
            return
        pages = load_monitoring_pages(self._monitoring_path)
        for page in pages:
            try:
                fetched = await self._fetcher.fetch(page.url, use_cache=False)
                parsed = await self._sites.pick(page.url).parse(fetched.html, fetched.url)
                content_hash = hashlib.sha256(parsed.markdown[:50000].encode("utf-8")).hexdigest()
            except Exception as e:
                log.warning("Мониторинг %s: %s", page.url, e)
                continue
            previous = await self._store.get_monitor_state(page.url)
            await self._store.set_monitor_state(page.url, content_hash, parsed.title)
            if previous and previous["content_hash"] != content_hash:
                note = f" ({page.note})" if page.note else ""
                await self._notifier.send(
                    f"📄 Страница изменилась{note}:\n{parsed.title or page.url}\n{page.url}"
                )
