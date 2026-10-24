"""Контекст, который ядро передаёт навыку.

Навык не ходит в систему сам: он просит контекст выполнить команду, сказать
текст или уточнить у пользователя. Ядро решает, разрешено ли это (права),
пишет журнал и следит за таймаутами.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import paths
from .errors import CommandFailedError, PermissionDeniedError
from .logging_setup import get_logger

PLACEHOLDERS = ("base", "legacy", "state", "config", "home")


@dataclass
class SkillContext:
    """То, что видит навык: провайдеры, права, лог, помощники."""

    config: Any
    permissions: Any
    providers: Any
    registry: Any = None
    journal: Any = None
    language: str = "ru"
    source: str = "cli"
    confirm_callback: Callable[[str], bool] | None = None
    say_callback: Callable[[str], None] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # --------------------------------------------------------------- helpers
    @property
    def log(self):
        return get_logger(f"skill.{self.extra.get('skill_id', 'unknown')}")

    def t(self, key: str, **values: Any) -> str:
        from .i18n import get_translator

        return get_translator(self.language).t(key, **values)

    def platform_name(self) -> str:
        """Имя текущей платформы: ``linux`` или ``windows``."""
        from ..platform import platform_name

        return platform_name()

    def substitute(self, template: str) -> str:
        """``{legacy}/scripts/x.sh`` → абсолютный путь."""
        return paths.substitute(template)

    def run(
        self,
        template: str,
        *,
        timeout: float = 15.0,
        background: bool = False,
        check: bool = True,
        allow_failure: bool = False,
        cwd: Path | None = None,
    ) -> str:
        """Выполняет статичную команду навыка (шаблон, написанный человеком).

        Аварийный код возврата — это ошибка, а не «готово»: исключение увидит
        ядро и покажет человеку понятный текст. Если сбой для команды нормален
        (пустой grep, отсутствующий инструмент), навык ставит ``allow_failure``.
        """
        if allow_failure:
            check = False
        if not (self.permissions.shell or self.permissions.dangerous):
            raise PermissionDeniedError("навыку не разрешён запуск команд")
        command = self.substitute(template)
        from ..platform import get_platform

        platform = get_platform()
        if background:
            platform.spawn(command, cwd=cwd or paths.PROJECT_ROOT)
            return ""
        result = platform.run(command, timeout=timeout, cwd=cwd or paths.PROJECT_ROOT)
        if check and result.returncode != 0:
            raise CommandFailedError(
                f"код {result.returncode}: {result.output[:200]}",
                details=command,
            )
        return result.output

    def spawn(self, template: str, *, cwd: Path | None = None):
        """Запускает команду навыка в фоне и возвращает процесс.

        Ядро по этому процессу узнаёт код возврата и честно сообщает итог:
        «выполнено» или «завершилось с ошибкой».
        """
        if not (self.permissions.shell or self.permissions.dangerous):
            raise PermissionDeniedError("навыку не разрешён запуск команд")
        from ..platform import get_platform

        return get_platform().spawn(self.substitute(template), cwd=cwd or paths.PROJECT_ROOT)

    def say(self, text: str) -> None:
        if self.say_callback and text:
            self.say_callback(text)

    def notify(self, title: str, body: str, urgency: str = "normal") -> None:
        if self.permissions.notify and self.providers is not None:
            self.providers.notify(title, body, urgency)

    def confirm(self, question: str) -> bool:
        if self.confirm_callback is None:
            return False
        return bool(self.confirm_callback(question))

    def speak(self, text: str) -> None:
        """Озвучить текст через TTS (если голос включён)."""
        if self.providers is not None:
            self.providers.speak(text, language=self.language)

    # ------------------------------------------------------------------ data
    @property
    def state_dir(self) -> Path:
        return paths.state_dir()

    @property
    def config_dir(self) -> Path:
        return paths.config_dir()

    @property
    def project_root(self) -> Path:
        return paths.PROJECT_ROOT

    @property
    def legacy_dir(self) -> Path:
        return paths.legacy_dir()


def make_context(**kwargs: Any) -> SkillContext:
    return SkillContext(**kwargs)
