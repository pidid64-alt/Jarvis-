"""Реестр навыков: находит плагины, лениво их загружает, хранит вкл/выкл.

Навык — это папка с ``skill.json`` (описание, фразы, права) и, если нужно,
кодом ``handler.py``. Добавить навык = положить папку в
``<каталог настроек>/skills`` — ядро править не нужно.

При старте читаются только описания (``skill.json``), код навыка
импортируется в момент первого вызова — это экономит память на слабой машине.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from . import paths
from .errors import SkillError
from .permissions import PermissionPolicy
from .types import Action, Permissions, Skill

REQUIRED_FIELDS = ("id", "name")
ACTION_LIST_FIELDS = {
    "phrases": list,
    "tags": list,
    "platforms": list,
}


def _action_from_dict(skill_id: str, data: dict[str, Any]) -> Action:
    if not isinstance(data, dict) or not data.get("id"):
        raise SkillError(f"{skill_id}: у действия нет id")
    phrases = data.get("phrases") or []
    if not isinstance(phrases, list) or not phrases:
        raise SkillError(f"{skill_id}.{data.get('id')}: не заданы фразы вызова")
    for field, expected in ACTION_LIST_FIELDS.items():
        value = data.get(field, [])
        if value and not isinstance(value, expected):
            raise SkillError(f"{skill_id}.{data['id']}: поле {field} должно быть списком")
    return Action(
        id=str(data["id"]),
        phrases=[str(item) for item in phrases],
        description=str(data.get("description", "")),
        command=data.get("command"),
        response=str(data.get("response", "")),
        speak_output=bool(data.get("speak_output", False)),
        speak_before=bool(data.get("speak_before", False)),
        background=bool(data.get("background", False)),
        confirm=bool(data.get("confirm", False)),
        confirm_prompt=str(data.get("confirm_prompt", "")),
        timeout=float(data.get("timeout", 15)),
        min_score=float(data["min_score"]) if data.get("min_score") is not None else None,
        tags=[str(tag) for tag in data.get("tags", [])],
        platforms=[str(item) for item in data.get("platforms", [])],
        handler=data.get("handler"),
        done_message=str(data.get("done_message", "")),
        allow_failure=bool(data.get("allow_failure", False)),
        capture=bool(data.get("capture", False)),
        capture_field=str(data.get("capture_field", "query")),
        autonomy_safe=bool(data.get("autonomy_safe", False)),
    )


def skill_from_dict(data: dict[str, Any], path: Path | None, *, builtin: bool = True) -> Skill:
    for field in REQUIRED_FIELDS:
        if not data.get(field):
            raise SkillError(f"{path}: в skill.json нет поля {field}")
    actions_data = data.get("actions")
    actions: list[Action] = []
    entry = data.get("entry")
    if entry is None and actions_data is None:
        # кодовый навык без списка действий: действия объявляет сам handler
        actions = []
    elif isinstance(actions_data, list):
        actions = [_action_from_dict(str(data["id"]), item) for item in actions_data]
    elif actions_data is not None:
        raise SkillError(f"{path}: actions должен быть списком")
    return Skill(
        id=str(data["id"]),
        name=str(data["name"]),
        description=str(data.get("description", "")),
        version=str(data.get("version", "1.0")),
        path=path,
        permissions=Permissions.from_dict(data.get("permissions")),
        actions=actions,
        entry=str(entry) if entry else None,
        examples=[str(item) for item in data.get("examples", [])],
        builtin=builtin,
        hidden=bool(data.get("hidden", False)),
    )


class SkillRegistry:
    """Все навыки, известные Jarvis, с учётом включённых и выключенных."""

    def __init__(self, policy: PermissionPolicy, *, disabled: Iterable[str] = (), journal: Any = None):
        self.policy = policy
        self._disabled: set[str] = set(disabled)
        self._skills: dict[str, Skill] = {}
        self._handlers: dict[str, Any] = {}
        self.journal = journal
        self.problems: list[str] = []

    # ------------------------------------------------------------------ scan
    def scan(self) -> list[Skill]:
        """Перечитывает навыки из встроенной и пользовательской папок."""
        self._skills.clear()
        self.problems.clear()
        for folder, builtin in ((paths.bundled_skills_dir(), True), (paths.user_skills_dir(), False)):
            if not folder.is_dir():
                continue
            for skill_dir in sorted(item for item in folder.iterdir() if item.is_dir()):
                manifest = skill_dir / "skill.json"
                if not manifest.is_file():
                    continue
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    skill = skill_from_dict(data, skill_dir, builtin=builtin)
                except (OSError, ValueError, SkillError) as exc:
                    message = f"{skill_dir.name}: {exc}"
                    self.problems.append(message)
                    if self.journal:
                        self.journal.warning("core.registry", f"навык не загружен — {message}")
                    continue
                for action in skill.actions:
                    self.policy.mark_dangerous(action)
                skill.enabled = skill.id not in self._disabled
                if skill.id in self._skills and builtin:
                    continue  # пользовательский навык с тем же id важнее
                self._skills[skill.id] = skill
        return list(self._skills.values())

    # ----------------------------------------------------------------- access
    def all(self, *, include_hidden: bool = False) -> list[Skill]:
        items = [skill for skill in self._skills.values() if include_hidden or not skill.hidden]
        return sorted(items, key=lambda skill: skill.name.casefold())

    def enabled(self, *, include_hidden: bool = False) -> list[Skill]:
        return [skill for skill in self.all(include_hidden=include_hidden) if skill.enabled]

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def find_action(self, full_id: str) -> tuple[Skill, Action] | None:
        """``volume.up`` → (навык, действие)."""
        if "." not in full_id:
            return None
        skill_id, action_id = full_id.split(".", 1)
        skill = self._skills.get(skill_id)
        if skill is None:
            return None
        action = skill.action(action_id)
        return (skill, action) if action else None

    def flat_action_ids(self) -> list[str]:
        return [f"{skill.id}.{action.id}" for skill in self.all() for action in skill.actions]

    # ------------------------------------------------------- enable / disable
    def is_disabled(self, skill_id: str) -> bool:
        return skill_id in self._disabled

    def set_enabled(self, skill_id: str, enabled: bool) -> Skill:
        skill = self._skills.get(skill_id)
        if skill is None:
            raise SkillError(f"навык {skill_id} не найден")
        if enabled:
            self._disabled.discard(skill_id)
        else:
            self._disabled.add(skill_id)
        skill.enabled = enabled
        return skill

    @property
    def disabled_ids(self) -> list[str]:
        return sorted(self._disabled)

    # --------------------------------------------------------------- handlers
    def handler(self, skill: Skill) -> Any:
        """Импортирует код навыка при первом обращении (ленивая загрузка)."""
        if skill.id in self._handlers:
            return self._handlers[skill.id]
        if not skill.entry or not skill.path:
            raise SkillError(f"{skill.id}: у навыка нет входного файла")
        entry_path = skill.path / skill.entry
        if not entry_path.is_file():
            raise SkillError(f"{skill.id}: не найден {skill.entry}")
        module_name = f"jarvis_skill_{skill.id}"
        spec = importlib.util.spec_from_file_location(module_name, entry_path)
        if spec is None or spec.loader is None:
            raise SkillError(f"{skill.id}: не удалось загрузить {entry_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - показываем пользователю, что навык сломан
            sys.modules.pop(module_name, None)
            raise SkillError(f"{skill.id}: ошибка загрузки — {exc}") from exc
        declared = getattr(module, "ACTIONS", None)
        if declared and not skill.actions:
            skill.actions = [_action_from_dict(skill.id, item) for item in declared]
            for action in skill.actions:
                self.policy.mark_dangerous(action)
        self._handlers[skill.id] = module
        return module

    def handler_callable(self, skill: Skill, action: Action) -> Callable[..., Any] | None:
        module = self.handler(skill)
        mapping = getattr(module, "HANDLERS", None)
        if isinstance(mapping, dict) and action.id in mapping:
            return mapping[action.id]
        function = getattr(module, action.id, None)
        if callable(function):
            return function
        if callable(getattr(module, "handle", None)):
            return getattr(module, "handle")
        return None
