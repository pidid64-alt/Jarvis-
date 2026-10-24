"""Права навыков: что каждому разрешено и когда нужно подтверждение.

Модель простая и намеренно консервативная:

* навык объявляет в ``skill.json`` запрошенные возможности
  (``shell``, ``files``, ``network``, ``notify``);
* ядро может сузить их (режим ``restricted`` — по умолчанию);
* опасные действия требуют подтверждения пользователя **всегда**,
  независимо от того, из какого интерфейса пришла команда;
* если шаблон команды похож на опасный (``rm -rf``, ``sudo``, ``systemctl
  poweroff`` и т. п.), подтверждение включается даже для навыка без флага.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import PermissionDeniedError
from .types import Action, Permissions, Skill

#: Шаблоны, при виде которых действие считается опасным автоматически.
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf]", "удаление файлов"),
    (r"\brm\s+-rf\b", "удаление файлов"),
    (r"\bsudo\b", "повышение прав"),
    (r"\bdoas\b", "повышение прав"),
    (r"\bmkfs\b", "форматирование диска"),
    (r"\bdd\s+if=.*of=/dev/", "запись на диск"),
    (r"(?i)\b(shutdown|poweroff|reboot|halt)\b", "выключение или перезагрузка"),
    (r"systemctl\s+(poweroff|reboot|suspend|hibernate|stop|disable)", "управление службами"),
    (r"(?i)\b(pacman|apt|dnf|yum|zypper)\s+.*(-S|install|-R|remove|upgrade)", "установка/удаление пакетов"),
    (r"(?i)\breg\s+(add|delete)\b", "правка реестра"),
    (r"(?i)\b(taskkill|Stop-Process|Remove-Item)\b", "удаление процессов или файлов"),
    (r"(?i)\bdel\s+/[fqs]", "удаление файлов"),
    (r"(?i)\bformat\s+[a-z]:", "форматирование диска"),
    (r"(?i)\b(shutdown|Restart-Computer|Stop-Computer)\b", "выключение компьютера"),
    (r"(?i)\bStop-Service\b", "остановка служб"),
]

DANGEROUS_RE = [re.compile(pattern) for pattern, _ in DANGEROUS_PATTERNS]

#: Действия, которые «действуют от имени пользователя» и всегда требуют подтверждения.
OUTGOING_PATTERNS = [
    (r"(?i)\bdiscord_message", "отправка сообщения"),
    (r"(?i)\btelegram", "отправка сообщения"),
    (r"(?i)\bmail\b", "отправка почты"),
    (r"(?i)xdotool\s+.*\bkey\b.*\bReturn\b", "нажатие Enter в чужом окне"),
]


@dataclass
class Decision:
    """Результат проверки прав."""

    allowed: bool
    needs_confirmation: bool = False
    reason: str = ""


class PermissionPolicy:
    """Единая точка принятия решения «можно/нельзя/спросить»."""

    def __init__(self, mode: str = "restricted", *, allow_extra_patterns: bool = True):
        self.mode = mode if mode in {"restricted", "normal"} else "restricted"
        self.allow_extra_patterns = allow_extra_patterns

    # ------------------------------------------------------------------ checks
    def mark_dangerous(self, action: Action) -> Action:
        """Проставляет ``danger`` по содержимому шаблона команды."""
        if action.danger:
            return action
        if action.confirm or "dangerous" in action.tags:
            action.danger = True
            return action
        if not self.allow_extra_patterns or not action.command:
            return action
        for index, regex in enumerate(DANGEROUS_RE):
            if regex.search(action.command):
                action.danger = True
                action.tags = [*action.tags, "dangerous"]
                action.confirm_prompt = action.confirm_prompt or (
                    f"Это опасное действие ({DANGEROUS_PATTERNS[index][1]}). Выполнить?"
                )
                break
        if not action.danger:
            for pattern, reason in OUTGOING_PATTERNS:
                regex = re.compile(pattern)
                if regex.search(action.command) or regex.search(action.id):
                    action.danger = True
                    action.tags = [*action.tags, "outgoing"]
                    action.confirm_prompt = action.confirm_prompt or (
                        f"Это действие от вашего имени ({reason}). Выполнить?"
                    )
                    break
        return action

    def check(self, skill: Skill, action: Action) -> Decision:
        """Проверяет запуск и решает, нужно ли подтверждение."""
        if not skill.enabled:
            return Decision(False, reason="skill_disabled")
        if action.command and not (skill.permissions.shell or skill.permissions.dangerous):
            return Decision(False, reason="shell_not_granted")
        # Подтверждение решается на уровне ДЕЙСТВИЯ, а не навыка: навык может
        # содержать и безобидные, и опасные команды одновременно (например,
        # «Питание» умеет и заблокировать экран, и выключить компьютер).
        # Флаг опасности самого навыка влияет на нечёткий поиск и на подпись
        # в окне «Навыки», но не заставляет переспрашивать всё подряд.
        if action.is_dangerous:
            return Decision(True, needs_confirmation=True, reason="dangerous_action")
        return Decision(True)

    def require(self, skill: Skill, action: Action) -> Decision:
        """Как ``check``, но отказ превращается в исключение с понятным текстом."""
        decision = self.check(skill, action)
        if not decision.allowed:
            raise PermissionDeniedError(
                f"{skill.id}.{action.id}: {decision.reason}",
                details=f"режим прав: {self.mode}",
            )
        return decision

    def describe_mode(self) -> str:
        return self.mode
