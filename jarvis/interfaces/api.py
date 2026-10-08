"""Локальный API: единственная дверь между ядром и интерфейсами.

Ядро (``jarvis.core``) ничего не знает про окно, трей или скрипты. Всё общение
идёт через этот небольшой HTTP-сервер на ``127.0.0.1``:

* живёт только на localhost и требует токен из файла с правами 600;
* отдаёт состояние, навыки, настройки, журнал и историю;
* принимает запросы (``/ask``, ``/voice``), в том числе требующие подтверждения:
  опасное действие не выполняется, пока интерфейс не подтвердит его отдельным
  запросом ``/confirm``.

Так интерфейс можно заменить целиком: окно на Tkinter, трей, Telegram-бот или
скрипт говорят с ядром одинаково. Зависимостей нет — только стандартная
библиотека.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets as py_secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .. import __version__
from ..core import paths
from ..core.errors import JarvisError
from ..core.i18n import get_translator
from ..core.logging_setup import get_logger

log = get_logger("interfaces.api")

MAX_BODY = 256 * 1024          # больше мегабайта запросов от окна не бывает
TASK_TTL = 600.0               # сколько храним завершённые задачи
MAX_TASKS = 50
FIRST_WAIT = 0.7               # сколько ждём ответ или вопрос о подтверждении
CONFIRM_WAIT = 2.0             # сколько ждём итог после решения пользователя

#: ключи, значения которых никогда не покидают ядро
SECRET_KEY_HINTS = ("api_key", "apikey", "token", "password", "secret", "passwd")


class ApiError(Exception):
    """Ошибка запроса: код для интерфейса и текст для человека."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class Task:
    """Один запрос пользователя, выполняемый в отдельном потоке."""

    id: str
    text: str
    source: str
    created: float = field(default_factory=time.time)
    question: str = ""
    approved: bool = False
    reply: dict[str, Any] | None = None
    error: str = ""
    decision: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)

    @property
    def asked(self) -> bool:
        return bool(self.question) and not self.decision.is_set()


