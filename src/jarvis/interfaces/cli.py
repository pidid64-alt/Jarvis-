"""CLI-интерфейс «Джарвиса»: чат, чтение страниц, MCP, память, Telegram, аудит."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from jarvis.config import get_settings
from jarvis.core.tools.base import ConfirmRequest
from jarvis.interfaces.runtime import Runtime, build_runtime, shutdown_runtime

app = typer.Typer(
    help="«Джарвис» — личный ИИ-ассистент (MVP: CLI, MCP, веб-парсер, Telegram read-only)",
    no_args_is_help=True,
)
memory_app = typer.Typer(help="Долговременная память: смотреть/править/удалять факты")
telegram_app = typer.Typer(help="Telegram-коннектор (ваш юзер-аккаунт)")
mcp_app = typer.Typer(help="MCP-серверы")
app.add_typer(memory_app, name="memory")
app.add_typer(telegram_app, name="telegram")
app.add_typer(mcp_app, name="mcp")

console = Console()

Path("data").mkdir(exist_ok=True)  # каталог для журнала
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("data/jarvis.log", encoding="utf-8")],
)
log = logging.getLogger("jarvis.cli")


def _setup_stdio() -> None:
    # Консоль Windows по умолчанию не UTF-8 — форсируем, чтобы русский текст не ломался.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


async def _confirmer(request: ConfirmRequest) -> bool:
    args_preview = json.dumps(request.args, ensure_ascii=False, indent=2)
    if len(args_preview) > 2000:
        args_preview = args_preview[:2000] + "\n…"
    console.print(
        Panel.fit(
            f"[bold yellow]Требуется подтверждение[/]\n"
            f"Инструмент: [bold]{request.tool.name}[/]\n"
            f"Причина: {request.reason}\n"
            f"Источник: {request.tool.source}\n\n{args_preview}",
            border_style="yellow",
        )
    )
    return Confirm.ask("Выполнить действие?", default=False)


def _observer(event: str, **details) -> None:
    tool = details.get("tool", "")
    if event == "start":
        console.print(f"  [dim]⚙ {tool}…[/]")
    elif event == "done":
        sec = details.get("seconds", 0)
        console.print(f"  [dim green]✓ {tool} ({sec:.1f} с)[/]")
    elif event == "denied":
        console.print(f"  [dim red]✗ {tool} — отклонено[/]")
    elif event == "error":
        console.print(f"  [dim red]✗ {tool} — {details.get('error', 'ошибка')}[/]")


async def _build(**kwargs) -> Runtime:
    # Общие настройки сборки для всех команд; CLI всегда подписывается на события
    # исполнения и подключает подтверждения действий.
    rt = await build_runtime(confirmer=_confirmer, **kwargs)
    rt.registry._observer = _observer
    return rt


def _print_mcp_statuses(rt: Runtime) -> None:
    if rt.mcp is None:
        console.print("MCP отключён в этой сессии.")
        return
    table = Table(title="MCP-серверы")
    table.add_column("Имя")
    table.add_column("Транспорт")
    table.add_column("Состояние")
    table.add_column("Инструментов", justify="right")
    for s in rt.mcp.statuses:
        color = {"connected": "green", "error": "red", "disabled": "dim"}.get(s.state, "white")
        state = s.state if not s.error else f"{s.state}: {s.error[:80]}"
        table.add_row(s.name, s.transport, f"[{color}]{state}[/]", str(s.tools))
    console.print(table)


# ================================ ЧАТ ================================


@app.command()
def chat():
    """Интерактивный чат с ассистентом (полный агент + инструменты)."""
    _setup_stdio()
    asyncio.run(_chat())


async def _chat() -> None:
    try:
        rt = await _build(with_llm=True)
    except Exception as e:
        console.print(f"[red]Не удалось запустить ассистента:[/] {e}")
        console.print("Проверьте .env (см. .env.example): ключ модели обязателен для чата.")
        raise typer.Exit(1)

    assert rt.agent is not None
    session_id = "cli-main"
    console.print(
        Panel.fit(
            f"Модель: [bold]{get_settings().llm_model}[/] ({get_settings().llm_provider})\n"
            f"Инструментов: {len(rt.registry.all())}\n"
            f"Telegram: {'подключён' if rt.telegram else 'нет'}\n"
            "Команды: /help, /tools, /reset, /mcp, /exit",
            title="🤖 Джарвис",
            border_style="cyan",
        )
    )
    try:
        while True:
            try:
                text = Prompt.ask("[bold cyan]Ты[/]").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text == "/exit":
                break
            if text == "/help":
                console.print("Команды: /help, /tools (список инструментов), /reset (новая сессия), /mcp (статусы и reload), /exit")
                continue
            if text == "/tools":
                _print_tools(rt)
                continue
            if text == "/reset":
                session_id = f"cli-{asyncio.get_running_loop().time():.0f}"
                console.print("[dim]Сессия диалога сброшена.[/]")
                continue
            if text == "/mcp":
                _print_mcp_statuses(rt)
                continue

            with console.status("[bold cyan]Джарвис думает…[/]"):
                answer = await rt.agent.handle(text, session_id)
            console.print(Panel(answer, title="[bold magenta]Джарвис[/]", border_style="magenta"))
    finally:
        await shutdown_runtime(rt)


@app.command()
def once(question: str):
    """Одиночный вопрос ассистенту без интерактива (удобно для проверки)."""
    _setup_stdio()
    asyncio.run(_once(question))


async def _once(question: str) -> None:
    try:
        rt = await _build(with_llm=True)
    except Exception as e:
        console.print(f"[red]Не удалось запустить ассистента:[/] {e}")
        raise typer.Exit(1)
    assert rt.agent is not None
    try:
        answer = await rt.agent.handle(question, "cli-once")
        console.print(Panel(answer, title="Джарвис", border_style="magenta"))
    finally:
        await shutdown_runtime(rt)


# ============================ ИНСТРУМЕНТЫ ============================


@app.command("tools")
def tools_list():
    """Список всех инструментов в реестре (встроенные + MCP)."""
    _setup_stdio()
    asyncio.run(_tools_list())


async def _tools_list() -> None:
    rt = await _build(with_llm=False)
    try:
        _print_tools(rt)
    finally:
        await shutdown_runtime(rt)


def _print_tools(rt: Runtime) -> None:
    table = Table(title=f"Реестр инструментов ({len(rt.registry.all())})")
    table.add_column("Имя")
    table.add_column("Класс")
    table.add_column("Источник")
    table.add_column("Описание", overflow="fold")
    for tool in sorted(rt.registry.all(), key=lambda t: t.name):
        color = {"read": "green", "write": "yellow", "outbound": "red", "high_stakes": "bold red"}.get(
            tool.side_effect, "white"
        )
        table.add_row(tool.name, f"[{color}]{tool.side_effect}[/]", tool.source, tool.description[:120])
    console.print(table)


# ================================ ВЕБ ================================


@app.command()
def fetch(
    url: str,
    max_chars: int = typer.Option(3000, "--max-chars", help="Лимит длины вывода"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Игнорировать кеш"),
    js: bool = typer.Option(False, "--js", help="Рендер через headless-браузер (динамические страницы)"),
):
    """Прочитать веб-страницу и показать извлечённый markdown (без модели)."""
    _setup_stdio()
    asyncio.run(_fetch(url, max_chars, no_cache, js))


async def _fetch(url: str, max_chars: int, no_cache: bool, js: bool = False) -> None:
    rt = await _build(with_llm=False)
    try:
        if js:
            html = await rt.browser.fetch_html(url)
            parser = rt.sites.pick(url)
            page = await parser.parse(html, url)
            text = parser.normalize(page, max_chars)
            title = f"{page.title or url} [браузер]"
        else:
            parser = rt.sites.pick(url)
            result = await parser.fetch(url)
            page = await parser.parse(result.html, result.url)
            text = parser.normalize(page, max_chars)
            title = f"{page.title or url} {'(кеш)' if result.from_cache else ''}"
        console.print(Panel(text, title=title))
    finally:
        await shutdown_runtime(rt)


# =============================== ПАМЯТЬ ===============================


@memory_app.command("list")
def memory_list(kind: str = typer.Option(None, "--kind", help="fact|contact|task|note")):
    """Все факты долгосрочной памяти."""
    _setup_stdio()
    asyncio.run(_memory_list(kind))


async def _memory_list(kind: str | None) -> None:
    rt = await _build(with_llm=False, with_mcp=False, with_telegram=False)
    try:
        facts = await rt.memory.list_facts(kind)
        if not facts:
            console.print("Память пуста.")
            return
        table = Table(title=f"Факты памяти ({len(facts)})")
        for col in ("id", "Вид", "Текст", "Обновлён"):
            table.add_column(col)
        for f in facts:
            table.add_row(str(f["id"]), f["kind"], f["text"][:150], str(f["updated_at"]))
        console.print(table)
    finally:
        await shutdown_runtime(rt)


@memory_app.command("show")
def memory_show(fact_id: int):
    """Показать факт целиком."""
    _setup_stdio()
    asyncio.run(_memory_show(fact_id))


async def _memory_show(fact_id: int) -> None:
    rt = await _build(with_llm=False, with_mcp=False, with_telegram=False)
    try:
        fact = await rt.memory.get_fact(fact_id)
        console.print(fact if fact else f"Факт {fact_id} не найден.")
    finally:
        await shutdown_runtime(rt)


@memory_app.command("edit")
def memory_edit(fact_id: int, text: str = typer.Option(..., "--text", help="Новый текст факта")):
    """Исправить текст факта."""
    _setup_stdio()
    asyncio.run(_memory_edit(fact_id, text))


async def _memory_edit(fact_id: int, text: str) -> None:
    rt = await _build(with_llm=False, with_mcp=False, with_telegram=False)
    try:
        await rt.memory.update_fact(fact_id, text)
        console.print(f"[green]Факт {fact_id} обновлён.[/]")
    finally:
        await shutdown_runtime(rt)


@memory_app.command("delete")
def memory_delete(fact_id: int):
    """Удалить факт (спросит подтверждение)."""
    _setup_stdio()
    asyncio.run(_memory_delete(fact_id))


async def _memory_delete(fact_id: int) -> None:
    rt = await _build(with_llm=False, with_mcp=False, with_telegram=False)
    try:
        fact = await rt.memory.get_fact(fact_id)
        if not fact:
            console.print(f"Факт {fact_id} не найден.")
            return
        console.print(f"[yellow]{fact['text'][:300]}[/]")
        if Confirm.ask("Удалить этот факт?", default=False):
            await rt.memory.delete_fact(fact_id)
            console.print("[green]Удалено.[/]")
    finally:
        await shutdown_runtime(rt)


# ============================== TELEGRAM ==============================


@telegram_app.command("login")
def telegram_login(
    force_sms: bool = typer.Option(False, "--sms", help="Прислать код СМС или звонком, а не в приложение"),
):
    """Интерактивный вход в Telegram (телефон + код из приложения/СМС)."""
    _setup_stdio()
    asyncio.run(_telegram_login(force_sms))


@telegram_app.command("bot")
def telegram_bot():
    """Запустить Telegram-бота управления (нужен JARVIS_TELEGRAM_BOT_TOKEN)."""
    _setup_stdio()
    from jarvis.interfaces.tg_bot import run_bot

    try:
        asyncio.run(run_bot())
    except SystemExit as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\nБот остановлен.")


async def _telegram_login(force_sms: bool = False) -> None:
    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        console.print("[red]Сначала задайте JARVIS_TELEGRAM_API_ID и JARVIS_TELEGRAM_API_HASH в .env[/]")
        console.print("Ключи создаются на https://my.telegram.org → API development tools.")
        raise typer.Exit(1)
    from telethon import TelegramClient

    session_dir = settings.data_dir / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(session_dir / "telegram.session"),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    # Телефон: из .env или спрашиваем вручную (формат обязателен: +79991234567).
    phone = settings.telegram_phone.strip() or None
    if phone is None:
        console.print("Телефон не задан в .env (JARVIS_TELEGRAM_PHONE) — введу вручную.")
        phone = Prompt.ask("Номер телефона аккаунта Telegram", default="").strip() or None
    if phone is None:
        console.print("[red]Без номера телефона войти нельзя. Попробуйте ещё раз.[/]")
        raise typer.Exit(1)
    if not phone.startswith("+"):
        console.print(f"[yellow]Похоже, нет «+» в начале ({phone}). Добавляю.[/]")
        phone = "+" + phone.lstrip("+").strip()

    console.print(
        f"Отправляю код подтверждения на {phone} "
        f"({'СМС/звонок' if force_sms else 'в приложение Telegram, чат от сервиса «Telegram»'})…"
    )
    try:
        await client.start(phone=phone, force_sms=force_sms)
    except Exception as e:
        console.print(f"[red]Вход не удался:[/] {type(e).__name__}: {e}")
        console.print("Проверьте номер (с «+» и кодом страны) и попробуйте ещё раз.")
        raise typer.Exit(1)

    me = await client.get_me()
    name = " ".join(part for part in (me.first_name, me.last_name or "") if part)
    console.print(f"[green]Готово:[] вошли как {name} (@{getattr(me, 'username', None)})")
    console.print("Сессия сохранена в data/sessions/telegram.session — теперь доступно чтение.")
    await client.disconnect()


@telegram_app.command("unread")
def telegram_unread(limit_dialogs: int = typer.Option(30)):
    """Сводка непрочитанного в Telegram (проверка коннектора без модели)."""
    _setup_stdio()
    asyncio.run(_telegram_unread(limit_dialogs))


async def _telegram_unread(limit_dialogs: int) -> None:
    rt = await _build(with_llm=False, with_mcp=False)
    try:
        if rt.telegram is None:
            console.print("[red]Telegram не подключён.[/] Выполните `jarvis telegram login`.")
            raise typer.Exit(1)
        console.print(Panel(await rt.telegram.unread_summary(limit_dialogs), title="Telegram: непрочитанное"))
    finally:
        await shutdown_runtime(rt)


# ================================ MCP ================================


@mcp_app.command("list")
def mcp_list():
    """Подключить MCP-серверы из конфига и показать статус."""
    _setup_stdio()
    asyncio.run(_mcp_list())


async def _mcp_list() -> None:
    rt = await _build(with_llm=False, with_telegram=False)
    try:
        _print_mcp_statuses(rt)
    finally:
        await shutdown_runtime(rt)


# =============================== АУДИТ ===============================


@app.command()
def audit(limit: int = typer.Option(30, "--limit", "-n")):
    """Журнал последних действий агента."""
    _setup_stdio()
    asyncio.run(_audit(limit))


async def _audit(limit: int) -> None:
    rt = await _build(with_llm=False, with_mcp=False, with_telegram=False)
    try:
        rows = await rt.audit.recent(limit)
        if not rows:
            console.print("Журнал пуст.")
            return
        from datetime import datetime

        table = Table(title=f"Журнал действий (последние {len(rows)})")
        for col in ("Время", "Инструмент", "Решение", "Статус", "Ошибка", "Подтвердил"):
            table.add_column(col)
        for r in rows:
            ts = datetime.fromtimestamp(r["ts"]).strftime("%d.%m %H:%M:%S")
            table.add_row(
                ts, r["tool"], r["decision"], r["status"], (r["error"] or "")[:60], r["confirmed_by"] or ""
            )
        console.print(table)
    finally:
        await shutdown_runtime(rt)


if __name__ == "__main__":
    app()
