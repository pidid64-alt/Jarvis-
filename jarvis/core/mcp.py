"""MCP в Jarvis: серверы инструментов как обычные навыки.

Идея проста: если у пользователя есть серверы MCP (чужой инструмент, свой
скрипт, домашний сервер), Jarvis подключается к ним и превращает инструменты в
действия — модель выбирает действие по описанию, а ядро выполняет его через
сервер. Ни команд, ни путей модель по-прежнему не пишет.

Правила Jarvis соблюдаются и здесь:

* ничего не подключается «на всякий случай» — сервер поднимается только тогда,
  когда фразу не разобрал ни один навык и понадобилась модель (на слабой машине
  это экономит и память, и диск);
* подключение и бездействие ограничены: неиспользуемый сервер сам гаснет
  (``idle_seconds``);
* вызов инструмента с аргументами показывается пользователю до отправки;
* ключи серверов — только ссылками ``${ИМЯ}`` из окружения и никогда в журнал.
"""

from __future__ import annotations

import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import paths
from .config import referenced_env_names, resolve_refs
from .errors import ConfigError
from .logging_setup import get_logger
from .types import Action, Permissions, Skill

log = get_logger("core.mcp")

DEFAULT_TIMEOUT = 20.0
DEFAULT_IDLE_SECONDS = 600.0
MAX_TOOLS_PER_SERVER = 25
#: имена, значения которых обязаны быть ссылкой на переменную окружения
SECRET_NAMES = ("token", "key", "secret", "password", "passwd", "authorization", "apikey")


@dataclass
class McpServer:
    """Один сервер MCP из файла настроек."""

    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    enabled: bool = True
    confirm: bool = False
    timeout: float = DEFAULT_TIMEOUT
    idle_seconds: float = DEFAULT_IDLE_SECONDS
    description: str = ""

    @property
    def kind(self) -> str:
        return "http" if self.url else "stdio"

    def summary(self) -> str:
        if self.url:
            return f"{self.name}: {self.url}"
        return f"{self.name}: {' '.join([self.command, *self.args])}".strip()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "enabled": self.enabled,
            "confirm": self.confirm,
            "description": self.description,
            "command": self.command if self.kind == "stdio" else "",
            "url": self.url,
            "timeout_seconds": self.timeout,
        }


@dataclass
class McpTool:
    """Инструмент сервера — станет действием навыка ``mcp``."""

    server: str
    name: str
    description: str = ""
    #: схема аргументов (inputSchema у сервера)
    schema: dict[str, Any] = field(default_factory=dict)
    #: подсказки сервера: только чтение, меняет данные (annotations)
    annotations: dict[str, Any] = field(default_factory=dict)

    @property
    def action_id(self) -> str:
        return f"{self.server}.{self.name}"

    @property
    def required(self) -> list[str]:
        values = self.schema.get("required")
        return [str(item) for item in values] if isinstance(values, list) else []

    @property
    def properties(self) -> dict[str, Any]:
        values = self.schema.get("properties")
        return values if isinstance(values, dict) else {}

    @property
    def read_only(self) -> bool:
        return bool(self.annotations.get("readOnlyHint"))

    @property
    def destructive(self) -> bool:
        return bool(self.annotations.get("destructiveHint"))

    def prompt_line(self) -> str:
        text = self.description.strip() or self.name
        if len(text) > 180:
            text = text[:177] + "…"
        if self.required:
            text += f" (нужно указать: {', '.join(self.required[:4])})"
        return f"[{self.server}] {text}"


def check_secret_refs(data: dict[str, Any]) -> None:
    """Токены в файле — только ссылками ``${ИМЯ}``, иначе стоп.

    Проверяем СЫРОЙ файл: если проверять после подстановки, ссылка уже
    превратится в пустую строку и правило перестанет работать.
    """
    items = data.get("server") or []
    if isinstance(items, dict):
        items = [items]
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "?"))
        for label in ("env", "headers"):
            values = item.get(label) or {}
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                text = str(value)
                if any(word in str(key).lower() for word in SECRET_NAMES) and "${" not in text:
                    raise ConfigError(
                        f"mcp.toml: {name}.{label}.{key} должен ссылаться на переменную "
                        f"окружения, например \"${{{str(key).upper()}}}\" — значение в файле "
                        f"хранить нельзя")


