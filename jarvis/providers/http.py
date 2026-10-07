"""HTTP-клиент на стандартной библиотеке: таймауты, повторы, «предохранитель».

Внешних зависимостей нет: ``urllib`` хватает для JSON-запросов, а для загрузки
файла (распознавание речи) есть маленький собственный multipart-энкодер.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import ProviderError, ProviderTimeoutError, ProviderUnavailableError

log = logging.getLogger("jarvis.providers.http")

USER_AGENT = "Jarvis/1.0 (+local assistant)"


@dataclass
class CircuitBreaker:
    """После N подряд сбоев провайдер «остывает» и не тормозит каждую фразу."""

    name: str
    threshold: int = 3
    cooldown: float = 60.0
    failures: int = 0
    opened_at: float = 0.0
    last_error: str = ""

    def allow(self) -> bool:
        if self.failures < self.threshold:
            return True
        if time.monotonic() - self.opened_at >= self.cooldown:
            self.failures = 0
            self.opened_at = 0.0
            return True
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0
        self.last_error = ""

    def record_failure(self, error: str) -> None:
        self.failures += 1
        self.last_error = error
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()
            log.warning("провайдер %s временно отключён на %.0f с: %s", self.name, self.cooldown, error)

    def raise_if_open(self) -> None:
        if not self.allow():
            raise ProviderUnavailableError(
                f"{self.name}: временно недоступен ({self.last_error})",
                details=f"повторим через ~{self.cooldown:.0f} с",
            )

    def state(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "failures": self.failures,
            "available": self.allow(),
            "last_error": self.last_error,
        }


@dataclass
class HttpResult:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8", errors="replace") or "null")
        except ValueError as exc:
            raise ProviderError("ответ не является JSON") from exc


class HttpClient:
    """Тонкая обёртка над urllib с повторами и предохранителем."""

    def __init__(self, *, timeout: float = 10.0, retries: int = 1, breaker: CircuitBreaker | None = None):
        self.timeout = timeout
        self.retries = max(0, retries)
        self.breaker = breaker

    # ------------------------------------------------------------------ verbs
    def get(self, url: str, *, headers: dict[str, str] | None = None, timeout: float | None = None) -> HttpResult:
        return self._request("GET", url, headers=headers, timeout=timeout)

    def post_json(self, url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None,
                  timeout: float | None = None) -> HttpResult:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        merged = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
        return self._request("POST", url, data=body, headers=merged, timeout=timeout)

    def post_multipart(self, url: str, fields: dict[str, str], file_field: str, filename: str,
                       file_bytes: bytes, *, headers: dict[str, str] | None = None,
                       timeout: float | None = None) -> HttpResult:
        body, content_type = encode_multipart(fields, file_field, filename, file_bytes)
        merged = {"Content-Type": content_type, **(headers or {})}
        return self._request("POST", url, data=body, headers=merged, timeout=timeout)

    # --------------------------------------------------------------- internals
    def _request(self, method: str, url: str, *, data: bytes | None = None,
                 headers: dict[str, str] | None = None, timeout: float | None = None) -> HttpResult:
        if self.breaker is not None:
            self.breaker.raise_if_open()
        attempt = 0
        last_error: Exception | None = None
        while attempt <= self.retries:
            attempt += 1
            request = urllib.request.Request(url, data=data, method=method)
            request.add_header("User-Agent", USER_AGENT)
            for name, value in (headers or {}).items():
                request.add_header(name, value)
            try:
                with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                    result = HttpResult(
                        status=response.status,
                        body=response.read(),
                        headers=dict(response.headers.items()),
                    )
                if self.breaker is not None:
                    self.breaker.record_success()
                return result
            except urllib.error.HTTPError as exc:
                body = exc.read() if hasattr(exc, "read") else b""
                message = f"HTTP {exc.code}: {body[:200].decode('utf-8', 'replace')}"
                last_error = ProviderError(message)
                if 400 <= exc.code < 500 and exc.code != 429:
                    break  # бессмысленно повторять
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", exc)
                last_error = ProviderTimeoutError(str(reason)) if "timed out" in str(reason).lower() \
                    else ProviderError(str(reason))
            except TimeoutError as exc:  # сокет-таймаут
                last_error = ProviderTimeoutError(str(exc))
            except Exception as exc:  # noqa: BLE001 - сеть бывает разной
                last_error = ProviderError(str(exc))

            if attempt <= self.retries:
                time.sleep(0.3 * attempt)

        if self.breaker is not None:
            self.breaker.record_failure(str(last_error))
        raise last_error or ProviderError("неизвестная ошибка сети")


def encode_multipart(fields: dict[str, str], file_field: str, filename: str, file_bytes: bytes) -> tuple[bytes, str]:
    """Собирает multipart/form-data вручную (для STT-сервера)."""
    boundary = f"----JarvisBoundary{uuid.uuid4().hex}"
    lines: list[bytes] = []
    for name, value in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(str(value).encode("utf-8"))
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    lines.append(f"--{boundary}".encode())
    lines.append(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'.encode())
    lines.append(f"Content-Type: {content_type}".encode())
    lines.append(b"")
    lines.append(file_bytes)
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    return b"\r\n".join(lines), f"multipart/form-data; boundary={boundary}"
