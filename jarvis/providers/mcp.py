"""Клиент MCP (Model Context Protocol) для внешних серверов инструментов.

MCP — это простой протокол: стороны обмениваются сообщениями JSON-RPC 2.0,
поэтому Jarvis обходится стандартной библиотекой, без пакетов SDK.

Поддерживаются два транспорта из спецификации:

* **stdio** — Jarvis запускает программу сервера и говорит с ней через
  stdin/stdout (по одной JSON-строке на сообщение);
* **streamable HTTP** — Jarvis шлёт POST-запросы на адрес сервера, ответ
  читает как JSON или как поток SSE.

Что используется на практике (см. этап 4): сначала ``initialize``, затем
``notifications/initialized``, затем ``tools/list`` и ``tools/call``.
Ключи серверов берутся только из ссылок вида ``${ИМЯ}`` и не пишутся в журнал.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from typing import Any, Callable

from ..core.errors import ProviderError
from ..core.logging_setup import get_logger
from .http import HttpClient

log = get_logger("providers.mcp")

#: версия протокола, которую понимает Jarvis (самая распространённая у серверов)
PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "jarvis"
CLIENT_VERSION = "0.3"


class McpError(ProviderError):
    """Сервер MCP недоступен или вернул ошибку."""


# --------------------------------------------------------------------- stdio
class StdioTransport:
    """Процесс сервера MCP: JSON-RPC по одной строке через stdin/stdout."""

    def __init__(self, command: str, args: list[str] | None = None, *,
                 env: dict[str, str] | None = None, cwd: str | None = None,
                 name: str = "mcp"):
        self.command = command
        self.args = list(args or [])
        self.env_extra = dict(env or {})
        self.cwd = cwd
        self.name = name
        self.process: subprocess.Popen | None = None
        self._pending: dict[str, tuple[threading.Event, list]] = {}
        self._lock = threading.RLock()
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self.error: str = ""

    # ------------------------------------------------------------ жизненный цикл
    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        env = os.environ.copy()
        env.update(self.env_extra)
        try:
            self.process = subprocess.Popen(
                [self.command, *self.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, cwd=self.cwd, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
        except (OSError, ValueError) as exc:
            raise McpError(f"не удалось запустить «{self.command}»: {exc}") from exc
        self.error = ""
        self._reader = threading.Thread(target=self._read_stdout, name=f"mcp-{self.name}-out",
                                        daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, name=f"mcp-{self.name}-err",
                                               daemon=True)
        self._stderr_reader.start()
        log.info("сервер MCP «%s» запущен: %s", self.name, " ".join([self.command, *self.args])[:200])

    def _read_stdout(self) -> None:
        stream = self.process.stdout if self.process else None
        if stream is None:
            return
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                log.debug("сервер %s написал не-JSON: %s", self.name, line[:200])
                continue
            if isinstance(message, dict) and "id" in message and (
                    "result" in message or "error" in message):
                self._deliver(message)
            else:
                log.debug("сервер %s прислал уведомление: %s", self.name,
                          str(message)[:200])
        self._fail_all("сервер закрыл соединение")

    def _read_stderr(self) -> None:
        stream = self.process.stderr if self.process else None
        if stream is None:
            return
        for line in stream:
            text = line.rstrip()
            if text:
                log.debug("[%s] %s", self.name, text[:400])

    def _deliver(self, message: dict) -> None:
        key = str(message.get("id"))
        with self._lock:
            entry = self._pending.pop(key, None)
        if entry is not None:
            entry[1].append(message)
            entry[0].set()

    def _fail_all(self, reason: str) -> None:
        self.error = reason
        with self._lock:
            entries = list(self._pending.values())
            self._pending.clear()
        for event, box in entries:
            box.append({"error": {"code": -32000, "message": reason}})
            event.set()

    # ------------------------------------------------------------------ запросы
    def request(self, method: str, params: dict | None = None, timeout: float = 20.0) -> Any:
        self.start()
        if self.process is None or self.process.poll() is not None:
            raise McpError(self.error or f"процесс «{self.command}» не запущен")
        request_id = uuid.uuid4().hex[:12]
        event, box = threading.Event(), []
        with self._lock:
            self._pending[request_id] = (event, box)
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise McpError(f"не удалось отправить запрос серверу: {exc}") from exc
        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise McpError(f"сервер не ответил за {timeout:g} с (метод {method})")
        message = box[0]
        if "error" in message:
            error = message["error"] or {}
            raise McpError(f"{error.get('message') or 'ошибка сервера'} "
                           f"(код {error.get('code', '?')})")
        return message.get("result")

    def notify(self, method: str, params: dict | None = None) -> None:
        self.start()
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        try:
            assert self.process is not None and self.process.stdin is not None
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:  # уведомление не критично
            log.debug("уведомление %s не ушло: %s", method, exc)

    def close(self) -> None:
        process, self.process = self.process, None
        self._fail_all("сервер остановлен")
        if process is None:
            return
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
        log.info("сервер MCP «%s» остановлен", self.name)

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


# ---------------------------------------------------------------------- HTTP
class HttpTransport:
    """Streamable HTTP: POST JSON-RPC, ответ JSON или поток SSE."""

    def __init__(self, url: str, *, headers: dict[str, str] | None = None, timeout: float = 20.0,
                 name: str = "mcp"):
        self.url = url
        self.name = name
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.session_id = ""
        self.client = HttpClient(timeout=timeout, retries=0)

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> Any:
        request_id = uuid.uuid4().hex[:12]
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            response = self.client.post_json(self.url, payload, headers=headers, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - сеть бывает разной
            raise McpError(f"сервер {self.url} недоступен: {exc}") from exc
        session = response.headers.get("Mcp-Session-Id") if getattr(response, "headers", None) else None
        if session:
            self.session_id = session
        content_type = (getattr(response, "headers", {}) or {}).get("Content-Type", "")
        body = response.body.decode("utf-8", "replace") if response.body else ""
        message = self._pick_message(body, content_type, request_id)
        if message is None:
            raise McpError("в ответе сервера нет данных (ожидался JSON-RPC)")
        if "error" in message:
            error = message["error"] or {}
            raise McpError(f"{error.get('message') or 'ошибка сервера'} (код {error.get('code', '?')})")
        return message.get("result")

    def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        headers = {"Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL_VERSION, **self.headers}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            self.client.post_json(self.url, payload, headers=headers)
        except Exception as exc:  # noqa: BLE001
            log.debug("уведомление %s не ушло: %s", method, exc)

    @staticmethod
    def _pick_message(body: str, content_type: str, request_id: str) -> dict | None:
        """Достаёт нужное сообщение из JSON-ответа или из потока SSE."""
        if "text/event-stream" in content_type or body.lstrip().startswith("event:") \
                or "data:" in body[:200]:
            for line in body.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    message = json.loads(chunk)
                except ValueError:
                    continue
                if isinstance(message, dict) and str(message.get("id")) == str(request_id):
                    return message
            return None
        try:
            message = json.loads(body)
        except ValueError:
            return None
        if isinstance(message, dict) and "jsonrpc" in message:
            return message
        if isinstance(message, list):  # сервер может ответить пачкой
            for item in message:
                if isinstance(item, dict) and str(item.get("id")) == str(request_id):
                    return item
        return None

    def close(self) -> None:
        return None

    def alive(self) -> bool:
        return True


# -------------------------------------------------------------------- клиент
class McpClient:
    """Один сервер MCP: рукопожатие, список инструментов, вызов инструмента."""

    def __init__(self, name: str, transport: Any, *, timeout: float = 20.0):
        self.name = name
        self.transport = transport
        self.timeout = timeout
        self.server_info: dict[str, Any] = {}
        self.connected = False

    def connect(self, timeout: float | None = None) -> dict[str, Any]:
        wait = timeout if timeout is not None else self.timeout
        result = self.transport.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
        }, timeout=wait)
        info = result if isinstance(result, dict) else {}
        self.server_info = info.get("serverInfo") or {}
        version = str(info.get("protocolVersion") or "")
        if version and version != PROTOCOL_VERSION:
            log.info("сервер %s использует версию протокола %s (Jarvis — %s)",
                     self.name, version, PROTOCOL_VERSION)
        self.transport.notify("notifications/initialized", {})
        self.connected = True
        return self.server_info

    def list_tools(self, timeout: float | None = None) -> list[dict[str, Any]]:
        wait = timeout if timeout is not None else self.timeout
        result = self.transport.request("tools/list", {}, timeout=wait)
        tools = (result or {}).get("tools") if isinstance(result, dict) else None
        return [item for item in (tools or []) if isinstance(item, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None,
                  timeout: float | None = None) -> str:
        wait = timeout if timeout is not None else self.timeout
        result = self.transport.request("tools/call", {"name": name, "arguments": arguments or {}},
                                        timeout=wait)
        return extract_text(result)

    def close(self) -> None:
        try:
            self.transport.close()
        finally:
            self.connected = False

    def alive(self) -> bool:
        return bool(self.connected and self.transport.alive())


def extract_text(result: Any) -> str:
    """Текст результата ``tools/call``: content может быть списком частей."""
    if not isinstance(result, dict):
        return str(result or "")
    if result.get("isError"):
        text = extract_text({**result, "isError": False})
        raise McpError(f"инструмент вернул ошибку: {text[:300] or 'без описания'}")
    parts = result.get("content")
    if isinstance(parts, list):
        chunks: list[str] = []
        for item in parts:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "text":
                chunks.append(str(item.get("text", "")))
            elif kind == "resource":
                resource = item.get("resource") or {}
                chunks.append(str(resource.get("text") or resource.get("uri") or ""))
            elif kind == "image":
                chunks.append(f"[картинка {item.get('mimeType', '')}]")
        text = "\n".join(chunk for chunk in chunks if chunk)
    else:
        text = json.dumps(result.get("structuredContent", result), ensure_ascii=False, default=str)
    return text.strip()


def resolve_env(values: dict[str, Any] | None) -> dict[str, str]:
    """Раскрывает ``${ИМЯ}`` в значениях окружения из переменных и ``.env``.

    Ключи серверов MCP (например, токен GitHub) живут только в переменных
    окружения — в файле настроек остаётся ссылка, и значение никогда не
    попадает ни в журнал, ни в окно.
    """
    from ..core.config import resolve_refs

    resolved: dict[str, str] = {}
    for key, value in (values or {}).items():
        text, missing = resolve_refs(str(value))
        if missing:
            raise McpError(f"в окружении нет {', '.join(missing)} (нужно для {key})")
        resolved[str(key)] = text
    return resolved


def wait_until(predicate: Callable[[], bool], timeout: float, step: float = 0.05) -> bool:
    """Небольшой помощник для тестов и ожиданий: ждать выполнения условия."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()
