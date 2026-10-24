"""Тесты реестра навыков: загрузка, включение/выключение, ошибки навыков."""

from __future__ import annotations

import json
import unittest

from tests.helpers import isolated_home

from jarvis.core import paths
from jarvis.core.errors import SkillError
from jarvis.core.permissions import PermissionPolicy
from jarvis.core.registry import SkillRegistry, skill_from_dict


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self._home = isolated_home()
        self.home = self._home.__enter__()
        self.registry = SkillRegistry(PermissionPolicy())
        self.registry.scan()

    def tearDown(self):
        self._home.__exit__(None, None, None)

    def test_builtin_skills_found(self):
        ids = {skill.id for skill in self.registry.all()}
        self.assertIn("dialogue", ids)
        self.assertIn("system_health", ids)
        self.assertIn("power", ids)

    def test_actions_are_parsed(self):
        skill = self.registry.get("system_health")
        self.assertIsNotNone(skill)
        action = skill.action("memory")
        self.assertEqual(action.platforms, [])
        self.assertIn("сколько свободной памяти", action.phrases)

    def test_dangerous_marked_on_scan(self):
        power = self.registry.get("power")
        self.assertTrue(power.action("poweroff").is_dangerous)
        self.assertFalse(power.action("lock").is_dangerous)

    def test_user_skill_overrides_builtin(self):
        folder = paths.user_skills_dir() / "dialogue"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "skill.json").write_text(json.dumps({
            "id": "dialogue",
            "name": "Моя беседа",
            "description": "Переопределение",
            "permissions": {"notify": False},
            "actions": [{"id": "hello", "phrases": ["здорово"], "description": "Привет", "response": "Привет!"}],
        }, ensure_ascii=False), encoding="utf-8")
        registry = SkillRegistry(PermissionPolicy())
        registry.scan()
        skill = registry.get("dialogue")
        self.assertEqual(skill.name, "Моя беседа")
        self.assertFalse(skill.builtin)

    def test_broken_skill_reported_not_crashing(self):
        folder = paths.user_skills_dir() / "broken"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "skill.json").write_text("{ это не json", encoding="utf-8")
        registry = SkillRegistry(PermissionPolicy())
        registry.scan()
        self.assertTrue(registry.problems)
        self.assertIn("broken", registry.problems[0])

    def test_enable_disable(self):
        self.registry.set_enabled("power", False)
        self.assertFalse(self.registry.get("power").enabled)
        self.assertNotIn("power", {skill.id for skill in self.registry.enabled()})
        self.registry.set_enabled("power", True)
        self.assertTrue(self.registry.get("power").enabled)

    def test_disabled_ids_respected_on_scan(self):
        registry = SkillRegistry(PermissionPolicy(), disabled=["volume"])
        registry.scan()
        self.assertFalse(registry.get("volume").enabled)

    def test_handler_loaded_lazily(self):
        skill = self.registry.get("dialogue")
        self.assertNotIn("dialogue", self.registry._handlers)
        module = self.registry.handler(skill)
        self.assertTrue(hasattr(module, "HANDLERS"))
        self.assertIn("dialogue", self.registry._handlers)

    def test_handler_callable_resolution(self):
        skill = self.registry.get("dialogue")
        action = skill.action("hello")
        function = self.registry.handler_callable(skill, action)
        self.assertTrue(callable(function))

    def test_find_action_and_flat_ids(self):
        self.assertIsNotNone(self.registry.find_action("time_date.time"))
        self.assertIsNone(self.registry.find_action("time_date.неизвестно"))
        self.assertIn("dialogue.hello", self.registry.flat_action_ids())


class SkillParsingTests(unittest.TestCase):
    def test_missing_action_phrases_rejected(self):
        with self.assertRaises(SkillError):
            skill_from_dict({"id": "x", "name": "X", "actions": [{"id": "a"}]}, None)

    def test_missing_name_rejected(self):
        with self.assertRaises(SkillError):
            skill_from_dict({"id": "x"}, None)

    def test_platforms_and_capture_parsed(self):
        skill = skill_from_dict({
            "id": "x", "name": "X",
            "actions": [{"id": "a", "phrases": ["найди"], "capture": True,
                         "platforms": ["linux"], "command": "true", "timeout": 3}],
        }, None)
        action = skill.actions[0]
        self.assertTrue(action.capture)
        self.assertEqual(action.platforms, ["linux"])
        self.assertEqual(action.timeout, 3)


class BuiltinSkillsIntegrityTests(unittest.TestCase):
    """Каждый встроенный навык должен быть валидным и иметь свои фразы."""

    def test_every_builtin_skill_loads(self):
        registry = SkillRegistry(PermissionPolicy())
        registry.scan()
        builtin = [skill for skill in registry.all() if skill.builtin]
        self.assertGreaterEqual(len(builtin), 8)
        for skill in builtin:
            with self.subTest(skill=skill.id):
                self.assertTrue(skill.actions, f"{skill.id}: нет действий")
                for action in skill.actions:
                    self.assertTrue(action.phrases, f"{skill.id}.{action.id}: нет фраз")

    def test_no_duplicate_phrases_inside_builtin(self):
        registry = SkillRegistry(PermissionPolicy())
        registry.scan()
        from jarvis.core.matcher import normalize_text

        owners: dict[str, str] = {}
        for skill in registry.all():
            if not skill.builtin:
                continue
            for action in skill.actions:
                for phrase in action.phrases:
                    key = normalize_text(phrase)
                    self.assertNotIn(key, owners,
                                     f"фраза «{phrase}» есть и у {owners.get(key)}, и у {skill.id}.{action.id}")
                    owners[key] = f"{skill.id}.{action.id}"


if __name__ == "__main__":
    unittest.main()