def parse_servers(data: dict[str, Any], *, path: Path | None = None,
                  check_secrets: bool = True) -> list[McpServer]:
    """Разбирает секции ``[[server]]`` файла mcp.toml."""
    if check_secrets:
        check_secret_refs(data)
    raw_items = data.get("server") or []
    if isinstance(raw_items, dict):  # редкий случай: одна секция без массива
        raw_items = [raw_items]
    servers: list[McpServer] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ConfigError(f"mcp.toml: запись server должна быть таблицей (номер {index + 1})")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ConfigError(f"mcp.toml: у сервера номер {index + 1} не указано имя (name)")
        command = str(item.get("command", "")).strip()
        url = str(item.get("url", "")).strip()
        if not command and not url:
            raise ConfigError(f"mcp.toml: у сервера «{name}» нет ни command, ни url")
        env = {str(key): str(value) for key, value in (item.get("env") or {}).items()}
        headers = {str(key): str(value) for key, value in (item.get("headers") or {}).items()}
        servers.append(McpServer(
            name=name,
            command=command,
            args=[str(part) for part in (item.get("args") or [])],
            url=url,
            env=env,
            headers=headers,
            cwd=str(item.get("cwd", "")),
            enabled=bool(item.get("enabled", True)),
            confirm=bool(item.get("confirm", False)),
            timeout=float(item.get("timeout_seconds", DEFAULT_TIMEOUT)),
            idle_seconds=float(item.get("idle_seconds", DEFAULT_IDLE_SECONDS)),
            description=str(item.get("description", "")),
        ))
    return servers


def ensure_config_file() -> Path:
    """Создаёт ``mcp.toml`` с подсказками при первом обращении."""
    path = paths.mcp_config_path()
    if path.exists():
        return path
    paths.ensure_dirs()
    example = paths.bundled_mcp_example_path()
    text = example.read_text(encoding="utf-8") if example.exists() else "[mcp]\nenabled = true\n"
    path.write_text(text, encoding="utf-8")
    return path


