"""Тесты локального API: та же дверь, которой пользуется окно.

Проверяем не только ответы, но и обещания к безопасности: токен обязателен,
ключи не покидают ядро, подтверждение опасных действий проходит отдельным шагом.
"""

from __future__ import annotations

import json
import os
import stat
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from tests.helpers import FakeProviders, isolated_home, make_assistant

from jarvis.core import paths
from jarvis.interfaces.api import ApiError, LocalApi
from jarvis.interfaces.client import ApiClient, ApiTarget, ApiUnavailable, read_target
from jarvis.platform.base import CommandResult

SECRET_VALUE = "sk-or-v1-abcdef1234567890"


class FakePlatform:
    name = "linux"

    def __init__(self):
        self.commands: list[str] = []

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
        return True

    def beep(self):
        pass

    def setup_hotkey(self, spec, command):
        return True, "ок"

    def hotkey_hint(self, spec, command):
        return ""

    def tray_supported(self):
        return True


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self._ctx = isolated_home()
        self.home = self._ctx.__enter__()
        self.platform = FakePlatform()
        self.patcher = mock.patch("jarvis.platform.get_platform", return_value=self.platform)
        self.patcher.start()
        self.providers = FakeProviders()
        self.providers.llm.enabled = False
        self.assistant = make_assistant(providers=self.providers)
        self.shutdown_calls: list[str] = []
        self.api = LocalApi(self.assistant, port=0, on_shutdown=lambda: self.shutdown_calls.append("stop"))
        self.api.start()
        self.client = ApiClient(ApiTarget(url=self.api.url, token=self.api.token))

    def tearDown(self):
        self.api.stop()
        self.patcher.stop()
        self._ctx.__exit__(None, None, None)

    # ------------------------------------------------------------- обнаружение
    def test_token_and_discovery_files_are_private(self):
        token_file = self.api.token_path
        discovery = paths.api_file()
        # путь из настроек раскрыт и ведёт именно в каталог состояния
        self.assertEqual(token_file, paths.state_dir() / "api.token")
        self.assertTrue(token_file.exists())
        self.assertTrue(discovery.exists())
        self.assertEqual(token_file.read_text(encoding="utf-8").strip(), self.api.token)
        data = json.loads(discovery.read_text(encoding="utf-8"))
        self.assertEqual(data["port"], self.api.port)
        self.assertEqual(data["url"], self.api.url)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(discovery.stat().st_mode), 0o600)
        # интерфейс находит ядро сам, без подсказок пользователя
        target = read_target()
        self.assertIsNotNone(target)
        self.assertEqual(target.url, self.api.url)
        self.assertEqual(target.token, self.api.token)

    def test_stop_removes_files(self):
        self.api.stop()
        self.assertFalse(paths.api_file().exists())
        self.assertFalse(self.api.token_path.exists())

    def test_health_needs_no_token(self):
        with urllib.request.urlopen(f"{self.api.url}/health", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["state"], "done")
        self.assertIn("status", payload)

    def test_requests_without_token_rejected(self):
        stranger = ApiClient(ApiTarget(url=self.api.url, token="wrong-token"))
        with self.assertRaises(ApiUnavailable) as ctx:
            stranger.status()
        self.assertIn("токен", str(ctx.exception))

    # ---------------------------------------------------------------- чтение
    def test_status_and_skills(self):
        status = self.client.status()
        # голос в статусе — обычное «да/нет», а не ссылка на функцию:
        # иначе окно показывает вместо ответа строку про метод
        self.assertIsInstance(status["voice_enabled"], bool)
        self.assertGreater(status["skills"]["total"], 5)
        skills = self.client.skills()
        dialogue = next(item for item in skills if item["id"] == "dialogue")
        self.assertTrue(dialogue["actions"])
        self.assertIn("permissions", dialogue)

    def test_confirmation_carries_question_and_request(self):
        """Окно подтверждения получает и вопрос навыка, и фразу пользователя."""
        answer = self.client.ask("перезагрузи компьютер", speak=False)
        self.assertEqual(answer["state"], "confirmation_required")
        self.assertTrue(answer["question"].strip(), "вопрос подтверждения пустой")
        self.assertEqual(answer["request"], "перезагрузи компьютер")
        self.client.confirm(answer["pending_id"], approved=False)

    def test_skill_toggle_persists_in_config(self):
        answer = self.client.toggle_skill("volume", False)
        self.assertFalse(answer["skill"]["enabled"])
        text = paths.config_path().read_text(encoding="utf-8")
        self.assertIn("volume", text)
        self.assertFalse(any(item["id"] == "volume" for item in self.client.skills() if item["enabled"]))
        self.client.toggle_skill("volume", True)
        self.assertIn("volume", {item["id"] for item in self.client.skills() if item["enabled"]})

    def test_unknown_skill_answers_404(self):
        with self.assertRaises(ApiUnavailable):
            self.client.toggle_skill("нет-такого", False)

    def test_history_and_journal(self):
        self.client.ask("сколько времени")
        history = self.client.history()
        self.assertTrue(any(item["role"] == "user" for item in history))
        journal = self.client.journal(limit=50)
        self.assertTrue(journal["entries"])
        self.assertTrue(journal["lines"])
        self.assertTrue(all(isinstance(line, str) for line in journal["lines"]))

    def test_events_include_state_changes(self):
        self.client.ask("привет")
        raw = self.client.events(since=0)
        self.assertTrue(raw)
        types = {event["type"] for event in raw}
        self.assertIn("reply", types)
        self.assertTrue(all("seq" in event for event in raw))

    # --------------------------------------------------------------- запросы
    def test_ask_returns_reply(self):
        answer = self.client.ask("сколько времени")
        self.assertEqual(answer["state"], "done")
        self.assertTrue(answer["reply"]["ok"])
        self.assertEqual(answer["reply"]["skill"], "time_date")

    def test_ask_unknown_is_polite(self):
        answer = self.client.ask("свари мне борщ из синей капусты")
        self.assertEqual(answer["state"], "done")
        self.assertFalse(answer["reply"]["ok"])
        self.assertEqual(answer["reply"]["error"], "not_understood")

    def test_empty_text_rejected(self):
        with self.assertRaises(ApiUnavailable):
            self.client.ask("   ")

    def test_ask_keeps_recognized_text_out_of_commands(self):
        self.client.ask("сколько свободной памяти")
        self.assertEqual(self.platform.commands, [])

    # ------------------------------------------------------- подтверждения
    def test_dangerous_action_asks_then_runs(self):
        answer = self.client.ask("выключи компьютер")
        self.assertEqual(answer["state"], "confirmation_required")
        self.assertIn("Выключить", answer["question"])
        pending = answer["pending_id"]
        self.assertEqual(self.platform.commands, [])  # до подтверждения ничего не запускаем

        final = self.client.confirm(pending, True)
        self.assertEqual(final["state"], "done")
        self.assertTrue(final["reply"]["ok"])
        self.assertTrue(any("poweroff" in command for command in self.platform.commands))

    def test_dangerous_action_can_be_declined(self):
        answer = self.client.ask("перезагрузи компьютер")
        self.assertEqual(answer["state"], "confirmation_required")
        final = self.client.confirm(answer["pending_id"], False)
        self.assertEqual(final["state"], "done")
        self.assertEqual(final["reply"]["error"], "cancelled")
        self.assertEqual(self.platform.commands, [])

    def test_result_polling_reports_waiting_or_done(self):
        answer = self.client.ask("привет")
        state = self.client.result(answer["pending_id"])
        self.assertEqual(state["state"], "done")
        with self.assertRaises(ApiUnavailable):
            self.client.result("нет-такого-номера")

    def test_voice_without_speech_is_explained(self):
        self.providers.voice_enabled = True  # у подставного провайдера это просто поле
        self.providers.recorder.path = None
        answer = self.client.voice()
        self.assertEqual(answer["state"], "done")
        self.assertFalse(answer["reply"]["ok"])
        self.assertEqual(answer["reply"]["error"], "no_speech")

    # ------------------------------------------------------ настройки секреты
    def test_config_never_returns_secret_value(self):
        os.environ["JARVIS_LLM_KEY"] = SECRET_VALUE
        self.assistant.refresh(reload_config=True)
        payload = self.client.config()
        dumped = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(SECRET_VALUE, dumped)
        self.assertEqual(payload["config"]["llm"]["api_key"], "${JARVIS_LLM_KEY}")
        os.environ.pop("JARVIS_LLM_KEY", None)

    def test_config_change_is_written_and_applied(self):
        answer = self.client.set_config("assistant.theme", "light")
        self.assertEqual(answer["state"], "done")
        self.assertIn('theme = "light"', paths.config_path().read_text(encoding="utf-8"))
        self.assertEqual(self.assistant.config.get("assistant.theme"), "light")

    def test_config_refuses_raw_secret(self):
        with self.assertRaises(ApiUnavailable) as ctx:
            self.client.set_config("llm.api_key", SECRET_VALUE)
        self.assertIn("секрет", str(ctx.exception).lower())
        self.assertNotIn(SECRET_VALUE, paths.config_path().read_text(encoding="utf-8"))

    def test_config_rejects_env_reference_from_ui(self):
        with self.assertRaises(ApiUnavailable):
            self.client.set_config("llm.api_key", "${JARVIS_LLM_KEY}")

    def test_secret_saved_to_env_only(self):
        answer = self.client.save_secret("JARVIS_LLM_KEY", SECRET_VALUE)
        self.assertEqual(answer["state"], "done")
        self.assertNotIn(SECRET_VALUE, json.dumps(answer, ensure_ascii=False))
        self.assertIn("…", answer["masked"])
        env_text = paths.env_file_path().read_text(encoding="utf-8")
        self.assertIn(f"JARVIS_LLM_KEY={SECRET_VALUE}", env_text)
        self.assertNotIn(SECRET_VALUE, paths.config_path().read_text(encoding="utf-8"))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(paths.env_file_path().stat().st_mode), 0o600)
        # после сохранения ядро сразу видит ключ и маскирует его в списке
        env = self.client.config()["env"]
        entry = next(item for item in env["names"] if item["name"] == "JARVIS_LLM_KEY")
        self.assertTrue(entry["set"])
        self.assertNotIn(SECRET_VALUE, json.dumps(env, ensure_ascii=False))

    def test_invalid_secret_name_rejected(self):
        with self.assertRaises(ApiUnavailable):
            self.client.save_secret("ключ от квартиры", SECRET_VALUE)
        with self.assertRaises(ApiUnavailable):
            self.client.save_secret("JARVIS_LLM_KEY", "   ")

    def test_report_hides_secret_values(self):
        self.client.save_secret("JARVIS_LLM_KEY", SECRET_VALUE)
        report = self.client.report()
        self.assertNotIn(SECRET_VALUE, report)
        self.assertIn("Jarvis", report)

    # -------------------------------------------------------------- служебное
    def test_unknown_path_and_method(self):
        with self.assertRaises(ApiUnavailable):
            self.client.get("/НетТакого")
        with self.assertRaises(ApiUnavailable):
            self.client.request("PUT", "/status")

    def test_shutdown_calls_callback(self):
        answer = self.client.shutdown()
        self.assertTrue(answer["stopping"])
        for _ in range(50):
            if self.shutdown_calls:
                break
            import time
            time.sleep(0.05)
        self.assertEqual(self.shutdown_calls, ["stop"])

    # ------------------------------------------------------ прямой вызов API
    def test_handle_works_without_http(self):
        """Ядро можно проверить и без сети: тот же разбор маршрутов."""
        result = self.api.handle("GET", "/status", {}, {})
        self.assertIn("status", result)
        with self.assertRaises(ApiError) as ctx:
            self.api.handle("GET", "/нет", {}, {})
        self.assertEqual(ctx.exception.status, 404)

    def test_api_only_listens_on_localhost(self):
        self.assertEqual(self.api.host, "127.0.0.1")
        self.assertNotIn("0.0.0.0", self.api.url)


if __name__ == "__main__":
    unittest.main()
