"""Сквозная проверка окна без экрана: кнопки, вкладки, настройки, секреты.

Окно строится на подставном Tkinter (``tests.fake_tk``), ядро — настоящее, как в
``test_api``: локальный API в этом же процессе. Так проверяется вся цепочка
«нажали кнопку → ушёл запрос → ядро ответило → окно показало».
"""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from tests.helpers import FakeProviders, isolated_home, make_assistant

from jarvis.core import paths
from jarvis.interfaces.api import LocalApi
from jarvis.interfaces.client import ApiClient, ApiTarget
from jarvis.platform.base import CommandResult

SECRET_VALUE = "sk-or-v1-gui-secret-0987654321"


class FakePlatform:
    name = "linux"

    def __init__(self):
        self.commands: list[str] = []
        self.urls: list[str] = []

    def run(self, command, *, timeout=15.0, cwd=None):
        self.commands.append(command)
        return CommandResult(0, "готово")

    def spawn(self, command, *, cwd=None):
        self.commands.append(command)

        class Process:
            def wait(self, timeout=None):
                return 0

        return Process()

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
        self.hotkey = (spec, command)
        return True, "ок"

    def hotkey_hint(self, spec, command):
        return ""

    def tray_supported(self):
        return True


def pump(app, seconds: float = 1.0, step: float = 0.02) -> None:
    """Крутит цикл окна вручную, пока фоновые запросы не завершатся."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.root.update()
        time.sleep(step)


def pump_until(app, condition=lambda: True, *, timeout: float = 8.0) -> bool:
    """Ждёт события, а не фиксированное время: меньше случайных падений.

    Фоновые запросы идут в потоках, и на загруженной машине двух секунд может
    не хватить — поэтому проверяем условие в цикле, но не дольше ``timeout``.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.root.update()
        if condition():
            return True
        time.sleep(0.02)
    return bool(condition())


