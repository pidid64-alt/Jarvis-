"""Тесты ядра «от реплики до ответа»: навыки, права, ошибки, отсутствие ключа."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from tests.helpers import FakeProviders, isolated_home, make_assistant

from jarvis.core import paths
from jarvis.core.errors import ProviderError, SecretMissingError
from jarvis.platform.base import CommandResult


class FakePlatform:
    """Подставная платформа: команды «выполняются» без реального запуска."""

    name = "linux"

    def __init__(self, result: CommandResult | None = None):
        self.commands: list[str] = []
        self.result = result or CommandResult(returncode=0, output="тестовый вывод")
        self.opened: list[str] = []

    def run(self, command, *, timeout=15.0, cwd=None):
        self.commands.append(command)
        return self.result

    def spawn(self, command, *, cwd=None):
        self.commands.append(command)
        return None

    def notify(self, title, body, urgency="normal"):
        return True

    def play_wav(self, path):
        return True

    def open_url(self, url):
        self.opened.append(url)
        return True

    def beep(self):
        pass

    def setup_hotkey(self, spec, command):
        return True, "ок"

    def hotkey_hint(self, spec, command):
        return ""

    def tray_supported(self):
        return True


class AssistantSkillTests(unittest.TestCase):
    def setUp(self):
        self._ctx = isolated_home()
        self.home = self._ctx.__enter__()
        self.platform = FakePlatform()
        self.patcher = mock.patch("jarvis.platform.get_platform", return_value=self.platform)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self._ctx.__exit__(None, None, None)

    # ------------------------------------------------------------- навыки
    def test_dialogue_skill_answers(self):
        assistant = make_assistant()
        reply = assistant.handle_text("привет")
        self.assertTrue(reply.ok)
        self.assertEqual(reply.skill, "dialogue")
        self.assertIn("Здравствуйте", reply.text)

    def test_time_skill_answers(self):
        assistant = make_assistant()
        reply = assistant.handle_text("сколько времени")
        self.assertEqual(reply.skill, "time_date")
        self.assertRegex(reply.text, r"\d{2}:\d{2}")

    def test_health_skill_reads_metrics(self):
        assistant = make_assistant()
        reply = assistant.handle_text("сколько свободной памяти")
        self.assertTrue(reply.ok)
        self.assertEqual(reply.skill, "system_health")

    def test_capabilities_lists_skills(self):
        assistant = make_assistant()
        reply = assistant.handle_text("что ты умеешь")
        self.assertIn("Умею", reply.text)

    def test_unknown_phrase_answered_politely(self):
        providers = FakeProviders()
        providers.llm.enabled = False  # без модели ядро честно говорит «не понял»
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_text("свари мне борщ из синей капусты")
        self.assertFalse(reply.ok)
        self.assertEqual(reply.error, "not_understood")

    def test_command_skill_runs_only_static_template(self):
        self.write_command_skill("t_static", "static_say", "echo тест",
                                 phrase="тестовая строка")
        assistant = make_assistant(providers=self.silent_providers())
        reply = assistant.handle_text("тестовая строка")
        self.assertTrue(reply.ok)
        self.assertEqual(self.platform.commands, ["echo тест"])

    def test_captured_text_never_enters_command(self):
        """Распознанный текст выбирает действие, но не попадает в команду."""
        self.write_command_skill("t_capture", "capt", "echo привет",
                                 phrase="повтори слово", capture=True)
        assistant = make_assistant(providers=self.silent_providers())
        assistant.handle_text("повтори слово rm -rf /")
        self.assertEqual(self.platform.commands, ["echo привет"])

    # ------------------------------------------------------------ служебное
    def write_command_skill(self, skill_id: str, action_id: str, command: str,
                            *, phrase: str, capture: bool = False,
                            allow_failure: bool = False) -> None:
        folder = paths.user_skills_dir() / skill_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "skill.json").write_text(json.dumps({
            "id": skill_id, "name": skill_id, "description": "",
            "permissions": {"shell": True, "notify": False},
            "actions": [{"id": action_id, "phrases": [phrase], "command": command,
                         "description": "тест", "capture": capture,
                         "allow_failure": allow_failure}],
        }, ensure_ascii=False), encoding="utf-8")

    def silent_providers(self) -> FakeProviders:
        providers = FakeProviders()
        providers.llm.enabled = False
        return providers

    # ------------------------------------------------------------- опасные
    def test_dangerous_action_declined(self):
        assistant = make_assistant()
        asked: list[str] = []

        def decline(question: str) -> bool:
            asked.append(question)
            return False

        reply = assistant.handle_text("выключи компьютер", confirm_callback=decline)
        self.assertFalse(reply.ok)
        self.assertEqual(reply.error, "cancelled")
        self.assertTrue(asked)
        self.assertEqual(self.platform.commands, [])  # команда не запускалась

    def test_dangerous_action_confirmed(self):
        assistant = make_assistant()
        reply = assistant.handle_text("заблокируй экран", confirm_callback=lambda q: True)
        self.assertTrue(reply.ok)
        self.assertEqual(self.platform.commands[-1], "loginctl lock-session || xdg-screensaver lock")

    def test_no_confirmation_channel_means_refusal(self):
        assistant = make_assistant()
        reply = assistant.handle_text("выключи компьютер")
        self.assertFalse(reply.ok)
        self.assertEqual(self.platform.commands, [])

    # -------------------------------------------------------------- ошибки
    def test_failing_command_reported_to_user(self):
        """Ненулевой код возврата — не «Готово», а понятная ошибка."""
        self.write_command_skill("t_fail", "boom", "false", phrase="проверка ошибки")
        self.platform.result = CommandResult(returncode=1, output="не вышло")
        assistant = make_assistant(providers=self.silent_providers())
        reply = assistant.handle_text("проверка ошибки")
        self.assertFalse(reply.ok)
        self.assertIn("ошибк", reply.text.lower())

    def test_allow_failure_action_accepted(self):
        self.write_command_skill("t_ok_fail", "grep_like", "false", phrase="проверь молча",
                                 allow_failure=True)
        self.platform.result = CommandResult(returncode=1, output="")
        assistant = make_assistant(providers=self.silent_providers())
        reply = assistant.handle_text("проверь молча")
        self.assertTrue(reply.ok)

    def test_broken_skill_does_not_crash_assistant(self):
        folder = paths.user_skills_dir() / "broken_skill"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "handler.py").write_text(
            "def handle(ctx, intent):\n    raise RuntimeError('навык сломался')\n",
            encoding="utf-8")
        (folder / "skill.json").write_text(json.dumps({
            "id": "broken_skill", "name": "Сломанный", "description": "",
            "entry": "handler.py", "permissions": {"notify": False},
            "actions": [{"id": "boom", "phrases": ["сломайся"], "description": "Упасть"}],
        }, ensure_ascii=False), encoding="utf-8")

        assistant = make_assistant()
        reply = assistant.handle_text("сломайся")
        self.assertFalse(reply.ok)
        self.assertEqual(reply.skill, "broken_skill")

    def test_disabled_skill_is_not_used(self):
        assistant = make_assistant()
        assistant.set_skill_enabled("dialogue", False, persist=False)
        reply = assistant.handle_text("привет")
        self.assertNotEqual(reply.skill, "dialogue")

    # ----------------------------------------------------------------- llm
    def test_missing_key_disables_llm_without_crash(self):
        providers = FakeProviders()
        providers.llm.enabled = False
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_text("расскажи что-нибудь интересное")
        self.assertFalse(reply.ok)  # без LLM честно говорим «не понял»
        self.assertEqual(reply.error, "not_understood")

    def test_llm_reply_used_when_available(self):
        providers = FakeProviders()
        providers.llm.answer = json.dumps({"actions": [{"action": "speak", "text": "Ответ модели"}]})
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_text("расскажи что-нибудь интересное")
        self.assertTrue(reply.ok)
        self.assertEqual(reply.text, "Ответ модели")

    def test_llm_ask_sets_pending_question(self):
        providers = FakeProviders()
        providers.llm.answer = json.dumps({"actions": [{"action": "ask", "text": "Какой именно?"}]})
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_text("сделай что-нибудь")
        self.assertTrue(reply.continue_dialog)
        self.assertIsNotNone(assistant.conversation.snapshot().get("pending"))

    def test_llm_unknown_action_id_ignored(self):
        providers = FakeProviders()
        providers.llm.answer = json.dumps({"actions": [{"action": "run", "id": "выдумка.нет"}]})
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_text("сделай что-нибудь странное")
        self.assertFalse(reply.ok)

    def test_llm_garbage_answered_without_crash(self):
        providers = FakeProviders()
        providers.llm.answer = "простите, я не умею в JSON"
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_text("сделай что-нибудь невероятное")
        self.assertFalse(reply.ok)  # мусор от модели не исполняется

    def test_search_skill_captures_query(self):
        providers = FakeProviders()
        providers.llm.enabled = False
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_text("поищи погоду")
        self.assertTrue(reply.ok)
        self.assertEqual(reply.skill, "web_search")
        self.assertEqual(providers.search.queries, ["погоду"])

    def test_llm_search_action_uses_provider(self):
        providers = FakeProviders()
        providers.llm.answer = json.dumps({"actions": [{"action": "search", "query": "тихоходки", "open": False}]})
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_text("мне интересно узнать про необычных существ")
        self.assertTrue(reply.ok)
        self.assertEqual(providers.search.queries, ["тихоходки"])

    def test_provider_error_answered_politely(self):
        providers = FakeProviders()
        providers.llm.answer = json.dumps({"actions": [{"action": "search", "query": "погода"}]})
        providers.search.search = lambda query: (_ for _ in ()).throw(ProviderError("нет сети"))
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_text("поищи погоду")
        self.assertFalse(reply.ok)

    # --------------------------------------------------------------- голос
    def test_voice_flow_records_and_transcribes(self):
        providers = FakeProviders(voice_enabled=True)
        providers.stt.text = "сколько времени"
        wav = paths.state_dir() / "test.wav"
        providers.recorder.path = wav
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_voice()
        self.assertTrue(reply.ok)
        self.assertEqual(reply.skill, "time_date")
        self.assertTrue(providers.spoken)

    def test_voice_recorder_failure_names_reason(self):
        providers = FakeProviders(voice_enabled=True)
        from jarvis.core.errors import ProviderError

        providers.record = lambda: (_ for _ in ()).throw(
            ProviderError("микрофон занят другой программой"))
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_voice(source="api")
        self.assertFalse(reply.ok)
        self.assertIn("микрофон занят", reply.text)

    def test_voice_unexpected_failure_is_explained(self):
        providers = FakeProviders(voice_enabled=True)

        def boom():
            raise RuntimeError("порт звука занят")

        providers.record = boom
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_voice(source="api")
        self.assertFalse(reply.ok)
        self.assertIn("порт звука занят", reply.text)
        self.assertNotIn("internal", reply.text)

    def test_voice_without_speech(self):
        providers = FakeProviders(voice_enabled=True)
        providers.recorder.path = None
        assistant = make_assistant(providers=providers)
        reply = assistant.handle_voice()
        self.assertFalse(reply.ok)
        self.assertEqual(reply.error, "no_speech")

    # ---------------------------------------------------------------- данные
    def test_status_and_report(self):
        assistant = make_assistant()
        status = assistant.status()
        self.assertGreater(status["skills"]["total"], 5)
        self.assertIn("providers", status)
        report = assistant.report()
        self.assertIn("Jarvis", report)

    def test_history_records_dialog(self):
        assistant = make_assistant(providers=self.silent_providers())
        assistant.handle_text("привет")
        history = assistant.history()
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[-1]["role"], "jarvis")


class ProviderKeyTests(unittest.TestCase):
    def test_llm_without_key_reports_not_ready(self):
        from jarvis.providers.llm import LLMProvider

        llm = LLMProvider(base_url="http://localhost:1/v1", api_key="", model="test")
        ready, reason = llm.available()
        self.assertFalse(ready)
        self.assertIn("ключ", reason)
        with self.assertRaises(SecretMissingError):
            llm.chat([{"role": "user", "content": "привет"}])

    def test_llm_without_model_reports_not_ready(self):
        from jarvis.providers.llm import LLMProvider

        llm = LLMProvider(base_url="http://localhost:1/v1", api_key="sk-test-123456", model="")
        ready, reason = llm.available()
        self.assertFalse(ready)
        self.assertIn("модель", reason)

    def test_state_never_returns_key(self):
        from jarvis.providers.llm import LLMProvider

        llm = LLMProvider(base_url="http://localhost:1/v1", api_key="sk-secret-value-123456", model="m")
        state = llm.state()
        self.assertNotIn("sk-secret-value-123456", json.dumps(state))
        self.assertIn("…", state["key_masked"])


if __name__ == "__main__":
    unittest.main()