class LocalApi:
    """HTTP-сервер поверх ассистента. Запускается демоном или окном."""

    def __init__(self, assistant, *, host: str | None = None, port: int | None = None,
                 token: str | None = None, on_shutdown: Callable[[], None] | None = None):
        self.assistant = assistant
        self.host = host or str(self.config.get("api.host", "127.0.0.1"))
        configured_port = self.config.get("api.port", 0)
        self.port = int(port if port is not None else configured_port or 0)
        self.token = token or py_secrets.token_urlsafe(24)
        self.confirm_timeout = float(self.config.get("api.confirm_timeout_seconds", 60))
        self.on_shutdown = on_shutdown

        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._tasks: dict[str, Task] = {}
        self._tasks_lock = threading.RLock()
        self._client_lock = threading.RLock()
        self._last_client = 0.0      # когда окно последний раз обращалось к ядру
        self._demo = bool(os.environ.get("JARVIS_DEMO_UI"))

    # ------------------------------------------------------------------ start
    def start(self) -> dict[str, Any]:
        """Поднимает сервер и записывает файлы обнаружения. Возвращает описание."""
        if self._server is not None:
            return self.info()
        paths.ensure_dirs()
        handler = _make_handler(self)
        try:
            server = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as exc:
            raise ApiError(500, "bind_failed",
                           f"не удалось занять порт {self.port} на {self.host}: {exc}") from exc
        server.daemon_threads = True
        self._server = server
        self.port = server.server_address[1]
        self._write_discovery()
        self._thread = threading.Thread(target=server.serve_forever, name="jarvis-api", daemon=True)
        self._thread.start()
        log.info("локальный API слушает %s (токен в %s)", self.url, self.token_path)
        self.assistant.events.publish("api_started", url=self.url)
        return self.info()

    def stop(self) -> None:
        if self._server is None:
            return
        self.assistant.events.publish("api_stopped")
        try:
            self._server.shutdown()
        except Exception:  # noqa: BLE001 - сервер мог уже остановиться
            log.debug("остановка API: сервер уже не отвечает", exc_info=True)
        self._server.server_close()
        self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        for path in (self._discovery_path, self.token_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                log.debug("не удалось убрать файл %s", path, exc_info=True)

    @property
    def config(self):
        """Настройки живого ассистента: ``refresh()`` заменяет объект конфига."""
        return self.assistant.config

    # ------------------------------------------------------------------- info
    def note_client(self) -> None:
        """Отметить, что интерфейс жив (по любому успешному запросу)."""
        self._last_client = time.time()

    def client_active(self, *, within: float = 10.0) -> bool:
        """Открыто ли окно прямо сейчас (обращалось ли оно за последние секунды)."""
        return bool(self._last_client) and (time.time() - self._last_client) <= within

    def ui_url(self) -> str:
        """Адрес интерфейса вместе с токеном — его открывает браузер."""
        return f"{self.url}/ui/?token={self.token}"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def token_path(self) -> Path:
        configured = str(self.config.get("api.token_file", "") or "")
        if "{" in configured:  # нераскрытая подстановка — пишем туда, где безопасно
            log.warning("api.token_file содержит подстановку (%s), беру путь по умолчанию", configured)
            return paths.token_file()
        return Path(configured) if configured else paths.token_file()

    @property
    def _discovery_path(self) -> Path:
        return paths.api_file()

    def info(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "host": self.host,
            "port": self.port,
            "url": self.url,
            "pid": os.getpid(),
            "version": __version__,
            "token_file": str(self.token_path),
            "confirm_timeout_seconds": self.confirm_timeout,
        }

    def _write_discovery(self) -> None:
        """Пишет токен (0600) и файл обнаружения: интерфейсы находят ядро сами."""
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        _write_private(self.token_path, self.token + "\n")
        _write_private(self._discovery_path, json.dumps(self.info(), ensure_ascii=False, indent=2) + "\n")

    # --------------------------------------------------------------- handlers
    def handle(self, method: str, path: str, query: dict[str, list[str]],
               payload: dict[str, Any]) -> dict[str, Any]:
        """Разбор маршрута. Вызывается обработчиком HTTP, тестами — напрямую."""
        if method == "GET":
            return self._get(path, query)
        if method == "POST":
            return self._post(path, payload)
        raise ApiError(405, "method_not_allowed", f"метод {method} не поддерживается")

    # -------------------------------------------------------------------- GET
    def _get(self, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        assistant = self.assistant
        if path in ("/health", "/"):
            return {"state": "done", "reply": None, "version": __version__,
                    "status": assistant.status(), "api": self.info()}
        if path == "/status":
            return {"state": "done", "status": assistant.status(), "api": self.info()}
        if path == "/skills":
            return {"state": "done", "skills": assistant.skills_public()}
        if path == "/config":
            return {"state": "done", "config": self._public_config(),
                    "env": self._public_env(), "path": str(paths.config_path())}
        if path == "/history":
            limit = _int(query, "limit", 50, 1, 500)
            return {"state": "done", "history": assistant.history(limit=limit)}
        if path == "/journal":
            limit = _int(query, "limit", 100, 1, 1000)
            level = _first(query, "level") or None
            search = _first(query, "search")
            entries = assistant.logs(limit=limit, level=level, search=search)
            return {"state": "done", "entries": entries, "lines": [_format_log(item) for item in entries]}
        if path == "/journal/report":
            limit = _int(query, "limit", 300, 10, 2000)
            return {"state": "done", "report": _redact_text(assistant.report(limit=limit))}
        if path == "/events":
            since = _int(query, "since", 0, 0, 10 ** 9)
            limit = _int(query, "limit", 100, 1, 500)
            return {"state": "done", "events": assistant.events.recent(after_seq=since, limit=limit)}
        if path == "/ask/result":
            task_id = _first(query, "pending_id") or _first(query, "id")
            return self._task_state(task_id)
        if path == "/languages":
            from ..core.i18n import get_translator

            return {"state": "done", "languages": get_translator().available_languages()}
        raise ApiError(404, "not_found", f"неизвестный путь {path}")

    # ------------------------------------------------------------------- POST
    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        assistant = self.assistant
        if path == "/ask":
            text = str(payload.get("text", "")).strip()
            if not text:
                raise ApiError(400, "empty_text", "пустой запрос")
            return self._start_task(text, source=str(payload.get("source", "gui")),
                                    speak=bool(payload.get("speak", False)), voice=False)
        if path == "/voice":
            return self._start_task("", source=str(payload.get("source", "gui")),
                                    speak=bool(payload.get("speak", True)), voice=True)
        if path == "/confirm":
            return self._confirm(payload)
        if path == "/skills/toggle":
            skill_id = str(payload.get("id", "")).strip()
            if not skill_id:
                raise ApiError(400, "no_skill", "не указан навык")
            try:
                skill = assistant.set_skill_enabled(skill_id, bool(payload.get("enabled", True)))
            except KeyError as exc:
                raise ApiError(404, "unknown_skill", f"навык {skill_id} не найден") from exc
            return {"state": "done", "skill": skill}
        if path == "/config":
            return self._set_config(payload)
        if path == "/secret":
            return self._set_secret(payload)
        if path == "/trigger":
            fired = False
            try:
                from .daemon import fire_trigger

                fired = fire_trigger()
            except Exception:  # noqa: BLE001 - демона может не быть
                log.debug("не удалось разбудить демон", exc_info=True)
            return {"state": "done", "fired": fired}
        if path == "/shutdown":
            if self.on_shutdown is not None:
                threading.Thread(target=self._shutdown_soon, name="jarvis-shutdown", daemon=True).start()
            return {"state": "done", "stopping": self.on_shutdown is not None}
        raise ApiError(404, "not_found", f"неизвестный путь {path}")

    def _shutdown_soon(self) -> None:
        time.sleep(0.2)  # даём ответу уйти в сеть
        try:
            self.on_shutdown()
        except Exception:  # noqa: BLE001
            log.exception("ошибка остановки по запросу интерфейса")

    # -------------------------------------------------------------- задачи
    def _start_task(self, text: str, *, source: str, speak: bool, voice: bool) -> dict[str, Any]:
        task = Task(id=uuid.uuid4().hex[:16], text=text, source=source)
        with self._tasks_lock:
            self._purge_tasks()
            self._tasks[task.id] = task
        thread = threading.Thread(target=self._run_task, args=(task, speak, voice),
                                  name=f"jarvis-task-{task.id[:6]}", daemon=True)
        thread.start()
        # Ждём либо готовый ответ, либо вопрос о подтверждении — чтобы интерфейс
        # не опрашивал сервер лишний раз в обычном случае.
        task.done.wait(timeout=FIRST_WAIT)
        return self._task_state(task.id)

    def _run_task(self, task: Task, speak: bool, voice: bool) -> None:
        assistant = self.assistant

        def confirm(question: str) -> bool:
            task.question = question
            if not task.decision.wait(timeout=self.confirm_timeout):
                task.question = question + " (время ожидания истекло)"
                return False
            return bool(task.approved)

        try:
            if voice:
                reply = assistant.handle_voice(confirm_callback=confirm, source=task.source)
            else:
                reply = assistant.handle_text(task.text, source=task.source,
                                              confirm_callback=confirm, speak=speak)
            task.reply = reply.to_dict()
        except JarvisError as exc:
            # Понятный отказ ядра: показываем его текст, а не «внутренняя ошибка».
            log.warning("задача %s отклонена: %s", task.id, exc)
            assistant.journal.error("api", f"запрос не выполнен: {exc}")
            task.error = str(exc)
            task.reply = {"text": get_translator(assistant.language).t(exc.user_message_key),
                          "ok": False, "error": str(exc), "state": "error", "skill": None,
                          "action": None, "continue_dialog": False, "await_confirmation": False,
                          "data": {"details": getattr(exc, "details", "")}}
        except Exception as exc:  # noqa: BLE001 - интерфейс не должен падать вместе с ядром
            log.exception("задача %s упала", task.id)
            reason = f"{type(exc).__name__}: {exc}"
            task.error = reason
            # Пишем причину и в журнал: раньше интерфейс показывал «internal», а
            # разбираться было негде — подробности оставались только в файле лога.
            assistant.journal.error("api", f"внутренняя ошибка запроса: {reason}")
            task.reply = {"text": get_translator(assistant.language).t("error.generic") + f" ({reason[:160]})",
                          "ok": False, "error": reason, "state": "error", "skill": None,
                          "action": None, "continue_dialog": False, "await_confirmation": False,
                          "data": {}}
        finally:
            if not task.decision.is_set():
                task.decision.set()  # отпускаем возможное ожидание подтверждения
            task.done.set()

    def _task_state(self, task_id: str | None) -> dict[str, Any]:
        if not task_id:
            raise ApiError(400, "no_task", "не указан номер запроса")
        with self._tasks_lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise ApiError(404, "unknown_task", "такой запрос не найден (возможно, окно перезапускалось)")
        if task.done.is_set():
            return {"state": "done", "reply": task.reply, "pending_id": task.id,
                    "request": task.text, "error": task.error or None}
        if task.asked:
            # окно спрашивает: показываем и вопрос навыка, и фразу пользователя,
            # чтобы было видно, о чём именно спрашивают
            return {"state": "confirmation_required", "pending_id": task.id,
                    "question": task.question, "request": task.text, "reply": None}
        return {"state": "working", "pending_id": task.id, "request": task.text, "reply": None}

    def _confirm(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("pending_id", "")).strip()
        if not task_id:
            raise ApiError(400, "no_task", "не указан номер запроса")
        with self._tasks_lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise ApiError(404, "unknown_task", "этот запрос уже завершён")
        if task.decision.is_set():
            return self._task_state(task_id)
        task.approved = bool(payload.get("approved", False))
        task.question = "" if task.approved else task.question
        task.decision.set()
        task.done.wait(timeout=CONFIRM_WAIT)
        return self._task_state(task_id)

    def _purge_tasks(self) -> None:
        now = time.time()
        stale = [task_id for task_id, task in self._tasks.items()
                 if task.done.is_set() and now - task.created > TASK_TTL]
        for task_id in stale:
            self._tasks.pop(task_id, None)
        if len(self._tasks) > MAX_TASKS:  # аварийная уборка, если интерфейс бросил опрос
            for task_id, _task in list(self._tasks.items())[: len(self._tasks) - MAX_TASKS]:
                self._tasks.pop(task_id, None)

    # ------------------------------------------------- настройки и секреты
    def _public_config(self) -> dict[str, Any]:
        """Настройки с вырезанными секретами: значения ключей не покидают ядро."""
        raw = self.config.raw_data or {}
        data = json.loads(json.dumps(self.config.data, ensure_ascii=False, default=str))
        return _mask_tree(data, raw)

    def _public_env(self) -> dict[str, Any]:
        from ..core import secrets

        path = paths.env_file_path()
        values = secrets.parse_env(path.read_text(encoding="utf-8")) if path.exists() else {}
        names = sorted(set(self.config.expected_env) | set(values))
        return {
            "file": str(path),
            "private": secrets.env_file_is_private(path) if path.exists() else True,
            "names": [
                {"name": name, "set": bool(values.get(name)),
                 "masked": secrets.mask_secret(values.get(name, ""))}
                for name in names
            ],
        }

    def _set_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Меняет один ключ config.toml, сохраняя комментарии и порядок."""
        from ..core import secrets, toml_edit

        dotted = str(payload.get("key", "")).strip()
        if not dotted:
            raise ApiError(400, "no_key", "не указан ключ настройки")
        value = payload.get("value")
        if _looks_secret_key(dotted):
            # Значение ключа в файл настроек не пишем никогда — только в .env.
            raise ApiError(400, "secret_key",
                           "это поле для секретов: сохраните значение через /secret "
                           "(оно ляжет в .env), а в настройках останется ссылка")
        if isinstance(value, str) and "${" in value:
            raise ApiError(400, "env_reference",
                           "ссылки на переменные окружения задаются в файле настроек вручную")
        if value is None:
            raise ApiError(400, "no_value", "не указано значение")
        if isinstance(value, (dict, list)) and not all(isinstance(item, (str, int, float, bool, type(None)))
                                                       for item in (value if isinstance(value, list) else value.values())):
            raise ApiError(400, "bad_value", "такое значение нельзя записать в настройки")
        path = paths.config_path()
        text = path.read_text(encoding="utf-8") if path.exists() else \
            paths.bundled_config_path().read_text(encoding="utf-8")
        try:
            updated = toml_edit.set_value(text, dotted, value)
        except toml_edit.TomlEditError as exc:
            raise ApiError(400, "bad_value", str(exc)) from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated, encoding="utf-8")
        self.assistant.refresh(reload_config=True)
        self.assistant.journal.info("ui", f"настройка {dotted} изменена")
        self.assistant.events.publish("config_changed", key=dotted)
        return {"state": "done", "key": dotted, "value": _mask_value(dotted, value),
                "config": self._public_config()}

    def _set_secret(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Сохраняет секрет в .env — и только туда. Наружу отдаётся лишь маска."""
        from ..core import secrets

        name = str(payload.get("name", "")).strip().upper()
        value = str(payload.get("value", "")).strip()
        if not name or not name.replace("_", "").isalnum():
            raise ApiError(400, "bad_name",
                           "имя переменной — латинские буквы, цифры и подчёркивания")
        if not value:
            raise ApiError(400, "empty_value", "пустое значение не сохраняю")
        secrets.save_env_var(name, value, paths.env_file_path())
        self.assistant.refresh(reload_config=True)
        self.assistant.journal.info("ui", f"секрет {name} сохранён в .env")
        self.assistant.events.publish("secret_saved", name=name)
        return {"state": "done", "name": name, "masked": secrets.mask_secret(value),
                "file": str(paths.env_file_path()), "env": self._public_env()}


# --------------------------------------------------------------------- helpers


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # на Windows прав достаточно самих по себе
        log.debug("не удалось выставить права на %s", path)


def _looks_secret_key(dotted: str) -> bool:
    lowered = dotted.lower()
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def _mask_value(key: str, value: Any) -> Any:
    from ..core import secrets

    if isinstance(value, str):
        if _looks_secret_key(key):
            return secrets.mask_secret(value)
        return value
    return value


def _mask_tree(node: Any, raw: Any = None, path: str = "") -> Any:
    """Рекурсивно прячет значения секретов; ссылки ``${NAME}`` показывает как есть."""
    from ..core import secrets

    if isinstance(node, dict):
        raw_map = raw if isinstance(raw, dict) else {}
        return {key: _mask_tree(value, raw_map.get(key), f"{path}.{key}" if path else str(key))
                for key, value in node.items()}
    if isinstance(node, list):
        raw_list = raw if isinstance(raw, list) else []
        return [_mask_tree(item, raw_list[index] if index < len(raw_list) else None, path)
                for index, item in enumerate(node)]
    if isinstance(node, str):
        if isinstance(raw, str) and raw.startswith("${") and raw.endswith("}"):
            return raw  # в настройках лежит ссылка, а не значение
        if _looks_secret_key(path):
            return secrets.mask_secret(node)
        return node
    return node


def _redact_text(text: str) -> str:
    from ..core import secrets

    return secrets.mask_text(text)


def _int(query: dict[str, list[str]], name: str, default: int, low: int, high: int) -> int:
    value = _first(query, name)
    if not value:
        return default
    try:
        number = int(value)
    except ValueError:
        return default
    return max(low, min(high, number))


def _first(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return values[0].strip() if values else ""


def _format_log(record: dict[str, Any]) -> str:
    stamp = time.strftime("%H:%M:%S", time.localtime(float(record.get("ts", time.time()))))
    return f"[{stamp}] {record.get('level', 'info').upper():<7} {record.get('source', '?')}: {record.get('message', '')}"


# ------------------------------------------------------------------ transport


def _make_handler(api: LocalApi):
    """Собирает обработчик HTTP со ссылкой на конкретный API."""

    class Handler(BaseHTTPRequestHandler):
        server_version = f"Jarvis/{__version__}"
        protocol_version = "HTTP/1.1"
        #: локальный API обслуживает короткие запросы, поэтому соединение
        #: закрываем сразу: так не копятся открытые сокеты и потоки
        close_connection = True
        timeout = 20

        # ------------------------------------------------------------- базовое
        def log_message(self, fmt: str, *args: Any) -> None:  # тишина в консоли
            log.debug("api %s", fmt % args)

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _fail(self, error: ApiError) -> None:
            self._send(error.status, {"state": "error", "error": error.code, "message": error.message})

        def _authorized(self) -> bool:
            token = self.headers.get("X-Jarvis-Token", "")
            if not token:
                token = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
            return bool(token) and hmac.compare_digest(token, api.token)

        def _read_payload(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > MAX_BODY:
                raise ApiError(413, "too_large", "запрос слишком большой")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except ValueError as exc:
                raise ApiError(400, "bad_json", "тело запроса не является JSON") from exc
            if not isinstance(data, dict):
                raise ApiError(400, "bad_json", "ожидался объект JSON")
            return data

        # -------------------------------------------------------------- методы
        def do_GET(self) -> None:  # noqa: N802 - так требует http.server
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if method == "GET" and (parsed.path == "/ui" or parsed.path.startswith("/ui/")):
                self._serve_ui(parsed.path)
                return
            if method == "GET" and parsed.path == "/" and api._demo:
                # демонстрационный режим (включается только JARVIS_DEMO_UI=1):
                # корень сразу открывает интерфейс, чтобы ссылку можно было дать как есть
                self.send_response(302)
                self.send_header("Location", f"/ui/?token={api.token}")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            try:
                if parsed.path != "/health" and not self._authorized():
                    raise ApiError(401, "unauthorized",
                                   "нужен заголовок X-Jarvis-Token (токен лежит в файле рядом с настройками)")
                payload = self._read_payload() if method == "POST" else {}
                api.note_client()
                with api._client_lock:  # ответы не перемешиваются между собой
                    result = api.handle(method, parsed.path, query, payload)
                self._send(200, result)
            except ApiError as exc:
                self._fail(exc)
            except Exception:  # noqa: BLE001 - сервер не должен падать от одного запроса
                log.exception("ошибка обработки запроса %s %s", method, parsed.path)
                self._send(500, {"state": "error", "error": "internal",
                                 "message": "внутренняя ошибка, подробности в журнале"})


        def _serve_ui(self, path: str) -> None:
            """Отдаёт файлы интерфейса из пакета.

            Это обычная статика (html, css, js, значок), секретов в ней нет,
            поэтому токен для файлов не нужен — а вот все запросы к API его
            требуют, так что без токена страница ничего не покажет.
            """
            from pathlib import Path

            name = "index.html" if path in ("/ui", "/ui/") else Path(path).name
            folder = Path(__file__).with_name("webui")
            file = folder / name
            types = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                     ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
                     ".json": "application/json; charset=utf-8", ".png": "image/png"}
            if not file.is_file() or name.startswith(".") or file.suffix not in types \
                    or folder.resolve() not in file.resolve().parents:
                self._send(404, {"state": "error", "error": "not_found",
                                 "message": "нет такого файла интерфейса"})
                return
            body = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", types[file.suffix])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    return Handler
