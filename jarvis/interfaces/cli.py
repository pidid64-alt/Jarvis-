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
