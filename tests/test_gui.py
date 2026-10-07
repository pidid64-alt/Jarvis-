"""Тесты окна: логика без Tkinter, тексты и склейка с ядром.

Само окно здесь не рисуется (в тестовой среде Tkinter может отсутствовать),
зато проверяется всё, что можно проверить без экрана: тексты интерфейса, цвета,
подписи навыков, поиск работающего ядра и запасной запуск своего.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.helpers import FakeProviders, isolated_home, make_assistant

from jarvis.core import paths
from jarvis.interfaces import gui
from jarvis.interfaces.api import LocalApi

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TextHelpersTests(unittest.TestCase):
    def test_theme_palette(self):
        dark = gui.theme_palette("dark")
        light = gui.theme_palette("light")
        self.assertEqual(gui.theme_palette("что-то иное"), dark)  # неизвестная тема — тёмная
        self.assertNotEqual(dark["bg"], light["bg"])
        for palette in (dark, light):
            for key in ("bg", "panel", "text", "accent", "entry"):
                self.assertIn(key, palette)

    def test_state_color_falls_back_to_idle(self):
        self.assertEqual(gui.state_color("idle"), gui.STATE_COLORS["idle"])
        self.assertEqual(gui.state_color("непонятное"), gui.STATE_COLORS["idle"])
        self.assertEqual(gui.state_color("listening"), gui.STATE_COLORS["listening"])

    def test_trim(self):
        self.assertEqual(gui.trim("  много   пробелов\nи строк  "), "много пробелов и строк")
        self.assertTrue(gui.trim("я" * 300, 50).endswith("…"))
        self.assertLessEqual(len(gui.trim("я" * 300, 50)), 50)

    def test_provider_summary(self):
        from jarvis.core.i18n import get_translator

        t = get_translator("ru")
        status = {"providers": {"llm": {"available": False}, "stt": {"available": True},
                                "tts": {"available": True}}}
        text = gui.provider_summary(status, t)
        self.assertIn(t("gui.provider.llm"), text)
        self.assertIn(t("gui.state.not_ready"), text)
        self.assertIn(t("gui.state.ready"), text)

    def test_permissions_and_actions_text(self):
        skill = {
            "id": "demo", "name": "Демо", "builtin": True,
            "permissions": {"shell": True, "dangerous": True, "notify": False},
            "actions": [{"id": "x", "description": "Сделать", "phrases": ["сделай"], "confirm": True}],
        }
        inline = gui.permissions_inline(skill, "ru")
        self.assertIn("команд", inline)
        self.assertIn("менять систему", inline)
        details = gui.actions_text(skill)
        self.assertIn("Сделать", details)
        self.assertIn("подтверждение", details)

    def test_permissions_inline_without_rights(self):
        skill = {"permissions": {"notify": False}, "actions": []}
        self.assertEqual(gui.permissions_inline(skill), "—")

    def test_reply_text_marks_errors(self):
        self.assertEqual(gui.reply_text(None), "")
        self.assertEqual(gui.reply_text({"text": "Готово", "ok": True}), "Готово")
        failed = gui.reply_text({"text": "Не вышло", "ok": False, "error": "command_failed"})
        self.assertIn("Не вышло", failed)
        self.assertIn("command_failed", failed)


class TranslationCoverageTests(unittest.TestCase):
    """Все подписи окна обязаны существовать в файле переводов."""

    def test_every_gui_key_exists(self):
        source = (PROJECT_ROOT / "jarvis" / "interfaces" / "gui.py").read_text(encoding="utf-8")
        keys = set(re.findall(r'"(gui\.[a-z0-9_.]+)"', source))
        self.assertGreater(len(keys), 40)
        ru = json.loads((PROJECT_ROOT / "jarvis" / "config" / "i18n" / "ru.json").read_text(encoding="utf-8"))
        missing = sorted(key for key in keys if key not in ru)
        self.assertEqual(missing, [], f"нет переводов для: {missing}")

    def test_state_labels_exist_for_all_states(self):
        from jarvis.core.types import State

        ru = json.loads((PROJECT_ROOT / "jarvis" / "config" / "i18n" / "ru.json").read_text(encoding="utf-8"))
        for state in State:
            self.assertIn(f"gui.state.{state.value}", ru)

    def test_translator_returns_key_for_unknown(self):
        from jarvis.core.i18n import get_translator

        self.assertTrue(get_translator("ru").t("gui.state.idle"))
        self.assertEqual(get_translator("ru").t("gui.нет.такого"), "gui.нет.такого")


class EnvironmentTests(unittest.TestCase):
    def test_tkinter_check_explains_what_to_install(self):
        ready, reason = gui.tkinter_available()
        if ready:
            self.assertEqual(reason, "")
        else:
            self.assertIn("tkinter", reason)
            self.assertTrue("pacman" in reason or "apt" in reason or "tcl/tk" in reason)

    def test_gui_module_imports_without_tkinter(self):
        # окно не должно требовать Tkinter для импорта: иначе падают CLI и тесты
        import importlib

        module = importlib.import_module("jarvis.interfaces.gui")
        self.assertTrue(hasattr(module, "GuiApp"))
        self.assertTrue(hasattr(module, "main"))

    def test_tray_reports_reason_without_crashing(self):
        from jarvis.interfaces.tray import tray_available

        ready, reason = tray_available()
        if not ready:
            self.assertTrue(reason)


class ConnectTests(unittest.TestCase):
    """Окно должно уметь и подключаться к демону, и работать без него."""

    def setUp(self):
        self._ctx = isolated_home()
        self.home = self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_connect_or_start_finds_running_core(self):
        providers = FakeProviders()
        providers.llm.enabled = False
        assistant = make_assistant(providers=providers)
        api = LocalApi(assistant, port=0)
        api.start()
        try:
            client, embedded = gui.connect_or_start()
            self.assertIsNotNone(client)
            self.assertIsNone(embedded, "работающее ядро подключается, а не дублируется")
            self.assertEqual(client.status()["skills"]["total"], assistant.status()["skills"]["total"])
        finally:
            api.stop()

    def test_connect_or_start_raises_own_core_when_alone(self):
        client, embedded = gui.connect_or_start()
        try:
            self.assertIsNotNone(embedded)
            assistant, api = embedded
            self.assertTrue(client.url.endswith(str(api.port)))
            self.assertGreater(client.status()["skills"]["total"], 5)
            # окно обязано убрать за собой временные файлы
            self.assertTrue(paths.api_file().exists())
        finally:
            assistant.shutdown()
            api.stop()
        self.assertFalse(paths.api_file().exists())

    def test_discovery_file_is_used_only_when_core_is_alive(self):
        providers = FakeProviders()
        providers.llm.enabled = False
        assistant = make_assistant(providers=providers)
        api = LocalApi(assistant, port=0)
        api.start()
        token = api.token_path.read_text(encoding="utf-8")
        api.stop()
        # файл обнаружения остался от прошлого запуска — доверия ему нет
        paths.api_file().write_text(json.dumps({"url": api.url, "port": api.port}), encoding="utf-8")
        api.token_path.write_text(token, encoding="utf-8")
        from jarvis.interfaces.client import ApiClient

        self.assertIsNone(ApiClient.discover())


if __name__ == "__main__":
    unittest.main()
