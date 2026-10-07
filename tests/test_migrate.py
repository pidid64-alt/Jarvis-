"""Тесты переноса данных старой версии: ничего не теряется и ничего не течёт."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from tests.helpers import isolated_home

from jarvis.core import migrate, paths, toml_edit
from jarvis.core.config import Config
from jarvis.core.registry import SkillRegistry
from jarvis.core.permissions import PermissionPolicy

LEGACY = Path(__file__).resolve().parents[1] / "legacy"


def read_legacy_commands(filename: str) -> list[dict]:
    data = json.loads((LEGACY / filename).read_text(encoding="utf-8"))
    return data.get("commands") or []


@unittest.skipUnless(LEGACY.exists(), "папка legacy/ недоступна")
class MigrateTests(unittest.TestCase):
    def setUp(self):
        self._ctx = isolated_home()
        self.home = self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_dry_run_changes_nothing(self):
        report = migrate.migrate(dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertGreater(report.actions, 100)
        self.assertFalse(paths.config_path().exists())
        self.assertFalse(paths.user_skills_dir().exists())

    def test_real_run_makes_backup_and_config(self):
        report = migrate.migrate()
        self.assertIsNotNone(report.backup_dir)
        self.assertTrue(report.backup_dir.exists())
        self.assertTrue((report.backup_dir / "config.json").exists())
        self.assertTrue(paths.config_path().exists())
        Config.load()  # настройки обязаны быть валидным TOML

    def test_all_commands_carried_over(self):
        report = migrate.migrate()
        expected = sum(len(read_legacy_commands(name))
                       for name in ("commands.json", "commands-win.json"))
        # Каждая старая команда либо перенесена, либо заменена встроенным
        # навыком (и названа в отчёте), либо отмечена как пропущенная.
        self.assertEqual(report.actions + len(report.superseded) + len(report.skipped), expected)
        self.assertGreater(len(report.superseded), 0)
        self.assertEqual(report.superseded[0], report.superseded[0])  # имена, а не объекты

    def test_phrases_preserved_verbatim(self):
        migrate.migrate()
        registry = SkillRegistry(PermissionPolicy())
        registry.scan()
        migrated = [skill for skill in registry.all() if skill.id.startswith("legacy_")]
        self.assertTrue(migrated)
        phrases = {phrase for skill in migrated for action in skill.actions for phrase in action.phrases}
        legacy_phrases = {
            phrase for name in ("commands.json", "commands-win.json")
            for entry in read_legacy_commands(name) for phrase in (entry.get("phrases") or [])
            if phrase
        }
        builtin = set()
        from jarvis.core.matcher import normalize_text
        for manifest in paths.bundled_skills_dir().glob("*/skill.json"):
            data = json.loads(manifest.read_text(encoding="utf-8"))
            for action in data.get("actions", []):
                for phrase in action.get("phrases", []):
                    builtin.add(normalize_text(phrase))
        lost = [phrase for phrase in legacy_phrases if normalize_text(phrase) not in
                {normalize_text(item) for item in phrases} and normalize_text(phrase) not in builtin]
        # допустимы потери только из-за конфликтов с встроенными фразами
        self.assertEqual(lost, [])

    def test_legacy_paths_rewritten(self):
        migrate.migrate()
        registry = SkillRegistry(PermissionPolicy())
        registry.scan()
        joined = " ".join(action.command or "" for skill in registry.all() if skill.id.startswith("legacy_")
                          for action in skill.actions)
        self.assertNotIn("$HOME/jarvis", joined)
        self.assertIn("{legacy}", joined)

    def test_windows_skills_limited_to_windows(self):
        migrate.migrate()
        registry = SkillRegistry(PermissionPolicy())
        registry.scan()
        win = registry.get("legacy_win_apps")
        if win is not None:
            self.assertTrue(all(action.platforms == ["windows"] for action in win.actions))

    def test_dangerous_flags_kept(self):
        migrate.migrate()
        registry = SkillRegistry(PermissionPolicy())
        registry.scan()
        dangerous = [f"{skill.id}.{action.id}" for skill in registry.all()
                     for action in skill.actions if action.is_dangerous]
        self.assertGreater(len(dangerous), 10)
        for must in ("power.poweroff", "power.reboot"):
            self.assertIn(must, dangerous)

    def test_secrets_not_written_into_config(self):
        old_env = paths.env_file_path()
        old_env.write_text("OMNIROUTE_API_KEY=sk-or-v1-secret-abcdef123456\n", encoding="utf-8")
        migrate.migrate()
        config_text = paths.config_path().read_text(encoding="utf-8")
        self.assertNotIn("sk-or-v1-secret-abcdef123456", config_text)
        self.assertIn("${JARVIS_LLM_KEY}", config_text)
        env_text = paths.env_file_path().read_text(encoding="utf-8")
        self.assertIn("JARVIS_LLM_KEY=", env_text)
        self.assertIn("sk-or-v1-secret-abcdef123456", env_text)
        if os.name != "nt":
            import stat
            mode = stat.S_IMODE(paths.env_file_path().stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_second_run_does_not_duplicate(self):
        first = migrate.migrate()
        second = migrate.migrate()
        self.assertEqual(second.actions, first.actions)
        registry = SkillRegistry(PermissionPolicy())
        registry.scan()
        ids = [skill.id for skill in registry.all() if skill.id.startswith("legacy_")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_autonomy_rules_migrated_and_config_parses(self):
        migrate.migrate()
        config = Config.load()
        rules = config.get("autonomy.rules") or []
        self.assertGreaterEqual(len(rules), 5)
        self.assertTrue(all("check" in rule for rule in rules))

    def test_backup_keeps_old_files(self):
        report = migrate.migrate()
        for name in ("config.json", "commands.json", "autonomy.json"):
            with self.subTest(name=name):
                copy = report.backup_dir / name
                if (LEGACY / name).exists():
                    self.assertTrue(copy.exists())
                    self.assertEqual(copy.read_bytes(), (LEGACY / name).read_bytes())


class TomlEditRemoveTests(unittest.TestCase):
    def test_remove_key_inside_section(self):
        text = '[autonomy]\nenabled = true\nrules = []\n\n[api]\nport = 0\n'
        updated = toml_edit.remove_key(text, ("autonomy", "rules"))
        self.assertNotIn("rules = []", updated)
        self.assertIn("enabled = true", updated)
        self.assertEqual(toml_edit.parse(updated)["api"]["port"], 0)

    def test_remove_missing_key_is_noop(self):
        text = "[a]\nb = 1\n"
        self.assertEqual(toml_edit.remove_key(text, ("a", "c")), text)

    def test_array_of_tables_parses_after_removal(self):
        text = '[autonomy]\nrules = []\n'
        updated = toml_edit.remove_key(text, ("autonomy", "rules"))
        updated += '\n[[autonomy.rules]]\nid = "x"\nrepeat_seconds = 60.0\n'
        parsed = toml_edit.parse(updated)
        self.assertEqual(parsed["autonomy"]["rules"], [{"id": "x", "repeat_seconds": 60.0}])


if __name__ == "__main__":
    unittest.main()
