"""Типы ядра: действия, навыки, намерения, ответы."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class State(str, Enum):
    """Состояние ассистента — его показывает индикатор в GUI."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    WAITING_CONFIRMATION = "waiting_confirmation"
    ERROR = "error"


@dataclass
class Permissions:
    """Запрошенные навыком возможности (объявляются в skill.json)."""

    shell: bool = False
    files: bool = False
    network: bool = False
    notify: bool = True
    dangerous: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Permissions":
        data = data or {}
        known = {"shell", "files", "network", "notify", "dangerous"}
        return cls(
            shell=bool(data.get("shell", False)),
            files=bool(data.get("files", False)),
            network=bool(data.get("network", False)),
            notify=bool(data.get("notify", True)),
            dangerous=bool(data.get("dangerous", False)),
            extra={key: value for key, value in data.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "shell": self.shell,
            "files": self.files,
            "network": self.network,
            "notify": self.notify,
            "dangerous": self.dangerous,
            **self.extra,
        }

    def describe(self, language: str = "ru") -> list[str]:
        """Человекочитаемый список прав для страницы «Навыки»."""
        labels_ru = {
            "shell": "Запускать команды системы",
            "files": "Работать с файлами",
            "network": "Выходить в сеть",
            "notify": "Показывать уведомления",
            "dangerous": "Может менять систему (спрашивает подтверждение)",
        }
        labels_en = {
            "shell": "Run system commands",
            "files": "Work with files",
            "network": "Access the network",
            "notify": "Show notifications",
            "dangerous": "Can change the system (asks for confirmation)",
        }
        labels = labels_en if language.startswith("en") else labels_ru
        return [labels[key] for key in ("shell", "files", "network", "notify", "dangerous") if getattr(self, key)]


@dataclass
class Action:
    """Одно умение внутри навыка.

    ``command`` — статичный шаблон команды, написанный человеком.
    Распознанный текст никогда не попадает в него: он только ВЫБИРАЕТ действие.
    """

    id: str
    phrases: list[str]
    description: str = ""
    command: str | None = None
    response: str = ""
    speak_output: bool = False
    speak_before: bool = False
    background: bool = False
    confirm: bool = False
    confirm_prompt: str = ""
    timeout: float = 15.0
    min_score: float | None = None
    tags: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)  # пусто = все платформы
    handler: str | None = None  # для навыков с кодом
    done_message: str = ""
    #: ненулевой код возврата у этого действия допустим (например, grep без совпадений)
    allow_failure: bool = False
    #: действие принимает «хвост» фразы как аргумент (напр. «найди <запрос>»)
    capture: bool = False
    capture_field: str = "query"
    #: действие разрешено запускать автономным проверкам (без пользователя)
    autonomy_safe: bool = False
    #: имена аргументов, которые модель может передать этому действию
    #: (например, получатель и текст сообщения). Пусто — модель аргументов не даёт.
    model_args: list[str] = field(default_factory=list)
    danger: bool = False  # вычисляется политикой прав

    @property
    def is_dangerous(self) -> bool:
        return self.danger or self.confirm or "dangerous" in self.tags


@dataclass
class Skill:
    """Навык-плагин: одна папка = один навык."""

    id: str
    name: str
    description: str
    version: str = "1.0"
    path: Path | None = None
    enabled: bool = True
    permissions: Permissions = field(default_factory=Permissions)
    actions: list[Action] = field(default_factory=list)
    entry: str | None = None  # имя python-модуля внутри папки навыка
    examples: list[str] = field(default_factory=list)
    builtin: bool = True
    hidden: bool = False

    def action(self, action_id: str) -> Action | None:
        for item in self.actions:
            if item.id == action_id:
                return item
        return None

    def to_public_dict(self) -> dict[str, Any]:
        """Описание навыка для GUI/API — без внутренних путей."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "enabled": self.enabled,
            "builtin": self.builtin,
            "hidden": self.hidden,
            "permissions": self.permissions.to_dict(),
            "examples": list(self.examples),
            "actions": [
                {
                    "id": action.id,
                    "description": action.description,
                    "phrases": list(action.phrases),
                    "confirm": action.confirm or action.is_dangerous,
                    "tags": list(action.tags),
                }
                for action in self.actions
            ],
        }


@dataclass
class Intent:
    """Выбранное действие плюс то, как оно было выбрано."""

    action: Action
    skill: Skill
    score: float = 1.0
    source: str = "exact"  # exact | fuzzy | llm | direct
    text: str = ""
    args: dict[str, Any] = field(default_factory=dict)

    @property
    def full_id(self) -> str:
        """``volume.gromche`` — уникальный адрес действия для LLM и журнала."""
        return f"{self.skill.id}.{self.action.id}"


@dataclass
class Reply:
    """Ответ ассистента: что показать и что произнести."""

    text: str = ""
    speech: str | None = None
    state: State = State.IDLE
    ok: bool = True
    error: str | None = None
    skill: str | None = None
    action: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    await_confirmation: bool = False
    continue_dialog: bool = False

    def spoken_text(self) -> str:
        return self.speech if self.speech is not None else self.text

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "speech": self.spoken_text(),
            "state": self.state.value,
            "ok": self.ok,
            "error": self.error,
            "skill": self.skill,
            "action": self.action,
            "await_confirmation": self.await_confirmation,
            "continue_dialog": self.continue_dialog,
            "data": self.data,
        }


@dataclass
class RequestContext:
    """Всё, что нужно навыку для работы: провайдеры, права, лог, интерфейс."""

    config: Any
    permissions: Any
    providers: Any
    language: str = "ru"
    source: str = "cli"  # cli | gui | voice | api
    confirm: Any = None  # callable(question) -> bool
    say: Any = None  # callable(text) -> None, беззвучный вывод в интерфейс
    log: Any = None
    extra: dict[str, Any] = field(default_factory=dict)
