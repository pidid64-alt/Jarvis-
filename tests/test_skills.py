"""Тесты навыков: каждый встроенный навык объявляет действия и умеет их выполнять."""

from __future__ import annotations

import importlib.util
import json
import unittest
from unittest import mock

from tests.helpers import FakeProviders, isolated_home, make_assistant

from jarvis.core import paths
from jarvis.core.permissions import PermissionPolicy
from jarvis.core.registry import SkillRegistry
from jarvis.platform.base import CommandResult


class FakePlatform:
    """Платформа-заглушка: команд не запускаем, но отвечаем как настоящая."""

    name = "linux"

    def __init__(self):
        self.commands: list[str] = []
        self.urls: list[str] = []

    def run(self, command, *, timeout=15.0, cwd=None):
        self.commands.append(command)
        if "uname -r" in command:
            return CommandResult(0, "6.1.158+")
        if "xclip" in command or "wl-paste" in command or "Get-Clipboard" in command:
            return CommandResult(0, "скопированный текст")
        return CommandResult(0, "готово")

    def spawn(self, command, *, cwd=None):
        self.commands.append(command)

    def notify(self, title, body, urgency="normal"):
        return True

    def play_wav(self, path):
        return True

    def open_url(self, url):
        self.urls.append(url)
        return True

    def beep(self):
        pass

    def setup_hotkey(self, spec, command):
        return True, "ок"

    def hotkey_hint(self, spec, command):
        return ""

    def tray_supported(self):
        return True


