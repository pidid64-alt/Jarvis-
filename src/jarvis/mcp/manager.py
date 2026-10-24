"""Менеджер MCP-серверов: соединения, hot reload, мост инструментов.

Особенности реализации:
- Весь жизненный цикл каждого соединения живёт в СВОЁМ asyncio-таске
  (требование anyio: enter/exit контекст-менеджеров в одном таске).
- Инструменты сервера попадают в общий реестр с префиксом mcp__<server>__
  и классом действия из конфига (least privilege: allowlist + read_only).
- Результаты MCP-инструментов всегда считаются недоверенными (обёртка
  <untrusted>): сервер — внешний процесс с непроверяемой логикой.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.config import MCPServerConfig, load_mcp_config
from jarvis.core.tools.base import SideEffect, Tool, ToolError, ToolResult
from jarvis.core.tools.registry import ToolRegistry

log = logging.getLogger("jarvis.mcp")

_VALID_NAME = re.compile(r"[^a-zA-Z0-9_-]")


class MCPError(RuntimeError):
    pass


def _resolve_env_value(value: str) -> str:
    """Значения вида "env:ИМЯ" подставляются из переменных окружения."""
    if isinstance(value, str) and value.startswith("env:"):
        env_name = value[4:]
        resolved = os.environ.get(env_name)
        if resolved is None:
            raise MCPError(f"Переменная окружения {env_name} не задана (указана в mcp_servers.yaml)")
        return resolved
    return value


@dataclass
class ServerStatus:
    name: str
    transport: str
    enabled: bool
    state: str = "idle"  # idle | connected | error | disabled
    tools: int = 0
    error: str | None = None


@dataclass
class _ServerWorker:
    """Держит одно соединение открытым между шагами агента."""

    cfg: MCPServerConfig
    session = None
    error: str | None = None
    _ready: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._worker(), name=f"mcp-{self.cfg.name}")
        await self._ready.wait()
        if self.error:
            raise MCPError(f"Сервер {self.cfg.name}: {self.error}")

    async def _worker(self) -> None:
        """Единый таск-владелец контекст-менеджеров соединения."""
        try:
            async with self._open_streams() as streams:
                read_stream, write_stream = streams[0], streams[1]
                from mcp import ClientSession

                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    self.session = session
                    self._ready.set()
                    await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log.error("MCP-сервер %s упал: %s", self.cfg.name, self.error)
        finally:
            self.session = None
            self._ready.set()

    def _open_streams(self):
        if self.cfg.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import get_default_environment, stdio_client

            env = get_default_environment()
            env.update({k: _resolve_env_value(v) for k, v in self.cfg.env.items()})
            params = StdioServerParameters(command=self.cfg.command, args=self.cfg.args, env=env)
            return stdio_client(params)
        if self.cfg.transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            headers = {k: _resolve_env_value(v) for k, v in self.cfg.headers.items()}
            return streamablehttp_client(self.cfg.url, headers=headers or None)
        if self.cfg.transport == "sse":
            from mcp.client.sse import sse_client

            headers = {k: _resolve_env_value(v) for k, v in self.cfg.headers.items()}
            return sse_client(self.cfg.url, headers=headers or None)
        raise MCPError(f"Неизвестный транспорт {self.cfg.transport!r} (сервер {self.cfg.name})")

    async def call(self, tool_name: str, arguments: dict):
        if self.session is None:
            raise MCPError(f"Сервер {self.cfg.name} не подключён")
        async with self._lock:
            return await self.session.call_tool(tool_name, arguments)

    async def list_tools(self) -> list:
        if self.session is None:
            raise MCPError(f"Сервер {self.cfg.name} не подключён")
        result = await self.session.list_tools()
        return list(result.tools)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass


class MCPManager:
    def __init__(self, config_path: Path, registry: ToolRegistry, max_result_chars: int = 12000):
        self._config_path = config_path
        self._registry = registry
        self._max_result_chars = max_result_chars
        self._workers: dict[str, _ServerWorker] = {}
        self.statuses: list[ServerStatus] = []

    async def start_all(self) -> None:
        """Подключает все включённые серверы из конфига и регистрирует инструменты."""
        config = load_mcp_config(self._config_path)
        self.statuses = []
        for cfg in config.servers:
            status = ServerStatus(name=cfg.name, transport=cfg.transport, enabled=cfg.enabled)
            if not cfg.enabled:
                status.state = "disabled"
                self.statuses.append(status)
                continue
            worker = _ServerWorker(cfg)
            try:
                await worker.start()
                tools = await worker.list_tools()
                self._workers[cfg.name] = worker
                self._register_tools(worker, tools)
                status.state = "connected"
                status.tools = len(tools)
                log.info("MCP %s: подключён, инструментов: %s", cfg.name, len(tools))
            except (MCPError, Exception) as e:
                status.state = "error"
                status.error = str(e)
                log.warning("MCP %s не подключился: %s", cfg.name, e)
            self.statuses.append(status)

    async def reload(self) -> None:
        """Горячая перезагрузка: перечитать YAML и пересобрать соединения."""
        log.info("Перезагрузка MCP-серверов из %s", self._config_path)
        await self.stop_all()
        await self.start_all()

    async def stop_all(self) -> None:
        for worker in self._workers.values():
            await worker.stop()
        self._workers.clear()
        self._registry.unregister_mcp()

    def _register_tools(self, worker: _ServerWorker, tools: list) -> None:
        cfg = worker.cfg
        for mcp_tool in tools:
            if cfg.allowed_tools is not None and mcp_tool.name not in cfg.allowed_tools:
                log.debug("Инструмент %s.%s не в allowlist — пропущен", cfg.name, mcp_tool.name)
                continue
            safe_name = _VALID_NAME.sub("_", mcp_tool.name)[:48] or "tool"
            full_name = f"mcp__{cfg.name}__{safe_name}"[:64]
            side_effect: SideEffect = (
                cfg.tool_side_effects.get(mcp_tool.name)  # type: ignore[assignment]
                or ("read" if cfg.read_only else "outbound")
            )
            description = (mcp_tool.description or "").strip()[:400]
            tool = Tool(
                name=full_name,
                description=f"[MCP:{cfg.name}] {description}".strip(),
                parameters=dict(mcp_tool.input_schema) if mcp_tool.input_schema else {"type": "object", "properties": {}},
                side_effect=side_effect,
                returns_untrusted=True,  # результаты внешних процессов — недоверенные
                handler=_make_handler(worker, mcp_tool.name, self._max_result_chars),
                source=f"mcp:{cfg.name}",
            )
            try:
                self._registry.register(tool)
            except ValueError:
                log.warning("Дубликат имени инструмента %s — пропущен", full_name)


def _make_handler(worker: _ServerWorker, remote_name: str, max_chars: int):
    async def handler(args: dict) -> ToolResult:
        try:
            result = await worker.call(remote_name, args)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"MCP-ошибка: {e}") from e
        texts = [getattr(block, "text", None) for block in (result.content or [])]
        text = "\n".join(t for t in texts if t).strip()
        if getattr(result, "isError", False):
            raise ToolError(text or f"MCP-инструмент {remote_name} вернул ошибку")
        if not text:
            text = f"(MCP-инструмент {remote_name} вернул пустой результат)"
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[…результат обрезан]"
        return ToolResult(output=text, untrusted=True)

    return handler
