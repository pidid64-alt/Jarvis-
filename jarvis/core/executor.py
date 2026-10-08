"""Исполнение действий навыков: права → подтверждение → запуск → результат.

Здесь же закрывается старая проблема «молчаливого успеха»: код возврата
команды проверяется всегда, ошибка попадает и в журнал, и в ответ пользователю.
"""

from __future__ import annotations

import threading
from typing import Any

from . import paths
from .context import SkillContext
from .errors import (
    CommandFailedError,
    JarvisError,
    PermissionDeniedError,
    SkillError,
)
from .i18n import get_translator
from .logging_setup import get_logger
from .types import Intent, Reply, State

log = get_logger("core.executor")


class Executor:
    """Выполняет намерение от имени ядра."""

    def __init__(self, config: Any, policy: Any, registry: Any, journal: Any = None):
        self.config = config
        self.policy = policy
        self.registry = registry
        self.journal = journal

    # ------------------------------------------------------------------ entry
    def execute(self, intent: Intent, ctx: SkillContext) -> Reply:
        translator = get_translator(ctx.language)
        skill, action = intent.skill, intent.action

        try:
            decision = self.policy.require(skill, action)
        except PermissionDeniedError as exc:
            self._journal("warning", skill.id, action.id, f"запрещено: {exc}")
            return Reply(text=translator.t("error.permission_denied"), ok=False,
                         error=str(exc), skill=skill.id, action=action.id, state=State.ERROR)

        if decision.needs_confirmation and not self._confirm(intent, ctx):
            self._journal("info", skill.id, action.id, "пользователь отменил")
            return Reply(text=translator.t("reply.cancelled"), ok=False, error="cancelled",
                         skill=skill.id, action=action.id)

        try:
            if skill.entry:
                reply = self._run_handler(intent, ctx)
            else:
                reply = self._run_command(intent, ctx)
        except CommandFailedError as exc:
            self._journal("error", skill.id, action.id, f"команда не выполнена: {exc}")
            return Reply(text=translator.t("error.command_failed"), ok=False, error=str(exc),
                         skill=skill.id, action=action.id, state=State.ERROR,
                         data={"details": exc.details or ""})
        except SkillError as exc:
            self._journal("error", skill.id, action.id, f"ошибка навыка: {exc}")
            return Reply(text=translator.t("error.skill"), ok=False, error=str(exc),
                         skill=skill.id, action=action.id, state=State.ERROR,
                         data={"details": exc.details or ""})
        except JarvisError as exc:
            self._journal("error", skill.id, action.id, f"ошибка: {exc}")
            return Reply(text=translator.t(exc.user_message_key), ok=False, error=str(exc),
                         skill=skill.id, action=action.id, state=State.ERROR)
        except Exception as exc:  # noqa: BLE001 - навык не должен ронять ассистента
            log.exception("навык %s упал", skill.id)
            self._journal("error", skill.id, action.id, f"неожиданная ошибка: {exc}")
            return Reply(text=translator.t("error.skill"), ok=False, error=repr(exc),
                         skill=skill.id, action=action.id, state=State.ERROR)

        self._journal("info", skill.id, action.id, reply.text or action.response or "выполнено",
                      score=f"{intent.score:.2f}", route=intent.source)
        return reply

    # ------------------------------------------------------------- helpers
    def _confirm(self, intent: Intent, ctx: SkillContext) -> bool:
        translator = get_translator(ctx.language)
        question = intent.action.confirm_prompt or translator.t(
            "confirm.default", action=intent.action.description or intent.action.id
        )
        if ctx.confirm_callback is None:
            log.warning("нет канала подтверждения для %s", intent.full_id)
            return False
        try:
            return bool(ctx.confirm_callback(question))
        except Exception:  # noqa: BLE001
            log.exception("канал подтверждения упал")
            return False

    def _run_handler(self, intent: Intent, ctx: SkillContext) -> Reply:
        skill = intent.skill
        ctx.extra["skill_id"] = skill.id
        function = self.registry.handler_callable(skill, intent.action)
        if function is None:
            raise SkillError(
                f"{skill.id}: для действия {intent.action.id} нет функции-обработчика",
                details="ожидается HANDLERS[action_id] или функция с именем действия",
            )
        result = function(ctx, intent)
        return self._normalize(result, intent, ctx)

    def _run_command(self, intent: Intent, ctx: SkillContext) -> Reply:
        action = intent.action
        translator = get_translator(ctx.language)
        providers = ctx.providers

        if not action.command:
            raise SkillError(f"{intent.full_id}: не задана команда")

        if action.background:
            if action.speak_before and action.response and providers:
                providers.speak(action.response, language=ctx.language)
            process = ctx.spawn(action.command)
            text = action.response or translator.t("reply.started_in_background")
            self._watch_background(intent, ctx, process)
            return Reply(text=text, skill=intent.skill.id, action=action.id)

        if action.speak_before and action.response and providers:
            # Сначала говорим, потом запускаем (shutdown/suspend иначе не успеют)
            providers.speak(action.response, language=ctx.language)
            ctx.run(action.command, timeout=action.timeout, background=True)
            return Reply(text=action.response, skill=intent.skill.id, action=action.id)

        # Ошибку команды показываем пользователю, а не прячем за «Готово»:
        # тихий успех при ненулевом коде возврата — прямой запрет в задании.
        output = ctx.run(action.command, timeout=action.timeout,
                         check=not action.allow_failure)
        if action.speak_output and output:
            text = f"{action.response} {output}".strip()
        else:
            text = action.response or (output if output else translator.t("reply.done"))
        return Reply(text=text.strip(), skill=intent.skill.id, action=action.id)

    def _watch_background(self, intent: Intent, ctx: SkillContext, process) -> None:
        """Дождёмся фоновой команды отдельным потоком и сообщим её итог.

        Раньше итог сообщался по таймеру, даже если команда ещё работала или
        упала. Теперь ждём процесс и говорим «выполнено» только при коде 0.
        """
        action = intent.action
        translator = get_translator(ctx.language)

        def watcher() -> None:
            providers = ctx.providers
            message = action.done_message or translator.t("reply.background_done", action=action.id)
            try:
                code = process.wait(timeout=max(1.0, min(action.timeout, 300.0)))
            except Exception:  # noqa: BLE001 - процесс мог не запуститься или ждать дольше
                code = None
            if code is None:
                message = translator.t("reply.background_long", action=action.id)
            elif code not in (0, None):
                self._journal("warning", intent.skill.id, action.id, f"фоновая команда вернула код {code}")
                message = translator.t("reply.background_failed", action=action.id)
            if providers is not None:
                providers.notify("Jarvis", message)
                providers.speak(message, language=ctx.language)

        thread = threading.Thread(target=watcher, name=f"jarvis-bg-{action.id}", daemon=True)
        thread.start()

    def _normalize(self, result: Any, intent: Intent, ctx: SkillContext) -> Reply:
        translator = get_translator(ctx.language)
        if isinstance(result, Reply):
            reply = result
        elif isinstance(result, str):
            reply = Reply(text=result)
        elif isinstance(result, dict):
            reply = Reply(
                text=str(result.get("text", "")),
                speech=result.get("speech"),
                ok=bool(result.get("ok", True)),
                data=result.get("data", {}) or {},
                continue_dialog=bool(result.get("continue_dialog", False)),
            )
        elif result is None:
            reply = Reply(text=intent.action.response or translator.t("reply.done"))
        else:
            raise SkillError(f"{intent.full_id}: навык вернул {type(result).__name__}, ожидался Reply/str/dict")
        reply.skill = reply.skill or intent.skill.id
        reply.action = reply.action or intent.action.id
        if not reply.text and not reply.speech:
            reply.text = intent.action.response or translator.t("reply.done")
        return reply

    def _journal(self, level: str, skill: str, action: str, message: str, **extra: Any) -> None:
        if self.journal is None:
            return
        self.journal.add(level, f"skill.{skill}", f"{action}: {message}", **extra)