class McpRegistry:
    """Подключения к серверам MCP, список инструментов и вызовы."""

    def __init__(self, config: Any = None, journal: Any = None, *, path: Path | None = None):
        self.config = config
        self.journal = journal
        self.path = Path(path) if path else paths.mcp_config_path()
        self._servers: list[McpServer] = []
        self._tools: list[McpTool] = []
        self._clients: dict[str, Any] = {}
        self._problems: dict[str, str] = {}
        self._used: dict[str, float] = {}
        self._discovered = False
        self._lock = threading.RLock()
        self._settings: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------ чтение
    def _load(self) -> None:
        self._servers = []
        self._settings = {"enabled": True, "max_tools_per_server": MAX_TOOLS_PER_SERVER}
        if not self.path.exists():
            return
        try:
            raw = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"{self.path}: {exc}") from exc
        resolved, missing = resolve_refs(raw)
        section = resolved.get("mcp") if isinstance(resolved.get("mcp"), dict) else {}
        self._settings = {
            "enabled": bool(section.get("enabled", True)),
            "max_tools_per_server": int(section.get("max_tools_per_server", MAX_TOOLS_PER_SERVER)),
        }
        self._missing_env = sorted(set(missing))
        check_secret_refs(raw)  # до подстановки: иначе ссылка станет пустой строкой
        self._servers = parse_servers(resolved, path=self.path, check_secrets=False)
        # «${ИМЯ}» без значения молча не подставляется: скажем об этом прямо.
        # Имена ищем в сыром файле: в разобранном ссылки уже заменены пустотой.
        wanted: set[str] = set()
        raw_items = raw.get("server") or []
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        for item in raw_items:
            if isinstance(item, dict):
                wanted.update(referenced_env_names(item.get("env") or {}))
                wanted.update(referenced_env_names(item.get("headers") or {}))
        self._missing_env = sorted(set(self._missing_env) | wanted)

    @property
    def enabled(self) -> bool:
        return bool(self._settings.get("enabled", True)) and bool(self._servers)

    @property
    def missing_env(self) -> list[str]:
        return list(getattr(self, "_missing_env", []))

    def servers(self, *, only_enabled: bool = False) -> list[McpServer]:
        return [item for item in self._servers if item.enabled or not only_enabled]

    @property
    def problems(self) -> dict[str, str]:
        return dict(self._problems)

    # -------------------------------------------------------------- подключение
    def _make_client(self, server: McpServer):
        from ..providers.mcp import HttpTransport, McpClient, StdioTransport

        if server.kind == "http":
            transport = HttpTransport(server.url, headers=server.headers, timeout=server.timeout,
                                      name=server.name)
        else:
            transport = StdioTransport(server.command, server.args, env=server.env,
                                       cwd=server.cwd or None, name=server.name)
        return McpClient(server.name, transport, timeout=server.timeout)

    def client(self, server: McpServer):
        """Клиент сервера, уже прошедший рукопожатие (поднимается по требованию)."""
        with self._lock:
            client = self._clients.get(server.name)
            if client is not None and client.alive():
                self._used[server.name] = time.monotonic()
                return client
            if client is not None:
                client.close()
                self._clients.pop(server.name, None)
        client = self._make_client(server)
        try:
            client.connect(timeout=server.timeout)
        except Exception:
            client.close()
            raise
        with self._lock:
            self._clients[server.name] = client
            self._used[server.name] = time.monotonic()
        return client

    def discover(self, *, timeout: float | None = None, force: bool = False) -> list[McpTool]:
        """Подключается к включённым серверам и собирает список инструментов."""
        with self._lock:
            if self._discovered and not force:
                return list(self._tools)
        if not self.enabled:
            return []
        tools: list[McpTool] = []
        limit = int(self._settings.get("max_tools_per_server", MAX_TOOLS_PER_SERVER))
        for server in self.servers(only_enabled=True):
            try:
                client = self.client(server)
                items = client.list_tools(timeout=timeout or server.timeout)
            except Exception as exc:  # noqa: BLE001 - сервер может быть просто выключен
                message = str(exc)
                self._problems[server.name] = message
                log.warning("сервер MCP «%s» недоступен: %s", server.name, message)
                if self.journal is not None:
                    self.journal.warning("core.mcp", f"сервер {server.name} недоступен: {message}")
                continue
            self._problems.pop(server.name, None)
            if len(items) > limit:
                log.info("у сервера %s %d инструментов, беру первые %d", server.name, len(items), limit)
            for item in items[:limit]:
                schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
                tool = McpTool(
                    server=server.name,
                    name=str(item.get("name", "")),
                    description=str(item.get("description") or item.get("title") or ""),
                    schema=schema or {},
                    annotations=item.get("annotations") if isinstance(item.get("annotations"), dict) else {},
                )
                if not tool.name:
                    continue
                tools.append(tool)
            if self.journal is not None:
                self.journal.info("core.mcp",
                                  f"сервер {server.name}: инструментов {len(items[:limit])}")
        with self._lock:
            self._tools = tools
            self._discovered = True
        return list(tools)

    @staticmethod
    def asks_for_mcp(text: str) -> bool:
        """Похоже ли, что человек говорит про MCP прямо сейчас.

        Нужно, чтобы фразы «какие инструменты mcp» и «вызови инструмент …»
        разбирались навыком без модели — а для этого серверы должны подняться
        ещё до поиска совпадений. Проверка дешёвая: пара слов, без сети.
        """
        from .matcher import normalize_text

        words = normalize_text(text).split()
        return "mcp" in words or any(word.startswith("инструмент") for word in words)

    def tools(self) -> list[McpTool]:
        with self._lock:
            return list(self._tools)

    def call(self, server_name: str, tool_name: str, arguments: dict[str, Any] | None = None,
             *, timeout: float | None = None) -> str:
        server = next((item for item in self._servers if item.name == server_name), None)
        if server is None:
            raise ConfigError(f"сервер MCP «{server_name}» не описан в mcp.toml")
        if not server.enabled:
            raise ConfigError(f"сервер MCP «{server_name}» выключен")
        client = self.client(server)
        self._used[server.name] = time.monotonic()
        if self.journal is not None:
            keys = ", ".join(sorted(arguments or {})) or "без аргументов"
            self.journal.info("core.mcp", f"вызов {server.name}.{tool_name} ({keys})")
        return client.call_tool(tool_name, arguments, timeout=timeout or server.timeout)

    # -------------------------------------------------------------- жизненный цикл
    def stop_if_idle(self, *, now: float | None = None) -> list[str]:
        """Гасит серверы, к которым давно не обращались (экономия памяти)."""
        moment = time.monotonic() if now is None else now
        stopped: list[str] = []
        with self._lock:
            for name, client in list(self._clients.items()):
                server = next((item for item in self._servers if item.name == name), None)
                idle = server.idle_seconds if server else DEFAULT_IDLE_SECONDS
                if moment - self._used.get(name, moment) >= idle:
                    client.close()
                    self._clients.pop(name, None)
                    stopped.append(name)
        if stopped:
            log.info("серверы MCP остановлены по бездействию: %s", ", ".join(stopped))
        return stopped

    def close(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                log.debug("не удалось закрыть сервер MCP", exc_info=True)

    def reload(self) -> None:
        """Перечитывает файл настроек (после правки mcp.toml)."""
        self.close()
        self._discovered = False
        self._tools = []
        self._problems.clear()
        self._load()

    # ------------------------------------------------------------------- навык
    def skill(self, registry: Any = None) -> Skill | None:
        """Виртуальный навык «mcp»: его действия — инструменты серверов."""
        tools = self.tools()
        if not tools:
            return None
        # Два действия, которые работают без модели: посмотреть список и
        # вызвать инструмент вслух. Если модель недоступна (или фразу разобрал
        # навык), инструменты MCP всё равно остаются доступными.
        actions: list[Action] = [
            Action(
                id="list",
                phrases=["какие инструменты mcp", "список инструментов mcp", "инструменты mcp",
                         "что умеют серверы mcp", "какие есть mcp инструменты"],
                description="Показать инструменты подключённых серверов MCP",
                tags=["mcp"],
            ),
            Action(
                id="run",
                phrases=["вызови инструмент", "вызови mcp", "запусти инструмент mcp"],
                description="Вызвать инструмент MCP по имени (например: вызови инструмент fake.echo)",
                capture=True,
                tags=["mcp", "network"],
            ),
        ]
        for tool in tools:
            server = next((item for item in self._servers if item.name == tool.server), None)
            confirm = bool((server.confirm if server else False) or tool.required or tool.destructive)
            if tool.read_only and not (server.confirm if server else False):
                confirm = bool(tool.required)  # только чтение, но с аргументами — покажем
            actions.append(Action(
                id=tool.action_id,
                phrases=[],
                description=tool.prompt_line(),
                handler=tool.name,
                # подтверждение спрашивает сам обработчик: в вопросе видно, что
                # именно уйдёт серверу (у обычных действий — политика прав)
                confirm=False,
                tags=["mcp", "network"] + (["dangerous"] if (server.confirm if server else False) else []),
            ))
        skill = Skill(
            id="mcp",
            name="Инструменты MCP",
            description="Инструменты внешних серверов MCP: Jarvis подключается к ним по вашему файлу mcp.toml",
            version="1.0",
            path=None,
            enabled=not (registry is not None and registry.is_disabled("mcp")),
            permissions=Permissions(network=True, files=False, shell=True, notify=False),
            actions=actions,
            entry="handler.py",  # не файл, а признак «есть обработчик в памяти»
            examples=[],
            builtin=False,
        )
        return skill

    def handlers(self, registry: Any = None) -> Any:
        """Модуль-заглушка с ``HANDLERS`` для навыка «mcp»."""
        tools = {tool.action_id: tool for tool in self.tools()}

        class _Handlers:
            HANDLERS = {
                action_id: (lambda ctx, intent, _tool=tool: self.run_tool(_tool, ctx, intent))
                for action_id, tool in tools.items()
            }
            HANDLERS["list"] = self.list_tools_reply
            HANDLERS["run"] = self.run_named_tool

        return _Handlers()

    # --------------------------------------------------- вызовы без модели
    def list_tools_reply(self, ctx: Any, intent: Any) -> Any:
        """«какие инструменты MCP» — список без обращения к модели."""
        tools = self.tools()
        if not tools:
            from ..skills import fail

            return fail(ctx, "skill.mcp.list_empty")
        lines = [ctx.t("skill.mcp.list_title", servers=len({tool.server for tool in tools}),
                       tools=len(tools))]
        for tool in tools:
            marks = []
            if tool.required:
                marks.append("нужны аргументы: " + ", ".join(tool.required))
            if tool.destructive:
                marks.append("меняет данные")
            tail = f" ({'; '.join(marks)})" if marks else ""
            lines.append(f"- {tool.action_id}: {tool.description or tool.name}{tail}")
        for name, reason in self._problems.items():
            lines.append(f"- {name}: не подключился — {reason}")
        return "\n".join(lines)

    def run_named_tool(self, ctx: Any, intent: Any) -> Any:
        """«вызови инструмент имя ключ=значение …» — тоже без модели."""
        from ..skills import fail

        raw = str(intent.args.get(intent.action.capture_field, "")).strip()
        if not raw:
            return fail(ctx, "skill.mcp.need_id")
        parts = raw.split()
        wanted = parts[0].strip("«»\"',")
        arguments: dict[str, Any] = {}
        for item in parts[1:]:
            if "=" in item:
                key, value = item.split("=", 1)
                arguments[key.strip()] = value.strip()
        tool = self._find_tool(wanted)
        if tool is None:
            return fail(ctx, "skill.mcp.unknown_tool", name=wanted)
        intent.args["model_arguments"] = arguments
        return self.run_tool(tool, ctx, intent, args_from_user=True)

    def _find_tool(self, wanted: str) -> McpTool | None:
        """Ищем инструмент по полному имени или по короткому (если он один такой)."""
        tools = self.tools()
        for tool in tools:
            if tool.action_id == wanted or tool.name == wanted:
                return tool
        matches = [tool for tool in tools if tool.name.lower() == wanted.lower()]
        return matches[0] if len(matches) == 1 else None

    # --------------------------------------------------------- выполнение вызова
    def run_tool(self, tool: McpTool, ctx: Any, intent: Any, *, args_from_user: bool = False) -> Any:
        """Обработчик действия: подтверждение → вызов → человеческий ответ.

        ``args_from_user`` — аргументы человек набрал сам («вызови инструмент
        fake.sum a=2 b=3»). Тогда переспрашивать не о чем; а вот если аргументы
        пришли от модели, их обязательно показывают перед отправкой.
        """
        from ..skills import fail

        arguments = dict(intent.args.get("model_arguments")
                         or intent.args.get("mcp_arguments") or {})
        missing = [key for key in tool.required if key not in arguments]
        if missing:
            return fail(ctx, "skill.mcp.need_args", tool=tool.name, server=tool.server,
                        args=", ".join(missing))
        server = next((item for item in self._servers if item.name == tool.server), None)
        needs_confirm = bool((server.confirm if server else False) or tool.destructive
                             or (arguments and not args_from_user))
        if needs_confirm:
            question = ctx.t("skill.mcp.confirm", server=tool.server, tool=tool.name,
                             args=self._format_arguments(arguments))
            if ctx.confirm_callback is None:
                return fail(ctx, "skill.mcp.no_confirmation", tool=tool.name)
            if not ctx.confirm_callback(question):
                return fail(ctx, "skill.mcp.cancelled", tool=tool.name)
        try:
            text = self.call(tool.server, tool.name, arguments)
        except Exception as exc:  # noqa: BLE001 - сервер может ответить чем угодно
            ctx.log.warning("инструмент %s.%s не сработал: %s", tool.server, tool.name, exc,
                            exc_info=True)
            return fail(ctx, "skill.mcp.failed", server=tool.server, tool=tool.name,
                        reason=str(exc)[:300])
        return text or ctx.t("skill.mcp.empty", tool=tool.name)

    @staticmethod
    def _format_arguments(arguments: dict[str, Any]) -> str:
        if not arguments:
            return "без аргументов"
        parts = []
        for key, value in list(arguments.items())[:6]:
            text = str(value)
            if len(text) > 80:
                text = text[:77] + "…"
            parts.append(f"{key}: {text}")
        return "; ".join(parts)

    # ----------------------------------------------------------------- контроль
    def status(self) -> dict[str, Any]:
        tools = self.tools()
        by_server: dict[str, int] = {}
        for tool in tools:
            by_server[tool.server] = by_server.get(tool.server, 0) + 1
        return {
            "enabled": self.enabled,
            "path": str(self.path),
            "exists": self.path.exists(),
            "missing_env": self.missing_env,
            "servers": [
                {**server.to_public_dict(),
                 "tools": by_server.get(server.name, 0),
                 "problem": self._problems.get(server.name, ""),
                 "connected": server.name in self._clients}
                for server in self._servers
            ],
            "tools": [{"id": tool.action_id, "server": tool.server, "name": tool.name,
                       "description": tool.description, "required": tool.required,
                       "read_only": tool.read_only, "destructive": tool.destructive}
                      for tool in tools],
        }


def attach(registry: Any, mcp: McpRegistry, *, timeout: float | None = None) -> bool:
    """Включает инструменты MCP в реестр навыков (если они есть)."""
    if mcp is None or not mcp.enabled:
        return False
    try:
        mcp.discover(timeout=timeout)
    except Exception:  # noqa: BLE001 - MCP не должен мешать обычной работе
        log.warning("не удалось собрать инструменты MCP", exc_info=True)
        return False
    skill = mcp.skill(registry)
    if skill is None:
        return False
    registry.add_skill(skill, mcp.handlers(registry))
    return True


def validate_args(arguments: Any, *, only: list[str] | None = None,
                  nested: tuple[str, ...] = ()) -> dict[str, Any]:
    """Оставляет только простые значения: инструментам не нужны сложные структуры.

    Заодно это защита: модель не сможет передать инструменту вложенный объект или
    длинную строку с чем угодно — только короткие значения, видимые в вопросе.
    ``only`` — какие имена разрешены, ``nested`` — какие ключи могут содержать
    маленький словарь (у инструментов MCP — ``arguments``).
    """
    if not isinstance(arguments, dict):
        return {}
    allowed: dict[str, Any] = {}
    permitted = set(only) if only else None
    for key, value in list(arguments.items())[:8]:
        name = str(key)[:60]
        if permitted is not None and name not in permitted:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            allowed[name] = value
        elif isinstance(value, str):
            allowed[name] = value[:400]
        elif isinstance(value, list):
            allowed[name] = [str(item)[:120] for item in value[:8]]
        elif isinstance(value, dict) and name in nested:
            allowed[name] = {str(inner_key)[:60]:
                             (inner_value[:400] if isinstance(inner_value, str) else inner_value)
                             for inner_key, inner_value in list(value.items())[:8]
                             if isinstance(inner_value, (str, int, float, bool))}
    return allowed


def call_with_retry(action: Callable[[], Any], attempts: int = 1, delay: float = 0.2) -> Any:
    """Повтор вызова сервера: stdio-сервер мог только проснуться."""
    last: Exception | None = None
    for index in range(max(1, attempts)):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if index + 1 < attempts:
                time.sleep(delay)
    assert last is not None
    raise last