class GuiSmokeTests(unittest.TestCase):
    def setUp(self):
        self._ctx = isolated_home()
        self.home = self._ctx.__enter__()
        self.platform = FakePlatform()
        self.patcher = mock.patch("jarvis.platform.get_platform", return_value=self.platform)
        self.patcher.start()

        self.providers = FakeProviders(voice_enabled=True)
        self.providers.llm.enabled = False
        self.providers.stt.text = "сколько времени"
        self.assistant = make_assistant(providers=self.providers)
        self.api = LocalApi(self.assistant, port=0)
        self.api.start()
        self.client = ApiClient(ApiTarget(url=self.api.url, token=self.api.token))

    def tearDown(self):
        app = getattr(self, "_app", None)
        if app is not None:  # останавливаем опросы окна, иначе они живут после теста
            app._closing = True
            try:
                app.root.destroy()
            except Exception:  # noqa: BLE001
                pass
        self.api.stop()
        self.patcher.stop()
        self._ctx.__exit__(None, None, None)

    # ---------------------------------------------------------------- сборка
    def build(self, *, tray: str = "none", yes: bool = False):
        """Собирает окно. ``tray``: none — не просили, unavailable — нет библиотеки,
        present — значок есть (подставной)."""
        from tests.fake_tk import fake_tkinter

        module = fake_tkinter(yes=yes)
        module.__enter__()
        self.addCleanup(lambda: module.__exit__(None, None, None))

        from jarvis.interfaces.gui import GuiApp

        ready = tray == "present"
        reason = "" if ready else "нет библиотеки pystray"
        icon = mock.Mock()
        with mock.patch("jarvis.interfaces.tray.tray_available", return_value=(ready, reason)), \
                mock.patch("jarvis.interfaces.tray.create_tray", return_value=icon):
            app = GuiApp(self.client, tray=tray != "none", start_minimized=False, embedded=None)
        self._app = app
        pump(app, 0.3)
        return app

    # ------------------------------------------------------------------- чат
    def test_window_opens_with_widgets_and_state(self):
        app = self.build()
        self.assertEqual(app.root.title_text, "Jarvis")
        self.assertIn("880x640", app.root.geometry_spec)
        self.assertTrue(app.chat.packed and app.entry.packed and app.mic_button.packed)
        self.assertIn(app.t("gui.state.idle"), app.state_label.text)
        self.assertIn(app.t("gui.chat.greeting"), app.chat.get())

    def test_text_command_travels_to_core_and_back(self):
        app = self.build()
        app.entry.insert(0, "сколько времени")
        app.send_button.invoke()
        pump_until(app, lambda: "Сейчас" in app.chat.get())
        text = app.chat.get()
        self.assertIn("сколько времени", text)      # реплика пользователя
        self.assertIn("Сейчас", text)                # ответ ядра
        self.assertEqual(app.state_label.text, app.t("gui.state.idle"))

    def test_empty_message_is_ignored(self):
        app = self.build()
        app.entry.insert(0, "   ")
        app.send_button.invoke()
        pump(app, 0.3)
        self.assertNotIn(app.t("gui.role.jarvis"), app.chat.get())

    def test_unknown_phrase_shows_polite_answer_and_error_state(self):
        app = self.build()
        app.entry.insert(0, "свари мне борщ из синей капусты")
        app.send_button.invoke()
        pump_until(app, lambda: "понял" in app.chat.get().lower())
        self.assertIn("понял", app.chat.get().lower())
        self.assertEqual(app.state_label.text, app.t("gui.state.error"))

    def test_microphone_button_uses_voice_flow(self):
        app = self.build()
        wav = paths.state_dir() / "gui-test.wav"
        self.providers.recorder.path = wav
        app.mic_button.invoke()
        pump_until(app, lambda: "Сейчас" in app.chat.get())
        self.assertTrue(wav.exists())
        self.assertIn("Сейчас", app.chat.get())

    def test_microphone_without_speech_explains(self):
        app = self.build()
        self.providers.recorder.path = None
        app.mic_button.invoke()
        pump_until(app, lambda: app.state_label.text == app.t("gui.state.error"))
        self.assertEqual(app.state_label.text, app.t("gui.state.error"))

    # --------------------------------------------------------- подтверждения
    def test_dangerous_action_declined_from_window(self):
        app = self.build(yes=False)
        app.entry.insert(0, "выключи компьютер")
        app.send_button.invoke()
        from tests.fake_tk import asked_questions
        pump_until(app, lambda: bool(asked_questions) and "Отменяю" in app.chat.get())
        self.assertTrue(asked_questions)
        self.assertIn("Выключить", asked_questions[-1])
        self.assertIn("Отменяю", app.chat.get())
        self.assertEqual(self.platform.commands, [])

    def test_dangerous_action_confirmed_from_window(self):
        app = self.build(yes=True)
        app.entry.insert(0, "заблокируй экран")
        app.send_button.invoke()
        pump_until(app, lambda: any("loginctl" in command or "xdg-screensaver" in command
                                    for command in self.platform.commands))
        self.assertTrue(any("loginctl" in command or "xdg-screensaver" in command
                            for command in self.platform.commands))

    # ---------------------------------------------------------------- навыки
    def test_skills_page_lists_and_describes(self):
        app = self.build()
        app.notebook.select(app.tab_skills)
        app._refresh_skills()
        pump_until(app, lambda: bool(app.skills_tree.get_children()))
        rows = app.skills_tree.get_children()
        self.assertGreater(len(rows), 5)
        self.assertIn("dialogue", rows)
        app.skills_tree.selection_set("dialogue")
        app._show_skill_details()
        details = app.skill_details.get()
        self.assertIn("Беседа", details)
        self.assertIn(app.t("gui.skills.rights"), details)

    def test_skill_toggle_from_window_persists(self):
        app = self.build()
        app.notebook.select(app.tab_skills)
        app._refresh_skills()
        pump_until(app, lambda: bool(app.skills_tree.get_children()))
        app.skills_tree.selection_set("volume")
        app._toggle_skill(False)
        pump_until(app, lambda: "volume" in paths.config_path().read_text(encoding="utf-8"))
        self.assertIn("volume", paths.config_path().read_text(encoding="utf-8"))
        self.assertFalse(any(item["id"] == "volume" for item in self.client.skills() if item["enabled"]))
        app.skills_tree.selection_set("volume")
        app._toggle_skill(True)
        pump_until(app, lambda: any(item["id"] == "volume" and item["enabled"]
                                    for item in self.client.skills()))
        self.assertTrue(any(item["id"] == "volume" for item in self.client.skills() if item["enabled"]))

    def test_skill_toggle_without_selection_hints(self):
        app = self.build()
        app._toggle_skill(False)
        self.assertEqual(app.skills_hint.text, app.t("gui.skills.pick"))

    # -------------------------------------------------------------- настройки
    def test_settings_saved_from_window(self):
        app = self.build()
        app.theme_var.set("light")
        app.settings_vars["hotkey.spec"].set("Ctrl+Alt+J")
        app._save_settings()
        pump_until(app, lambda: app.settings_status.text.startswith("Сохранено"))
        config_text = paths.config_path().read_text(encoding="utf-8")
        self.assertIn('theme = "light"', config_text)
        self.assertIn('spec = "Ctrl+Alt+J"', config_text)
        self.assertIn("2", app.settings_status.text)  # «Сохранено настроек: 2»
        self.assertEqual(self.assistant.config.get("assistant.theme"), "light")

    def test_secret_is_saved_to_env_only(self):
        app = self.build()
        app.secret_var.set(SECRET_VALUE)
        app._save_secret()
        pump_until(app, lambda: paths.env_file_path().exists())
        env_text = paths.env_file_path().read_text(encoding="utf-8")
        self.assertIn(SECRET_VALUE, env_text)
        self.assertNotIn(SECRET_VALUE, paths.config_path().read_text(encoding="utf-8"))
        self.assertNotIn(SECRET_VALUE, app.settings_status.text)   # в окне только маска
        self.assertNotIn(SECRET_VALUE, app.chat.get())
        self.assertIn("…", app.settings_status.text)

    def test_secret_requires_value(self):
        app = self.build()
        app.secret_var.set("   ")
        app._save_secret()
        self.assertEqual(app.settings_status.text, app.t("gui.settings.key_empty"))

    # ---------------------------------------------------------------- журнал
    def test_journal_page_and_report_hides_secrets(self):
        app = self.build()
        app.secret_var.set(SECRET_VALUE)
        app._save_secret()
        pump_until(app, lambda: paths.env_file_path().exists())
        app.entry.insert(0, "привет")
        app.send_button.invoke()
        pump_until(app, lambda: "привет" in app.chat.get())

        app._refresh_journal()
        pump_until(app, lambda: "привет" in app.log_text.get())
        self.assertIn("привет", app.log_text.get())

        app._copy_report()
        pump_until(app, lambda: "Jarvis" in app.root.clipboard)
        pump_until(app, lambda: app.log_hint.text == app.t("gui.log.copied"))
        self.assertNotIn(SECRET_VALUE, app.root.clipboard)
        self.assertIn("Jarvis", app.root.clipboard)
        self.assertEqual(app.log_hint.text, app.t("gui.log.copied"))

    def test_journal_level_filter_is_passed(self):
        app = self.build()
        app.log_level.set("error")
        with mock.patch.object(self.client, "journal", wraps=self.client.journal) as spy:
            app._refresh_journal()
            pump_until(app, lambda: spy.called)
        self.assertTrue(spy.called)
        self.assertEqual(spy.call_args.kwargs.get("level"), "error")

    # ----------------------------------------------------------------- вкладки
    def test_tab_changes_refresh_pages(self):
        app = self.build()
        app.notebook.select(app.tab_log)
        app.notebook.event_generate("<<NotebookTabChanged>>")
        pump_until(app, lambda: bool(app.log_text.get()))
        self.assertTrue(app.log_text.get())
        app.notebook.select(app.tab_skills)
        app.notebook.event_generate("<<NotebookTabChanged>>")
        pump_until(app, lambda: bool(app.skills_tree.get_children()))
        self.assertTrue(app.skills_tree.get_children())

    def test_about_page_buttons(self):
        app = self.build()
        app._open_path(paths.config_dir())
        self.assertTrue(self.platform.urls)
        app._copy("текст для копирования")
        self.assertEqual(app.root.clipboard, "текст для копирования")

    # ------------------------------------------------------------------ трей
    def test_tray_absence_is_explained_not_fatal(self):
        app = self.build(tray="unavailable")
        self.assertIsNone(app.tray)
        self.assertIn("Трей недоступен", app.hint_label.text)

    def test_no_tray_requested_keeps_quiet(self):
        app = self.build(tray="none")
        self.assertIsNone(app.tray)
        self.assertEqual(app.hint_label.text, app.t("gui.hint"))

    def test_close_without_tray_quits(self):
        app = self.build(tray="unavailable")
        app._on_close()
        self.assertTrue(app.root.destroyed)

    def test_close_with_tray_hides_window(self):
        app = self.build(tray="present")
        self.assertIsNotNone(app.tray)
        app._on_close()
        self.assertTrue(app.root.withdrawn)
        self.assertFalse(app.root.destroyed)
        app._quit()

    # ------------------------------------------------------------ клавиша/триггер
    def test_embedded_window_registers_hotkey_and_watches_trigger(self):
        from tests.fake_tk import fake_tkinter

        module = fake_tkinter()
        module.__enter__()
        self.addCleanup(lambda: module.__exit__(None, None, None))
        from jarvis.interfaces.gui import GuiApp

        with mock.patch("jarvis.interfaces.tray.tray_available", return_value=(False, "нет")):
            app = GuiApp(self.client, tray=False, embedded=(self.assistant, self.api))
        self._app = app
        pump(app, 0.2)
        self.assertEqual(self.platform.hotkey[0], "Super+J")
        self.assertIn("jarvis trigger", self.platform.hotkey[1])

        # нажатие горячей клавиши оставляет файл-триггер — окно должно отреагировать
        self.providers.recorder.path = paths.state_dir() / "trigger.wav"
        trigger = paths.state_dir() / "trigger"
        trigger.write_text("1", encoding="utf-8")
        pump_until(app, lambda: app._trigger_seen > 0.0, timeout=6.0)
        self.assertGreater(app._trigger_seen, 0.0)          # окно заметило горячую клавишу
        pump_until(app, lambda: "Сейчас" in app.chat.get(), timeout=8.0)
        self.assertIn("Сейчас", app.chat.get())              # и начало слушать микрофон

    def test_window_never_registers_hotkey_when_daemon_owns_it(self):
        app = self.build()  # embedded=None: ядром владеет демон
        self.assertIsNone(app._hotkey)
        self.assertFalse(hasattr(self.platform, "hotkey"))


if __name__ == "__main__":
    unittest.main()
