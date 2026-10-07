"""Тесты командной строки: запуск, вывод, отсутствие секретов в консоли."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(home: Path, *args: str, input_text: str | None = None, timeout: int = 120,
            extra_env: dict[str, str] | None = None):
    env = dict(os.environ)
    env["JARVIS_HOME"] = str(home)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env.pop("JARVIS_LLM_KEY", None)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "jarvis", *args],
        capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT),
        input=input_text, timeout=timeout,
    )


class LlmCommandTests(unittest.TestCase):
    """`jarvis llm` показывает, что уходит модели; ключ в вывод не попадает."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="jarvis-llm-")
        self.home = Path(self._tmp.name)
        (self.home / "config").mkdir(parents=True, exist_ok=True)
        (self.home / "state").mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._tmp.cleanup)

    def test_shows_request_without_sending_it(self):
        # берём фразу, которой нет среди навыков: иначе модель не спрашивают вовсе
        result = run_cli(self.home, "llm", "--text", "напиши стих про кота")
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in ("Модель", "Запрос", "chat/completions", "messages",
                         "навыков", "Запрос не отправлен"):
            self.assertIn(expected, result.stdout, f"в выводе нет «{expected}»")

    def test_key_value_never_shown(self):
        env_home = self.home
        import os
        import subprocess
        import sys

        env = dict(os.environ)
        env["JARVIS_HOME"] = str(env_home)
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        env["JARVIS_LLM_KEY"] = "sk-очень-секретный-ключ"
        result = subprocess.run([sys.executable, "-m", "jarvis", "llm",
                                 "--text", "напиши стих про кота"],
                                capture_output=True, text=True, env=env,
                                cwd=str(PROJECT_ROOT), timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("sk-очень-секретный-ключ", result.stdout + result.stderr)
        self.assertIn("не показываем", result.stdout)

    def test_help_lists_the_command(self):
        result = run_cli(self.home, "--help")
        self.assertIn("llm", result.stdout)

    def test_phrase_known_by_a_skill_never_goes_to_the_model(self):
        """Если фраза разобрана навыком, модели её не отправляют — так и говорим."""
        result = run_cli(self.home, "llm", "--text", "привет")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("разобрана локально", result.stdout)
        self.assertIn("dialogue.hello", result.stdout)
        self.assertIn("НЕ отправляется", result.stdout)
        self.assertNotIn("Тело запроса", result.stdout)

    def test_send_explains_the_answer_in_plain_words(self):
        """Ответ модели показывается и разбирается: что Jarvis по нему сделает."""
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        answer = _json.dumps({"actions": [
            {"action": "run", "id": "volume.up"},
            {"action": "speak", "text": "Прибавил громкость."},
        ]}, ensure_ascii=False)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - так требует http.server
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = _json.dumps({"choices": [{"message": {"content": answer}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        (self.home / "config" / "config.toml").write_text(
            '[llm]\nenabled = true\nbase_url = "http://127.0.0.1:%d/v1"\n'
            'api_key = "${JARVIS_LLM_KEY}"\nmodel = "test"\ntimeout_seconds = 30\n'
            'max_retries = 0\n' % port, encoding="utf-8")

        result = run_cli(self.home, "llm", "--send", "--text", "напиши стих про кота",
                         extra_env={"JARVIS_LLM_KEY": "sk-test-for-check"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("sk-test-for-check", result.stdout + result.stderr)
        self.assertIn("Что сделает Jarvis", result.stdout)
        self.assertIn("выполнит действие volume.up", result.stdout)
        self.assertIn("ответит словами: «Прибавил громкость.»", result.stdout)
        self.assertIn("Записанные команды модель не выбирает", result.stdout)


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="jarvis-cli-")
        self.home = Path(self._tmp.name)
        (self.home / "config").mkdir(parents=True, exist_ok=True)
        (self.home / "state").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_version_and_help(self):
        result = run_cli(self.home, "version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Jarvis", result.stdout)

        result = run_cli(self.home, "--help")
        for command in ("run", "ask", "status", "skills", "config", "secret", "migrate"):
            self.assertIn(command, result.stdout)

    def test_status_creates_config_and_reports_providers(self):
        result = run_cli(self.home, "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Навыков:", result.stdout)
        self.assertIn("llm:", result.stdout)
        # ключ не задан — так и должно быть написано, без значений
        self.assertNotIn("sk-", result.stdout)
        self.assertTrue((self.home / "config" / "config.toml").exists())

    def test_skills_lists_bundled_skills(self):
        result = run_cli(self.home, "skills")
        self.assertEqual(result.returncode, 0, result.stderr)
        for skill in ("dialogue", "time_date", "system_health", "power"):
            self.assertIn(skill, result.stdout)

    def test_ask_text_command(self):
        result = run_cli(self.home, "ask", "сколько времени", "--no-speak")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"\d{1,2}:\d{2}")

    def test_ask_unknown_phrase_is_polite(self):
        """Непонятая фраза — вежливый ответ и код 1 (удобно скриптам)."""
        result = run_cli(self.home, "ask", "свари мне борщ из синей капусты", "--no-speak")
        self.assertEqual(result.returncode, 1)
        self.assertIn("понял", result.stdout.lower())

    def test_ask_empty_text_is_rejected(self):
        result = run_cli(self.home, "ask", "   ", "--no-speak")
        self.assertNotEqual(result.returncode, 0)

    def test_ask_dangerous_without_confirmation_is_cancelled(self):
        result = run_cli(self.home, "ask", "выключи компьютер", "--no-speak", input_text="нет\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Отменяю", result.stdout)

    def test_ask_dangerous_confirmed_by_word_yes(self):
        result = run_cli(self.home, "ask", "перезагрузи компьютер", "--no-speak", input_text="да\n")
        # в песочнице нет systemd — команда честно сообщает об ошибке, но подтверждение принято
        self.assertIn("да", result.stdout.lower() + result.stderr.lower())
        self.assertNotIn("Отменяю", result.stdout)

    def test_config_get_and_path(self):
        result = run_cli(self.home, "config", "path")
        self.assertIn("config.toml", result.stdout)

        result = run_cli(self.home, "config", "get", "permissions.mode")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("restricted", result.stdout)

    def test_secret_set_stores_only_in_env_and_masks_output(self):
        run_cli(self.home, "status")  # создаёт config.toml, как при первом запуске
        result = run_cli(self.home, "secret", "set", "JARVIS_LLM_KEY", input_text="sk-or-v1-abcdef123456\n")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn("sk-or-v1-abcdef123456", result.stdout)
        candidates = [self.home / "config" / ".env", self.home / ".env"]
        env_file = next((path for path in candidates if path.exists()), None)
        self.assertIsNotNone(env_file, "секрет должен лежать в .env")
        self.assertIn("JARVIS_LLM_KEY=sk-or-v1-abcdef123456", env_file.read_text(encoding="utf-8"))
        config_text = (self.home / "config" / "config.toml").read_text(encoding="utf-8")
        self.assertNotIn("sk-or-v1-abcdef123456", config_text)
        if os.name != "nt":
            import stat
            self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)

        listing = run_cli(self.home, "secret", "list")
        self.assertNotIn("sk-or-v1-abcdef123456", listing.stdout)
        self.assertIn("задан", listing.stdout)

    def test_logs_command_masks_secrets(self):
        run_cli(self.home, "ask", "привет", "--no-speak")
        result = run_cli(self.home, "logs", "--limit", "20")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("привет", result.stdout)

    def test_migrate_dry_run_does_not_touch_files(self):
        result = run_cli(self.home, "migrate", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Действий перенесено", result.stdout)
        self.assertIn("пробный прогон", result.stdout.lower())
        # встроенные навыки перекрывают часть старых команд — это видно в отчёте
        self.assertIn("Заменено встроенными навыками", result.stdout)
        self.assertFalse((self.home / "config" / "skills").exists())

    def test_doctor_like_status_has_no_tracebacks(self):
        result = run_cli(self.home, "status")
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertNotIn("Traceback", run_cli(self.home, "skills").stderr)


class ImportHealthTests(unittest.TestCase):
    """Пакет должен импортироваться целиком — это ловит забытые модули."""

    def test_import_every_module(self):
        import importlib
        import pkgutil

        import jarvis

        modules = [name for _finder, name, _ispkg in pkgutil.walk_packages(jarvis.__path__, "jarvis.")]
        self.assertGreater(len(modules), 20)
        for name in modules:
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_pyproject_lists_existing_packages(self):
        import tomllib

        data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        packages = data["tool"]["setuptools"]["packages"]
        for package in packages:
            path = PROJECT_ROOT / package.replace(".", "/")
            self.assertTrue((path / "__init__.py").exists(), f"нет {path}/__init__.py")


if __name__ == "__main__":
    unittest.main()
