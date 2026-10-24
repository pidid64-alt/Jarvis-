"""Клиент локального API: то, чем интерфейсы разговаривают с ядром.

Окно (Tkinter), трей и скрипты не трогают ядро напрямую — они находят адрес в
файле обнаружения ``{state}/api.json``, берут токен и делают обычные HTTP-запросы.
Благодаря этому интерфейс можно заменить, не меняя ядро.

Сеть здесь не нужна: адрес всегда ``127.0.0.1``, а HTTP-сервер поднимает ядро
(``jarvis run`` или само окно, если демон не запущен).
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.logging_setup import get_logger

log = get_logger("interfaces.client")

DEFAULT_TIMEOUT = 15.0
DISCOVERY_TIMEOUT = 3.0


class ApiUnavailable(RuntimeError):
    """Ядро не отвечает: демон не запущен или файл обнаружения устарел."""


@dataclass
class ApiTarget:
    url: str
    token: str
    pid: int = 0
    version: str = ""


def discovery_path() -> Path:
    return paths.api_file()


def read_target(*, verify: bool = True) -> ApiTarget | None:
    """Читает файл обнаружения и (по желанию) проверяет, что ядро живо."""
    path = discovery_path()
    token_path = paths.token_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.debug("файл обнаружения повреждён: %s", path)
        return None
    url = str(data.get("url") or f"http://{data.get('host', '127.0.0.1')}:{data.get('port', 0)}")
    token_file = Path(data.get("token_file") or token_path)
    if not token_file.exists():
        token_file = token_path
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not token:
        return None
    target = ApiTarget(url=url, token=token, pid=int(data.get("pid", 0)),
                       version=str(data.get("version", "")))
    if verify and not ping(target):
        return None
    return target


def ping(target: ApiTarget, *, timeout: float = DISCOVERY_TIMEOUT) -> bool:
    """Быстрая проверка живости: ``/health`` без токена."""
    try:
        with urllib.request.urlopen(f"{target.url}/health", timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


class ApiClient:
    """Небольшая обёртка над urllib: JSON, токен, понятные ошибки."""

    def __init__(self, target: ApiTarget, *, timeout: float = DEFAULT_TIMEOUT):
        self.target = target
        self.timeout = timeout
        self._seq = 0

    # ------------------------------------------------------------- создание
    @classmethod
    def discover(cls, *, timeout: float = DEFAULT_TIMEOUT, verify: bool = True) -> "ApiClient | None":
        target = read_target(verify=verify)
        return cls(target, timeout=timeout) if target else None

    @property
    def url(self) -> str:
        return self.target.url

    @property
    def pid(self) -> int:
        return self.target.pid

    # --------------------------------------------------------------- запросы
    def request(self, method: str, path: str, *, payload: dict[str, Any] | None = None,
                query: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        from urllib.parse import quote

        url = f"{self.target.url}{quote(path, safe='/?=&')}"
        if query:
            from urllib.parse import urlencode

            url += "?" + urlencode({key: value for key, value in query.items() if value is not None})
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("X-Jarvis-Token", self.target.token)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            try:
                parsed = json.loads(detail or "{}")
                message = parsed.get("message") or parsed.get("error") or f"HTTP {exc.code}"
            except ValueError:
                message = f"HTTP {exc.code}"
            raise ApiUnavailable(message) from exc
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise ApiUnavailable(f"ядро не отвечает: {exc}") from exc
        try:
            parsed = json.loads(body or "{}")
        except ValueError as exc:
            raise ApiUnavailable("ядро вернуло не JSON") from exc
        if not isinstance(parsed, dict):
            raise ApiUnavailable("неожиданный ответ ядра")
        return parsed

    def get(self, path: str, **query: Any) -> dict[str, Any]:
        return self.request("GET", path, query=query or None)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("POST", path, payload=payload or {})

    # ------------------------------------------------------- удобные обёртки
    def status(self) -> dict[str, Any]:
        return self.get("/status").get("status", {})

    def skills(self) -> list[dict[str, Any]]:
        return self.get("/skills").get("skills", [])

    def toggle_skill(self, skill_id: str, enabled: bool) -> dict[str, Any]:
        return self.post("/skills/toggle", {"id": skill_id, "enabled": enabled})

    def config(self) -> dict[str, Any]:
        return self.get("/config")

    def set_config(self, key: str, value: Any) -> dict[str, Any]:
        return self.post("/config", {"key": key, "value": value})

    def save_secret(self, name: str, value: str) -> dict[str, Any]:
        return self.post("/secret", {"name": name, "value": value})

    def journal(self, *, limit: int = 100, level: str | None = None, search: str = "") -> dict[str, Any]:
        return self.get("/journal", limit=limit, level=level, search=search)

    def report(self, *, limit: int = 300) -> str:
        return str(self.get("/journal/report", limit=limit).get("report", ""))

    def history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.get("/history", limit=limit).get("history", [])

    def events(self, *, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        return self.get("/events", since=since, limit=limit).get("events", [])

    def ask(self, text: str, *, speak: bool = False, source: str = "gui") -> dict[str, Any]:
        """Отправляет реплику. Возвращает ответ, вопрос о подтверждении или «работаю»."""
        return self.post("/ask", {"text": text, "speak": speak, "source": source})

    def voice(self, *, speak: bool = True, source: str = "gui") -> dict[str, Any]:
        return self.post("/voice", {"speak": speak, "source": source})

    def confirm(self, pending_id: str, approved: bool) -> dict[str, Any]:
        return self.post("/confirm", {"pending_id": pending_id, "approved": approved})

    def result(self, pending_id: str) -> dict[str, Any]:
        return self.get("/ask/result", pending_id=pending_id)

    def wait_result(self, pending_id: str, *, timeout: float = 30.0,
                    interval: float = 0.15) -> dict[str, Any]:
        """Ждёт итог запроса: нужен редко (обычно ответ приходит сразу)."""
        deadline = time.monotonic() + timeout
        while True:
            state = self.result(pending_id)
            if state.get("state") != "working":
                return state
            if time.monotonic() > deadline:
                return {"state": "timeout", "reply": None, "pending_id": pending_id}
            time.sleep(interval)

    def trigger(self) -> bool:
        return bool(self.post("/trigger").get("fired"))

    def shutdown(self) -> dict[str, Any]:
        return self.post("/shutdown")
