"""Тесты настроек, правки TOML и секретов."""

from __future__ import annotations

import os
import stat
import sys
import unittest
from pathlib import Path

from tests.helpers import isolated_home  # noqa: F401

from jarvis.core import paths, secrets, toml_edit
from jarvis.core.config import Config, referenced_env_names, resolve_refs


class TomlEditTests(unittest.TestCase):
    SAMPLE = """# Заголовок файла, который нельзя терять
[llm]
enabled = true
model = "gpt"   # комментарий про модель
api_key = "${JARVIS_LLM_KEY}"

[voice]
enabled = true
"""

    def test_value_replaced_and_comment_kept(self):
        updated = toml_edit.set_value(self.SAMPLE, ("llm", "model"), "other")
        self.assertIn('model = "other"', updated)
        self.assertIn("# комментарий про модель", updated)
        self.assertIn("# Заголовок файла, который нельзя терять", updated)

    def test_new_key_inserted_into_section(self):
        updated = toml_edit.set_value(self.SAMPLE, ("llm", "timeout"), 12)
        self.assertIn("timeout = 12", updated)
        # ключ попал именно в секцию llm, до заголовка voice
        self.assertLess(updated.index("timeout = 12"), updated.index("[voice]"))

    def test_missing_section_created(self):
        updated = toml_edit.set_value(self.SAMPLE, ("search", "max_results"), 5)
        self.assertIn("[search]", updated)
        self.assertIn("max_results = 5", updated)

    def test_array_of_strings(self):
        updated = toml_edit.set_value(self.SAMPLE, ("skills", "disabled"), ["power", "volume"])
        self.assertIn('disabled = ["power", "volume"]', updated)

    def test_result_is_valid_toml(self):
        updated = toml_edit.set_value(self.SAMPLE, ("llm", "model"), "с \"кавычками\"")
        parsed = toml_edit.parse(updated)
        self.assertEqual(parsed["llm"]["model"], 'с "кавычками"')

    def test_unsupported_type_rejected(self):
        with self.assertRaises(toml_edit.TomlEditError):
            toml_edit.format_value({"a": 1})


class ConfigTests(unittest.TestCase):
    def test_defaults_are_loaded_without_user_file(self):
        with isolated_home():
            config = Config.load()
            self.assertEqual(config.get("assistant.language"), "ru")
            self.assertEqual(config.get("permissions.mode"), "restricted")
            self.assertTrue(paths.config_path().exists())

    def test_user_file_overrides_defaults(self):
        with isolated_home():
            Config.load()
            paths.config_path().write_text('[assistant]\nlanguage = "en"\n', encoding="utf-8")
            config = Config.load()
            self.assertEqual(config.get("assistant.language"), "en")
            self.assertEqual(config.get("permissions.mode"), "restricted")  # остальное из шаблона

    def test_env_reference_resolved(self):
        with isolated_home():
            os.environ["JARVIS_TEST_KEY"] = "secret-value"
            Config.load()
            path = paths.config_path()
            text = path.read_text(encoding="utf-8").replace("${JARVIS_LLM_KEY}", "${JARVIS_TEST_KEY}")
            path.write_text(text, encoding="utf-8")
            config = Config.load()
            self.assertEqual(config.get("llm.api_key"), "secret-value")
            self.assertEqual(config.missing_env, [])
            del os.environ["JARVIS_TEST_KEY"]

    def test_missing_secret_reported_by_name_not_value(self):
        os.environ.pop("JARVIS_LLM_KEY", None)
        with isolated_home():
            config = Config.load()
            self.assertIn("JARVIS_LLM_KEY", config.missing_env)
            self.assertTrue(config.is_empty_secret("llm.api_key"))
            self.assertEqual(config.get("llm.api_key"), "")

    def test_path_placeholders_are_resolved(self):
        """{state}/{legacy}/... раскрываются сразу, чтобы никто не получил «{state}» строкой."""
        with isolated_home():
            config = Config.load()
            token_file = config.get("api.token_file")
            self.assertNotIn("{", token_file)
            self.assertEqual(Path(token_file), paths.state_dir() / "api.token")
            self.assertEqual(config.get("tts.data_dir"), str(paths.legacy_dir() / "models"))
            self.assertTrue(config.get("stt.model").startswith(str(paths.legacy_dir())))
            # подстановка {python} — это тот же интерпретатор, что запустил программу
            self.assertIn(sys.executable, config.get("tts.server_command"))

    def test_path_substitution_helper(self):
        with isolated_home():
            self.assertEqual(paths.substitute("{state}/x"), str(paths.state_dir() / "x"))
            self.assertEqual(paths.substitute("{legacy}/y"), str(paths.legacy_dir() / "y"))
            self.assertEqual(paths.substitute("{base}"), str(paths.PROJECT_ROOT))
            self.assertEqual(paths.substitute(""), "")
            self.assertEqual(paths.substitute("без подстановок"), "без подстановок")

    def test_reference_helpers(self):
        value, missing = resolve_refs({"a": "${NOPE}", "b": "${NOPE:default}"})
        self.assertEqual(value, {"a": "", "b": "default"})
        self.assertEqual(missing, ["NOPE"])
        self.assertEqual(referenced_env_names({"x": "${A}", "y": ["${B}"]}), ["A", "B"])


