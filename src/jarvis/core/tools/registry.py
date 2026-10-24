"""Единый реестр инструментов: регистрация, исполнение, гейт подтверждений, аудит."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from jarvis.core.audit import AuditLog
from jarvis.core.llm.base import ToolSchema
from jarvis.core.tools.base import (
    Confirmer,
    ConfirmRequest,
    SideEffect,
    Tool,
    ToolError,
    ToolResult,
)
from jarvis.core.tools.policy import Policy

log = logging.getLogger("jarvis.tools.registry")

# Наблюдатель событий исполнения: observer(событие, tool=..., **детали)
Observer = Callable[..., None]


class ToolRegistry:
    def __init__(self, policy: Policy, audit: AuditLog, observer: Observer | None = None):
        self._policy = policy
        self._audit = audit
        self._observer = observer
        self._tools: dict[str, Tool] = {}

    # ---------- управление ----------

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Инструмент {tool.name!r} уже зарегистрирован")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def unregister_mcp(self) -> None:
        """Снимает все инструменты, добавленные MCP-серверами (для hot reload)."""
        for name in [n for n in self._tools if n.startswith("mcp__")]:
            del self._tools[name]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(name=t.name, description=t.description, parameters=t.parameters)
            for t in self._tools.values()
        ]

    # ---------- исполнение ----------

    async def execute(self, name: str, args: dict, confirmer: Confirmer | None) -> ToolResult:
        """Исполняет инструмент через политику: проверка → подтверждение → вызов → аудит.

        Ошибки не бросаются наружу, а возвращаются моделью наблюдений —
        агентный цикл должен продолжаться.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(output=f"Ошибка: инструмент {name!r} не найден.")

        decision = self._policy.decide(tool, args)
        self._notify("start", tool=name, decision=decision)

        if decision == "confirm":
            reason = _reason_for(tool.side_effect)
            if confirmer is None:
                await self._audit.log(name, args, decision, "denied", error="нет интерфейса подтверждения")
                self._notify("denied", tool=name)
                return ToolResult(
                    output=f"Действие {name!r} требует подтверждения, но интерфейс недоступен. Действие не выполнено."
                )
            request = ConfirmRequest(tool=tool, args=args, reason=reason)
            approved = await confirmer(request)
            if not approved:
                await self._audit.log(name, args, decision, "denied", confirmed_by="user")
                self._notify("denied", tool=name)
                return ToolResult(output=f"Пользователь отклонил выполнение {name!r}.")
            await self._audit.log(name, args, decision, "approved", confirmed_by="user")
            self._notify("approved", tool=name)

        started = time.monotonic()
        try:
            result = await asyncio.wait_for(tool.handler(dict(args)), timeout=tool.timeout)
        except TimeoutError:
            await self._audit.log(name, args, decision, "error", error="таймаут")
            self._notify("error", tool=name, error="таймаут")
            return ToolResult(output=f"Ошибка: инструмент {name!r} превысил лимит времени ({tool.timeout:.0f} с).")
        except ToolError as e:
            await self._audit.log(name, args, decision, "error", error=str(e))
            self._notify("error", tool=name, error=str(e))
            return ToolResult(output=f"Ошибка инструмента {name!r}: {e}")
        except Exception as e:  # непредвиденное — не роняем агентный цикл
            log.exception("Сбой инструмента %s", name)
            await self._audit.log(name, args, decision, "error", error=f"{type(e).__name__}: {e}")
            self._notify("error", tool=name, error=str(e))
            return ToolResult(output=f"Внутренняя ошибка инструмента {name!r}: {type(e).__name__}.")

        elapsed = time.monotonic() - started
        await self._audit.log(name, args, decision, "ok")
        self._notify("done", tool=name, seconds=elapsed, untrusted=result.untrusted)
        return result

    def _notify(self, event: str, **details) -> None:
        if self._observer is not None:
            try:
                self._observer(event, **details)
            except Exception:
                log.debug("Наблюдатель событий упал", exc_info=True)


def _reason_for(side_effect: SideEffect) -> str:
    return {
        "outbound": "исходящее действие во внешний мир",
        "high_stakes": "действие повышенного риска (финансы/документы/обещания)",
    }.get(side_effect, "требуется подтверждение")