class SkillTests(unittest.TestCase):
    def setUp(self):
        self._ctx = isolated_home()
        self.home = self._ctx.__enter__()
        self.platform = FakePlatform()
        self.patcher = mock.patch("jarvis.platform.get_platform", return_value=self.platform)
        self.patcher.start()
        self.providers = FakeProviders()
        self.providers.llm.enabled = False
        self.assistant = make_assistant(providers=self.providers)

    def tearDown(self):
        self.patcher.stop()
        self._ctx.__exit__(None, None, None)

    def say(self, text: str):
        return self.assistant.handle_text(text, confirm_callback=lambda question: True)

    # -------------------------------------------------------------- структура
    def test_every_bundled_skill_has_manifest_and_handler_file(self):
        for manifest in sorted(paths.bundled_skills_dir().glob("*/skill.json")):
            with self.subTest(skill=manifest.parent.name):
                data = json.loads(manifest.read_text(encoding="utf-8"))
                self.assertTrue(data.get("id"))
                self.assertTrue(data.get("name"))
                self.assertTrue(data.get("description"))
                self.assertTrue(data.get("examples"))
                entry = data.get("entry")
                if entry:
                    self.assertTrue((manifest.parent / entry).exists(),
                                    f"{data['id']}: нет файла {entry}")

    def test_every_action_has_handler_function(self):
        registry = SkillRegistry(PermissionPolicy())
        registry.scan()
        for skill in registry.all():
            if not skill.builtin:
                continue
            with self.subTest(skill=skill.id):
                module = registry.handler(skill) if skill.entry else None
                for action in skill.actions:
                    if action.command:
                        continue
                    self.assertIsNotNone(module, f"{skill.id}: нет модуля-обработчика")
                    self.assertTrue(callable(getattr(module, "HANDLERS", {}).get(action.id)),
                                    f"{skill.id}.{action.id}: нет функции в HANDLERS")

    def test_all_builtin_actions_run_without_internal_errors(self):
        for skill in self.assistant.registry.all():
            if not skill.builtin:
                continue
            for action in skill.actions:
                if action.command or (action.platforms and "linux" not in action.platforms):
                    continue
                with self.subTest(skill=f"{skill.id}.{action.id}"):
                    reply = self.say(action.phrases[0])
                    self.assertNotEqual(reply.error, "not_understood",
                                        f"{skill.id}.{action.id}: фраза не распознана")
                    self.assertFalse(str(reply.error).startswith(("AttributeError", "TypeError", "KeyError")),
                                     f"{skill.id}.{action.id}: {reply.error}")

    # ------------------------------------------------------------- по навыкам
    def test_dialogue_hello_and_capabilities(self):
        self.assertIn("Здравствуйте", self.say("привет").text)
        self.assertIn("Умею", self.say("что ты умеешь").text)

    def test_time_skill_phrases(self):
        for phrase in ("сколько времени", "который час", "какое сейчас время"):
            with self.subTest(phrase=phrase):
                reply = self.say(phrase)
                self.assertEqual(reply.skill, "time_date")
                self.assertRegex(reply.text, r"\d{1,2}:\d{2}")

    def test_date_skill(self):
        reply = self.say("какое сегодня число")
        self.assertEqual(reply.skill, "time_date")
        self.assertIn("года", reply.text)

    def test_system_health_reads_without_shell(self):
        reply = self.say("сколько свободной памяти")
        self.assertEqual(reply.skill, "system_health")
        self.assertTrue(reply.text)
        self.assertEqual(self.platform.commands, [])  # здоровье системы читаем сами

    def test_volume_up_uses_platform_command(self):
        with mock.patch("jarvis.skills.has_program", side_effect=lambda *names: names[0] if names[0] == "pactl" else None):
            reply = self.say("громче")
        self.assertTrue(reply.ok)
        self.assertTrue(any("pactl" in command for command in self.platform.commands))

    def test_volume_without_tools_answers_politely(self):
        with mock.patch("jarvis.skills.has_program", return_value=None):
            reply = self.say("тише")
        self.assertFalse(reply.ok)

    def test_clipboard_reads_and_reports(self):
        with mock.patch("shutil.which", side_effect=lambda name: "/usr/bin/xclip" if name == "xclip" else None):
            reply = self.say("что в буфере обмена")
        self.assertTrue(reply.ok)
        self.assertIn("скопированный текст", reply.text)

    def test_clipboard_empty(self):
        self.platform.run = lambda command, **kwargs: CommandResult(0, "")
        with mock.patch("shutil.which", side_effect=lambda name: "/usr/bin/xclip" if name == "xclip" else None):
            reply = self.say("что в буфере обмена")
        self.assertFalse(reply.ok)
        self.assertIn("пуст", reply.text.lower())

    def test_power_requires_confirmation_and_runs(self):
        asked: list[str] = []
        reply = self.assistant.handle_text("выключи компьютер",
                                           confirm_callback=lambda question: asked.append(question) or True)
        self.assertTrue(reply.ok)
        self.assertTrue(asked)
        self.assertTrue(any("poweroff" in command for command in self.platform.commands))

    def test_power_cancelled_does_nothing(self):
        reply = self.assistant.handle_text("перезагрузи компьютер", confirm_callback=lambda question: False)
        self.assertEqual(reply.error, "cancelled")
        self.assertEqual(self.platform.commands, [])

    def test_media_uses_playerctl(self):
        with mock.patch("jarvis.skills.has_program", side_effect=lambda *names: "/usr/bin/playerctl" if names[0] == "playerctl" else None):
            reply = self.say("следующий трек")
        self.assertTrue(reply.ok)
        self.assertTrue(any("playerctl next" in command for command in self.platform.commands))

    def test_apps_open_browser_without_installed_browser(self):
        with mock.patch("shutil.which", return_value=None):
            reply = self.say("открой браузер")
        self.assertFalse(reply.ok)
        self.assertIn("не установлена", reply.text)

    def test_apps_open_files_with_thunar(self):
        with mock.patch("shutil.which", side_effect=lambda name: "/usr/bin/thunar" if name == "thunar" else None):
            reply = self.say("открой файлы")
        self.assertTrue(reply.ok)
        self.assertTrue(any("thunar" in command for command in self.platform.commands))

    def test_screenshot_reports_saved_file(self):
        def fake_which(name):
            return "/usr/bin/scrot" if name == "scrot" else None

        def fake_run(command, *, timeout=15.0, cwd=None, background=False, allow_failure=False):
            self.platform.commands.append(command)
            target = command.split()[-1]
            from pathlib import Path
            Path(target).write_bytes(b"\x89PNG")
            return CommandResult(0, "")

        self.platform.run = fake_run
        with mock.patch("shutil.which", side_effect=fake_which):
            reply = self.say("сделай скриншот")
        self.assertTrue(reply.ok)
        self.assertIn("Снимок сохранён", reply.text)

    def test_web_search_uses_provider_and_model(self):
        providers = FakeProviders()
        providers.llm.answer = "Тихоходки — микроскопические животные."
        providers.llm.chat_text = lambda messages, **kwargs: "Тихоходки — микроскопические животные."
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_text("найди тихоходок", confirm_callback=lambda question: True)
        self.assertTrue(reply.ok)
        self.assertEqual(providers.search.queries, ["тихоходок"])
        self.assertIn("Тихоходки", reply.text)

    def test_web_search_without_network_explains(self):
        providers = FakeProviders()
        def boom(query):
            raise RuntimeError("нет сети")
        providers.search.search = boom
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_text("найди что-нибудь", confirm_callback=lambda question: True)
        self.assertFalse(reply.ok)
        self.assertNotIn("RuntimeError", reply.text)

    def test_open_site_opens_browser(self):
        reply = self.say("открой сайт example.com")
        self.assertTrue(reply.ok)
        self.assertEqual(self.platform.urls, ["https://example.com"])

    def test_open_site_without_dot_uses_search(self):
        reply = self.say("открой сайт погода")
        self.assertTrue(reply.ok)
        self.assertTrue(self.platform.urls[0].startswith("https://duckduckgo.com/"))


if __name__ == "__main__":
    unittest.main()
