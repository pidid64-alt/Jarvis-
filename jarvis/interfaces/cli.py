"""Командная строка Jarvis: одна точка входа для всего.

    jarvis                 запустить ассистента (демон: хоткей + слово + автопроверки)
    jarvis ask "текст"     задать вопрос текстом
    jarvis listen          одна голосовая команда
    jarvis status          что запущено и что настроено
    jarvis skills          список навыков, включение и выключение
    jarvis logs            журнал событий (можно скопировать для отчёта)
    jarvis config          где лежит конфиг и что в нём
    jarvis secret set X    сохранить секрет в .env (значение не печатается)
    jarvis migrate         перенести данные старой версии
    jarvis trigger         разбудить запущенный демон (используется хоткеем)
    jarvis mcp             серверы MCP: что подключено и какие есть инструменты
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .. import __version__
from ..core import paths
from ..core.errors import JarvisError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis", description="Jarvis — локальный ассистент")
    parser.add_argument("--debug", action="store_true", help="подробный лог в консоль")
    parser.add_argument("--version", action="version", version=f"Jarvis {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="запустить ассистента (демон)")
    run.add_argument("--no-voice", action="store_true", help="без микрофона (только хоткей и автопроверки)")
    run.add_argument("--no-autonomy", action="store_true", help="без автономных проверок")

    ask = sub.add_parser("ask", help="спросить текстом")
    ask.add_argument("text", help="текст запроса")
    ask.add_argument("--no-speak", action="store_true")
    ask.add_argument("--yes", action="store_true", help="подтверждать опасные действия без вопроса")

    sub.add_parser("listen", help="одна голосовая команда и выход")
    sub.add_parser("trigger", help="разбудить запущенный демон")
    sub.add_parser("status", help="состояние ассистента")
    sub.add_parser("version", help="версия")

    skills = sub.add_parser("skills", help="навыки")
    skills.add_argument("action", nargs="?", default="list",
                        choices=["list", "info", "enable", "disable"])
    skills.add_argument("skill_id", nargs="?")

    logs = sub.add_parser("logs", help="журнал событий")
    logs.add_argument("--limit", type=int, default=50)
    logs.add_argument("--level", choices=["debug", "info", "warning", "error"])
    logs.add_argument("--search", default="")
    logs.add_argument("--report", action="store_true", help="текст для отчёта (секреты вырезаны)")

    config = sub.add_parser("config", help="настройки")
    config.add_argument("action", nargs="?", default="path", choices=["path", "show", "get"])
    config.add_argument("key", nargs="?")

    secret = sub.add_parser("secret", help="секреты (значения не выводятся)")
    secret.add_argument("action", choices=["set", "list"])
    secret.add_argument("name", nargs="?")

    migrate = sub.add_parser("migrate", help="перенести данные старой версии")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--force", action="store_true", help="перезаписать config.toml")

    sub.add_parser("autonomy", help="прогнать автономные проверки один раз")

    llm = sub.add_parser("llm", help="показать запрос к модели (и по желанию отправить)")
    llm.add_argument("--text", default="", help="фраза для примера (по умолчанию «привет»)")
    llm.add_argument("--send", action="store_true", help="действительно отправить и замерить время")

    mcp = sub.add_parser("mcp", help="серверы MCP: список, инструменты, проверка вызова")
    mcp.add_argument("--tools", action="store_true", help="показать инструменты серверов")
    mcp.add_argument("--json", action="store_true", help="вывести состояние как JSON")
    mcp.add_argument("--reload", action="store_true", help="перечитать файл настроек MCP")
    mcp.add_argument("--call", default="", help="проверить вызов: имя.инструмент")
    mcp.add_argument("--arg", action="append", default=[], metavar="КЛЮЧ=ЗНАЧЕНИЕ",
                     help="аргумент для --call (можно несколько раз)")

    gui = sub.add_parser("gui", help="открыть окно Jarvis (браузер в режиме приложения)")
    gui.add_argument("--tab", action="store_true", help="открыть обычной вкладкой, без режима приложения")
    gui.add_argument("--browser", default="", help="какой браузер использовать (edge, chrome, chromium…)")
    gui.add_argument("--no-daemon", action="store_true", help="не поднимать ядро, только открыть окно")
    gui.add_argument("--voice", action="store_true", help="включить микрофон у поднятого ядра")
    gui.add_argument("--print-url", action="store_true", help="только напечатать адрес окна")
    return parser


# --------------------------------------------------------------------- helpers


def _assistant(debug: bool = False, *, speak: bool = False):
    from ..core.assistant import create_assistant

    return create_assistant(debug=debug)


def _print_reply(reply) -> None:
    print(reply.text or "")
    if not reply.ok and reply.error:
        print(f"(код: {reply.error})", file=sys.stderr)


# ----------------------------------------------------------------------- команды


def cmd_ask(args) -> int:
    assistant = _assistant(args.debug)
    confirm = (lambda question: True) if args.yes else _console_confirm
    reply = assistant.handle_text(args.text, source="cli", speak=not args.no_speak,
                                  confirm_callback=confirm)
    _print_reply(reply)
    assistant.shutdown()
    return 0 if reply.ok else 1


def cmd_listen(args) -> int:
    assistant = _assistant(args.debug)
    reply = assistant.handle_voice(confirm_callback=_console_confirm, source="cli")
    _print_reply(reply)
    assistant.shutdown()
    return 0 if reply.ok else 1


def _console_confirm(question: str) -> bool:
    try:
        answer = input(f"{question} [да/нет]: ").strip().casefold()
    except EOFError:
        return False
    return answer in {"да", "д", "yes", "y", "ага", "угу", "подтверждаю"}


def cmd_run(args) -> int:
    from .daemon import Daemon

    assistant = _assistant(args.debug)
    daemon = Daemon(assistant, autonomy=not args.no_autonomy, voice=not args.no_voice)
    hotkey = assistant.config.get("hotkey.spec", "Super+J")
    print(f"Jarvis {__version__} запущен. Горячая клавиша: {hotkey}. "
          f"Автопроверки: {'вкл' if not args.no_autonomy else 'выкл'}. Ctrl+C — остановить.")
    daemon.run()
    return 0


def cmd_trigger(args) -> int:
    from .daemon import daemon_running, fire_trigger

    if not daemon_running():
        print("Jarvis не запущен. Запустите: jarvis run", file=sys.stderr)
        return 1
    fire_trigger()
    return 0


def cmd_status(args) -> int:
    from .daemon import daemon_running

    assistant = _assistant(args.debug)
    status = assistant.status()
    print(f"Jarvis {__version__}")
    print(f"Демон: {'запущен' if daemon_running() else 'не запущен'}")
    print(f"Язык: {status['language']}, режим прав: {status['permissions_mode']}")
    print(f"Навыков: {status['skills']['total']} (включено {status['skills']['enabled']})")
    if status["skills"]["problems"]:
        print("Проблемы с навыками:")
        for problem in status["skills"]["problems"]:
            print(f"  - {problem}")
    print(f"Голос: {'включён' if status['voice_enabled'] else 'выключен'}")
    for name, state in status["providers"].items():
        if isinstance(state, dict):
            if "available" in state:
                mark = "ок" if state["available"] else "нет"
                print(f"  {name}: {mark} — {state.get('reason', '')}")
            elif "running" in state:
                mark = "работает" if state["running"] else "остановлен"
                print(f"  {name}: {mark}")
    print(f"Конфиг: {status['config_path']}")
    print(f"Данные: {status['state_dir']}")
    assistant.shutdown()
    return 0


def cmd_skills(args) -> int:
    assistant = _assistant(args.debug)
    if args.action == "list":
        for skill in assistant.skills_public():
            mark = "вкл " if skill["enabled"] else "выкл"
            print(f"[{mark}] {skill['id']:<18} {skill['name']} — {skill['description']}")
        print(f"\nВсего: {len(assistant.skills_public())}")
        return 0
    if not args.skill_id:
        print("Укажите id навыка: jarvis skills info <id>", file=sys.stderr)
        return 2
    if args.action == "info":
        skill = next((item for item in assistant.skills_public() if item["id"] == args.skill_id), None)
        if skill is None:
            print(f"Навык {args.skill_id} не найден", file=sys.stderr)
            return 1
        print(json.dumps(skill, ensure_ascii=False, indent=2))
        return 0
    enabled = args.action == "enable"
    try:
        skill = assistant.set_skill_enabled(args.skill_id, enabled)
    except JarvisError as exc:
        print(f"Не получилось: {exc}", file=sys.stderr)
        return 1
    print(f"Навык {skill['id']} {'включён' if enabled else 'выключен'}")
    return 0


def cmd_logs(args) -> int:
    assistant = _assistant(args.debug)
    if args.report:
        print(assistant.report(limit=args.limit * 6))
        return 0
    import time

    for record in assistant.logs(limit=args.limit, level=args.level, search=args.search):
        stamp = time.strftime("%H:%M:%S", time.localtime(record.get("ts", 0)))
        print(f"{stamp} [{record.get('level', 'info'):<7}] {record.get('source', '?'):<16} "
              f"{record.get('message', '')}")
    return 0


def cmd_config(args) -> int:
    from ..core.config import Config

    if args.action == "path":
        print(paths.config_path())
        return 0
    config = Config.load()
    if args.action == "show":
        print(json.dumps(config.data, ensure_ascii=False, indent=2))
        return 0
    if args.action == "get":
        if not args.key:
            print("Укажите ключ: jarvis config get llm.model", file=sys.stderr)
            return 2
        value = config.get(args.key)
        print(json.dumps(value, ensure_ascii=False) if value is not None else "")
        if config.missing_env:
            print(f"(не заданы переменные окружения: {', '.join(config.missing_env)})", file=sys.stderr)
        return 0
    return 0


def cmd_secret(args) -> int:
    from ..core import secrets

    if args.action == "list":
        config = None
        from ..core.config import Config

        config = Config.load(create=False)
        names = sorted(set(config.expected_env) | set(secrets.parse_env(
            paths.env_file_path().read_text(encoding="utf-8") if paths.env_file_path().exists() else "")))
        if not names:
            print("Секретов не задано.")
            return 0
        values = secrets.parse_env(paths.env_file_path().read_text(encoding="utf-8")) \
            if paths.env_file_path().exists() else {}
        for name in names:
            value = values.get(name, "")
            mark = f"задан ({secrets.mask_secret(value)})" if value else "не задан"
            print(f"{name}: {mark}")
        print(f"Файл: {paths.env_file_path()} (права 600)")
        return 0

    if not args.name:
        print("Укажите имя переменной: jarvis secret set JARVIS_LLM_KEY", file=sys.stderr)
        return 2
    try:
        value = getpass.getpass(f"Значение {args.name} (не отображается): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nОтменено.", file=sys.stderr)
        return 1
    if not value:
        print("Пустое значение — не сохраняю.", file=sys.stderr)
        return 1
    secrets.save_env_var(args.name, value)
    print(f"Сохранено в {paths.env_file_path()} (в конфиге ссылка ${{{args.name}}})")
    return 0


def cmd_migrate(args) -> int:
    from ..core.migrate import migrate

    report = migrate(dry_run=args.dry_run, force=args.force)
    for line in report.lines():
        print(line)
    if report.dry_run:
        print("\nЭто пробный прогон: файлы не изменялись.")
    else:
        print("\nГотово. Проверьте: jarvis status")
    return 0


def cmd_autonomy(args) -> int:
    from ..core.autonomy import Autonomy

    assistant = _assistant(args.debug)
    autonomy = Autonomy(assistant.config, assistant.providers, assistant.journal)
    outcomes = autonomy.tick(force=True)
    if not outcomes:
        print("Проблем не обнаружено.")
    for outcome in outcomes:
        print(f"{outcome.rule_id}: {outcome.text}")
    assistant.shutdown()
    return 0


def cmd_llm(args) -> int:
    """Показать (и по желанию отправить) запрос к модели — целиком, без секретов."""
    import time as _time
    from datetime import datetime

    from ..core.planner import build_messages

    assistant = _assistant(args.debug)
    providers = assistant.providers
    state = providers.llm.state()

    print("Модель")
    print(f"  сервис:  {state['base_url'] or '— не задан —'}")
    print(f"  модель:  {state['model'] or '— не задана —'}")
    print(f"  ключ:    {'задан, ' + state['key_masked'] if state['key_present'] else 'не задан'}")
    print(f"  ожидание ответа: {providers.llm.timeout:.0f} с")
    if not state["available"]:
        print(f"  состояние: не готов — {state['reason']}")

    text = (args.text or "привет").strip()
    skills = assistant.registry.enabled(include_hidden=False)

    # Сначала проверяем то же, что и ассистент при работе: совпала ли фраза с
    # навыком. Если да — модель вообще не спрашивают, и объяснять тут нечего.
    from ..platform import platform_name as _platform_name

    local = assistant.router.route(text, platform=_platform_name(), allow_llm=False)
    if local.handled_by in {"exact", "fuzzy"} and local.intents:
        intent = local.intents[0]
        print()
        print("Навык")
        print(f"  фраза «{text}» разобрана локально: {intent.full_id} "
              f"({intent.action.description or intent.action.id})")
        print("  модели этот запрос НЕ отправляется: навык справляется сам.")
        print("  Чтобы посмотреть запрос к модели, возьмите фразу, которой нет у навыков, "
              "например:")
        print('    python -m jarvis llm --text "напиши стих про кота"')
        assistant.shutdown()
        return 0
    messages = build_messages(
        skills, text,
        history_tail=[], pending=None,
        now_str=datetime.now().strftime("%Y-%m-%d %H:%M"),
        assistant_name=str(assistant.config.get("assistant.name", "Jarvis")),
        language=str(assistant.config.get("assistant.language", "ru")),
        extra_rules=str(assistant.config.get("llm.extra_rules", "")),
    )
    payload = {
        "model": state["model"] or "<модель из настроек>",
        "messages": messages,
        "max_tokens": int(assistant.config.get("llm.max_tokens", 400)),
        "temperature": float(assistant.config.get("llm.temperature", 0.2)),
        "response_format": {"type": "json_object"},
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    chars = sum(len(item["content"]) for item in messages)

    print()
    print("Запрос")
    print(f"  адрес:   POST {state['base_url']}/chat/completions")
    print(f"  фраза:   «{text}»")
    print(f"  действий в списке: {sum(len(skill.actions) for skill in skills)} "
          f"(навыков: {len(skills)})")
    print(f"  размер:  {chars} символов текста, {len(body.encode('utf-8'))} байт JSON")
    print(f"  оценка:  ~{chars // 3} токенов промпта (по-русски это примерно "
          f"{chars // 4}–{chars // 3} токенов)")
    print("  заголовки: Authorization: Bearer <ключ не показываем>, "
          "Content-Type: application/json")
    print()
    print("Тело запроса (то, что увидит в своём журнале локальная модель):")
    print(body)

    if not args.send:
        print()
        print("Запрос не отправлен. Добавьте --send, чтобы проверить ответ и время.")
        assistant.shutdown()
        return 0

    print()
    print(f"Отправляю и жду до {providers.llm.timeout:.0f} с…")
    started = _time.monotonic()
    try:
        answer = providers.llm.chat(messages)
    except JarvisError as exc:
        elapsed = _time.monotonic() - started
        print(f"Ошибка через {elapsed:.1f} с: {exc}")
        if getattr(exc, "details", ""):
            print(f"  подсказка: {exc.details}")
        assistant.shutdown()
        return 1
    elapsed = _time.monotonic() - started
    print(f"Ответ за {elapsed:.1f} с:")
    print(answer)
    _explain_llm_answer(assistant, answer)
    assistant.shutdown()
    return 0


def _explain_llm_answer(assistant, answer: str) -> None:
    """Разбирает ответ модели теми же правилами, что и ядро, и говорит, что будет.

    Ответ глазами человека — это просто JSON; здесь он превращается в понятную
    строку: «ответит словами», «выполнит действие», «уточнит», «поищет». Заодно
    видно, когда модель выдумала действие: такие пункты ядро отбросит, и об этом
    честно сообщается.
    """
    from ..core.planner import parse_actions

    print()
    print("Что сделает Jarvis по этому ответу:")
    try:
        planned = parse_actions(answer, assistant.registry.flat_action_ids())
    except JarvisError as exc:
        print(f"  разобрать не удалось: {exc} — будет ответ «не понял»")
        return
    if not planned:
        print("  ничего: ответ пуст или действия недопустимы (будет «не понял»)")
        return
    for item in planned:
        if item.kind == "speak":
            print(f"  ответит словами: «{item.text}»")
        elif item.kind == "ask":
            print(f"  спросит уточнение: «{item.text}»")
        elif item.kind == "search":
            print(f"  поищет в интернете: «{item.query}»"
                  + (" и откроет браузер" if item.open_browser else ""))
        elif item.kind == "run":
            found = assistant.registry.find_action(item.action_id or "")
            title = found[1].description if found else ""
            note = " (с подтверждением)" if item.confirmation else ""
            print(f"  выполнит действие {item.action_id}: {title}{note}")
    print("  Записанные команды модель не выбирает: она называет только id, "
          "остальное решает ядро.")


def cmd_mcp(args) -> int:
    """Серверы MCP: что настроено, какие инструменты и проверка вызова.

    Никаких ключей в вывод не попадает: показываются только имена серверов,
    команды (без значений окружения) и названия инструментов.
    """
    from ..core.mcp import ensure_config_file

    path = ensure_config_file()
    assistant = _assistant(args.debug)
    mcp = assistant.mcp
    if args.reload:
        mcp.reload()

    if args.json:
        if args.tools or args.call:
            mcp.discover()
        print(json.dumps(mcp.status(), ensure_ascii=False, indent=2))
        assistant.shutdown()
        return 0

    print("Серверы MCP")
    print(f"  файл настроек: {path}")
    if not mcp.servers():
        print("  пока пусто — откройте файл и добавьте сервер (в нём есть примеры)")
        assistant.shutdown()
        return 0

    for server in mcp.servers():
        state = "включён" if server.enabled else "выключен"
        print(f"  {server.name} ({server.kind}, {state})")
        print(f"    {server.summary()}")
        if server.description:
            print(f"    {server.description}")
    if mcp.missing_env:
        print(f"  не заданы переменные окружения: {', '.join(mcp.missing_env)}")

    if args.call:
        if "." not in args.call:
            print("Укажите инструмент как сервер.инструмент, например files.read_file")
            assistant.shutdown()
            return 2
        server_name, tool_name = args.call.split(".", 1)
        arguments: dict[str, str] = {}
        for item in args.arg:
            if "=" not in item:
                print(f"Аргумент «{item}» должен быть вида ключ=значение", file=sys.stderr)
                assistant.shutdown()
                return 2
            key, value = item.split("=", 1)
            arguments[key.strip()] = value
        print()
        print(f"Вызываю {server_name}.{tool_name}"
              + (f" с аргументами: {arguments}" if arguments else ""))
        try:
            result = mcp.call(server_name, tool_name, arguments)
        except JarvisError as exc:
            print(f"Не получилось: {exc}", file=sys.stderr)
            assistant.shutdown()
            return 1
        print(result[:4000])
        assistant.shutdown()
        return 0

    if not any(server.enabled for server in mcp.servers()):
        print()
        print("Все серверы выключены: поставьте enabled = true тому, который нужен.")
        assistant.shutdown()
        return 0

    print()
    print("Подключаюсь и читаю список инструментов…")
    tools = mcp.discover()
    if not tools:
        print("  инструментов не видно")
        for name, reason in mcp.problems.items():
            print(f"  {name}: {reason}")
        assistant.shutdown()
        return 1
    grouped: dict[str, list] = {}
    for tool in tools:
        grouped.setdefault(tool.server, []).append(tool)
    for name, items in grouped.items():
        print(f"  {name}: инструментов {len(items)}")
        for tool in items:
            marks = []
            if tool.required:
                marks.append("нужны аргументы: " + ", ".join(tool.required))
            if tool.destructive:
                marks.append("меняет данные")
            elif tool.read_only:
                marks.append("только чтение")
            tail = f" ({'; '.join(marks)})" if marks else ""
            print(f"    - {tool.name}: {(tool.description or '').strip()[:100]}{tail}")
    print()
    print(f"Вызвать инструмент: jarvis mcp --call {tools[0].action_id}"
          + (f" --arg {tools[0].required[0]}=значение" if tools[0].required else ""))
    assistant.shutdown()
    return 0


def cmd_gui(args) -> int:
    """Открыть окно Jarvis (локальный интерфейс в отдельном окне браузера)."""
    from .webapp import open_ui

    if getattr(args, "print_url", False):
        from .client import read_target

        target = read_target(verify=True)
        if target is None:
            print("Jarvis не запущен. Запустите: jarvis run", file=sys.stderr)
            return 1
        print(f"{target.url}/ui/?token={target.token}")
        return 0
    return open_ui(preferred=getattr(args, "browser", "") or "",
                   debug=getattr(args, "debug", False),
                   start=not getattr(args, "no_daemon", False),
                   app_mode=not getattr(args, "tab", False),
                   voice=getattr(args, "voice", False))


COMMANDS = {
    "run": cmd_run,
    "ask": cmd_ask,
    "listen": cmd_listen,
    "trigger": cmd_trigger,
    "status": cmd_status,
    "skills": cmd_skills,
    "logs": cmd_logs,
    "config": cmd_config,
    "secret": cmd_secret,
    "migrate": cmd_migrate,
    "autonomy": cmd_autonomy,
    "llm": cmd_llm,
    "mcp": cmd_mcp,
    "gui": cmd_gui,
}


def cmd_version(args) -> int:
    print(f"Jarvis {__version__}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # «jarvis» без аргументов — запуск ассистента
        args = parser.parse_args(["run"])
    if args.command == "version":
        return cmd_version(args)
    handler = COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return handler(args)
    except JarvisError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nОстановлено.", file=sys.stderr)
        return 130