class SecretTests(unittest.TestCase):
    def test_parse_env_handles_quotes_and_comments(self):
        parsed = secrets.parse_env('# key\nA=1\nexport B="two"\nC=\'three\'\n')
        self.assertEqual(parsed, {"A": "1", "B": "two", "C": "three"})

    def test_save_creates_file_with_600(self):
        with isolated_home():
            path = secrets.save_env_var("JARVIS_LLM_KEY", "sk-test-1234567890")
            self.assertTrue(path.exists())
            if os.name != "nt":
                mode = stat.S_IMODE(path.stat().st_mode)
                self.assertEqual(mode, 0o600)
            self.assertTrue(secrets.env_file_is_private(path))

    def test_save_keeps_other_lines(self):
        with isolated_home():
            path = paths.env_file_path()
            path.write_text("# комментарий\nOTHER=1\n", encoding="utf-8")
            secrets.save_env_var("JARVIS_LLM_KEY", "sk-abcdef123456", path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("# комментарий", text)
            self.assertIn("OTHER=1", text)
            self.assertIn("JARVIS_LLM_KEY=sk-abcdef123456", text)

    def test_mask_hides_value(self):
        secrets.reset_registry()
        secrets.register("sk-or-v1-abcdef123456")
        masked = secrets.mask_text("ключ: sk-or-v1-abcdef123456 и всё")
        self.assertNotIn("abcdef123456", masked)
        self.assertIn("sk-o", masked)

    def test_mask_unknown_token_shapes(self):
        secrets.reset_registry()
        masked = secrets.mask_text('Authorization: Bearer abcdefghijklmnop123456')
        self.assertNotIn("abcdefghijklmnop123456", masked)

    def test_load_env_file_applies_and_registers(self):
        with isolated_home():
            path = paths.env_file_path()
            path.write_text("JARVIS_LLM_KEY=sk-abcdef123456\n", encoding="utf-8")
            os.environ.pop("JARVIS_LLM_KEY", None)
            secrets.load_env_file(path)
            self.assertEqual(os.environ.get("JARVIS_LLM_KEY"), "sk-abcdef123456")
            self.assertNotIn("sk-abcdef123456", secrets.mask_text("sk-abcdef123456"))
            os.environ.pop("JARVIS_LLM_KEY", None)


class JournalTests(unittest.TestCase):
    def test_journal_masks_secrets(self):
        with isolated_home():
            from jarvis.core.journal import Journal

            secrets.reset_registry()
            secrets.register("sk-or-v1-abcdef123456")
            journal = Journal()
            journal.info("test", "ключ sk-or-v1-abcdef123456 в логе")
            records = journal.read()
            self.assertEqual(len(records), 1)
            self.assertNotIn("abcdef123456", records[0]["message"])
            report = journal.report()
            self.assertNotIn("abcdef123456", report)

    def test_journal_levels_filter(self):
        with isolated_home():
            from jarvis.core.journal import Journal

            journal = Journal()
            journal.info("a", "обычное")
            journal.error("b", "ошибка")
            self.assertEqual(len(journal.read(level="error")), 1)


if __name__ == "__main__":
    unittest.main()
