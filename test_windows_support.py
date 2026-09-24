#!/usr/bin/env python3
"""
Тесты кроссплатформенного (Windows) слоя: плейсхолдеры путей, декодирование
вывода команд, файловый триггер вместо SIGUSR1, блокировки без fcntl,
env-файл, корректность commands-win.json и выбор модели wake-word.

Запуск: python -m unittest test_windows_support.py -v
(проходят и на Linux — гоняются ветки кода, общие для обеих платформ,
и проверяется сам Windows-контракт статических данных.)
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import jarvis
import platform_support as plat
from health_checks import collect


BASE = Path(__file__).resolve().parent


class PlaceholderTests(unittest.TestCase):
    def test_base_and_state_substituted(self):
        out = plat.substitute_placeholders(
            'python "{base}\\health_checks.py" disk_space', BASE, Path("/tmp/state"))
        self.assertIn(str(BASE), out)
        self.assertNotIn("{base}", out)
        out2 = plat.substitute_placeholders("cat {state}/inbox.json", BASE, Path("/tmp/state"))
        self.assertEqual(out2, "cat /tmp/state/inbox.json")

    def test_braces_in_shell_not_touched(self):
        # Фигурные скобки awk/printf не должны ломаться (не .format()).
        src = "awk '{print $1}' && echo {base}"
        out = plat.substitute_placeholders(src, BASE, Path("/s"))
        self.assertIn("awk '{print $1}'", out)
        self.assertIn(str(BASE), out)


class DecodeOutputTests(unittest.TestCase):
    def test_utf8_passthrough(self):
        self.assertEqual(plat.decode_output("привет".encode("utf-8")), "привет")

    def test_str_untouched(self):
        self.assertEqual(plat.decode_output("ok"), "ok")

    def test_empty(self):
        self.assertEqual(plat.decode_output(b""), "")
        self.assertEqual(plat.decode_output(None), "")

    def test_windows_cp866_fallback(self):
        raw = "Интернет есть.".encode("cp866")
        with patch.object(plat, "IS_WINDOWS", True):
            self.assertEqual(plat.decode_output(raw), "Интернет есть.")

    def test_windows_cp866_preferred_over_undecodable_utf8(self):
        # cmd.exe на русской локали отдаёт OEM cp866 — он должен
        # расшифровываться, а не сыпаться исключениями.
        raw = "Интернет есть.".encode("cp866")
        with patch.object(plat, "IS_WINDOWS", True):
            self.assertEqual(plat.decode_output(raw), "Интернет есть.")
        # а без windows-флага те же байты не роняют вызывающий код
        out = plat.decode_output(b"\x84\x82 broken \xff")
        self.assertIsInstance(out, str)

    def test_undecodable_replaced_not_raised(self):
        with patch.object(plat, "IS_WINDOWS", True):
            out = plat.decode_output(b"\xff\xfe\xfa")
            self.assertIsInstance(out, str)


class FileLockTests(unittest.TestCase):
    def test_exclusive_nonblocking_conflict_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "x.lock"
            with plat.file_lock(lock):
                with self.assertRaises(BlockingIOError):
                    with plat.file_lock(lock, blocking=False):
                        pass
            # после освобождения — берётся
            with plat.file_lock(lock, blocking=False):
                pass


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.trigger = self.state / "trigger"
        p1 = patch.object(plat, "STATE_DIR", self.state)
        p2 = patch.object(plat, "TRIGGER_FILE", self.trigger)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        jarvis.trigger_event.clear()

    def test_fire_then_consume_once(self):
        plat.fire_trigger()
        self.assertTrue(self.trigger.exists())
        self.assertTrue(plat.consume_trigger())
        self.assertFalse(plat.consume_trigger())

    def test_stale_trigger_ignored_and_removed(self):
        self.trigger.write_text("0", encoding="utf-8")
        old = time.time() - plat.TRIGGER_MAX_AGE - 1
        os.utime(self.trigger, (old, old))
        self.assertFalse(plat.consume_trigger())
        self.assertFalse(self.trigger.exists())

    def test_wait_for_trigger_sees_file(self):
        plat.fire_trigger()
        self.assertTrue(jarvis.wait_for_trigger(timeout=1.0))

    def test_wait_for_trigger_timeout(self):
        jarvis.trigger_event.clear()
        started = time.monotonic()
        self.assertFalse(jarvis.wait_for_trigger(timeout=0.4))
        self.assertGreaterEqual(time.monotonic() - started, 0.3)


class EnvFileTests(unittest.TestCase):
    def test_applies_and_does_not_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "env"
            path.write_text(
                "# comment\n"
                "JARVIS_TEST_VAR_A=hello\n"
                "JARVIS_TEST_VAR_B=\"quoted\"\n"
                "PATH=must_not_override\n",
                encoding="utf-8")
            os.environ.pop("JARVIS_TEST_VAR_A", None)
            os.environ.pop("JARVIS_TEST_VAR_B", None)
            applied = plat.load_env_file(path)
            try:
                self.assertEqual(os.environ["JARVIS_TEST_VAR_A"], "hello")
                self.assertEqual(os.environ["JARVIS_TEST_VAR_B"], "quoted")
                self.assertNotIn("PATH", applied)  # уже задана — не перетираем
            finally:
                os.environ.pop("JARVIS_TEST_VAR_A", None)
                os.environ.pop("JARVIS_TEST_VAR_B", None)

    def test_missing_file_ok(self):
        self.assertEqual(plat.load_env_file(Path("/nonexistent/env")), {})


class CommandsWinJsonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(
            (BASE / "commands-win.json").read_text(encoding="utf-8"))
        cls.commands = cls.data["commands"]
        cls.by_id = {c["id"]: c for c in cls.commands}

    def test_unique_ids(self):
        ids = [c["id"] for c in self.commands]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_command_has_phrases_and_command(self):
        for c in self.commands:
            with self.subTest(id=c["id"]):
                self.assertTrue(c.get("phrases"), "нет фраз")
                self.assertTrue(c.get("command"), "нет команды")

    def test_no_linux_only_paths(self):
        for c in self.commands:
            cmd = c.get("command", "") + " " + (c.get("autonomy_command") or "")
            with self.subTest(id=c["id"]):
                self.assertNotIn("$HOME/jarvis", cmd)
                self.assertNotIn("xdotool", cmd)
                self.assertNotIn("parecord", cmd)
                self.assertNotIn("systemctl", cmd)
                self.assertNotIn("pacman", cmd)

    def test_script_references_use_base_placeholder(self):
        for c in self.commands:
            if "scripts_win" in c.get("command", ""):
                with self.subTest(id=c["id"]):
                    self.assertIn("{base}", c["command"])

    def test_dangerous_commands_still_require_confirmation(self):
        for cid in ("poweroff", "reboot", "logout", "system_update"):
            c = self.by_id[cid]
            self.assertTrue(c.get("confirm") or "dangerous" in c.get("tags", []),
                            f"{cid} потерял confirm/dangerous")

    def test_power_commands_map_to_windows(self):
        self.assertIn("shutdown.exe", self.by_id["poweroff"]["command"])
        self.assertIn("shutdown.exe", self.by_id["reboot"]["command"])
        self.assertIn("LockWorkStation", self.by_id["lock_screen"]["command"])

    def test_autonomy_safe_entries_have_autonomy_command(self):
        for c in self.commands:
            if c.get("autonomy_safe"):
                with self.subTest(id=c["id"]):
                    self.assertTrue(c.get("autonomy_command"))

    def test_autonomy_rule_checks_exist_in_commands_win(self):
        # Правила autonomy.json обязаны ссылаться на существующие id.
        rules = json.loads((BASE / "autonomy.json").read_text(encoding="utf-8"))
        ids = set(self.by_id)
        for rule in rules["rules"]:
            with self.subTest(rule=rule["id"]):
                self.assertIn(rule["check"], ids)

    def test_internet_check_phrases_match_autonomy_text(self):
        # autonomy.json ждёт ровно «Интернета нет.» — команда печатает так же.
        cmd = self.by_id["internet_check"]["command"]
        self.assertIn("Интернета нет.", cmd)
        self.assertIn("Интернет есть.", cmd)

    def test_exact_match_still_works_over_windows_commands(self):
        got = jarvis.match_commands_exact(
            "джарвис открой дискорд", self.commands, 6)
        self.assertEqual([c["id"] for c in got], ["open_discord"])

    def test_llm_prompt_never_sees_shell_field(self):
        import llm_parser
        block = llm_parser.build_commands_block(self.commands)
        self.assertNotIn("shutdown.exe", block)
        self.assertNotIn("{base}", block)


class LoadCommandsPlatformTests(unittest.TestCase):
    def test_linux_loads_commands_json(self):
        with patch.object(plat, "IS_WINDOWS", False):
            self.assertEqual(plat.commands_file_name(), "commands.json")
            cmds = jarvis.load_commands()
            self.assertTrue(any(c["id"] == "solve_terminal" for c in cmds))

    def test_windows_loads_commands_win_json(self):
        with patch.object(plat, "IS_WINDOWS", True):
            self.assertEqual(plat.commands_file_name(), "commands-win.json")
            cmds = jarvis.load_commands()
            self.assertTrue(cmds)
            self.assertTrue(any(c["id"] == "open_discord" for c in cmds))
            # linux-only команд в windows-whitelist не должно быть
            self.assertFalse(any(c["id"] == "solve_terminal" for c in cmds))


class WakewordPlatformTests(unittest.TestCase):
    def test_onnx_extension_forces_onnx(self):
        self.assertEqual(
            plat.detect_wakeword_framework(Path("x/jarvis.onnx")), "onnx")

    def test_tflite_on_windows_uses_onnx(self):
        with patch.object(plat, "IS_WINDOWS", True):
            self.assertEqual(
                plat.detect_wakeword_framework(Path("x/jarvis.tflite")), "onnx")

    def test_tflite_on_linux_keeps_tflite(self):
        with patch.object(plat, "IS_WINDOWS", False):
            self.assertEqual(
                plat.detect_wakeword_framework(Path("x/jarvis.tflite")), "tflite")

    def test_windows_prefers_onnx_sibling(self):
        model = BASE / "models" / "wakeword" / "jarvis.tflite"
        onnx_sibling = model.with_suffix(".onnx")
        if not onnx_sibling.exists():
            self.skipTest("в репозитории нет jarvis.onnx")
        with patch.object(plat, "IS_WINDOWS", True):
            got = plat.wakeword_model_on_windows(model)
        self.assertEqual(got.suffix, ".onnx")

    def test_repo_ships_both_wakeword_variants(self):
        self.assertTrue((BASE / "models" / "wakeword" / "jarvis.onnx").exists())
        self.assertTrue((BASE / "models" / "wakeword" / "jarvis.tflite").exists())


class HealthChecksPortableTests(unittest.TestCase):
    def test_disk_space_runs_everywhere(self):
        got = collect("disk_space")
        self.assertIn("free_percent", got["metrics"])

    def test_health_text_prints_prose(self):
        import subprocess
        import sys
        out = subprocess.run(
            [sys.executable, str(BASE / "health_text.py"), "disk_space"],
            capture_output=True, check=True)
        text = out.stdout.decode("utf-8").strip()
        self.assertTrue(text)
        self.assertNotIn("{", text)  # не JSON


class PowerScriptsPresenceTests(unittest.TestCase):
    """Ключевые scripts_win реально существуют (опечатка = молчаливый сбой)."""

    REQUIRED = [
        "status.ps1", "diskspace.ps1", "battery.ps1", "failedservices.ps1",
        "recentlogs.ps1", "checkupdates.ps1", "weather.ps1", "news.ps1",
        "discord.ps1", "browser_keys.ps1", "window.ps1", "media.ps1",
        "screenshot.ps1", "timer.ps1", "clipboard.ps1", "net.ps1",
    ]

    def test_required_scripts_present(self):
        for name in self.REQUIRED:
            with self.subTest(script=name):
                self.assertTrue((BASE / "scripts_win" / name).exists())

    def test_scripts_param_block_is_first(self):
        # param() в PowerShell должен быть первой инструкцией; внутри
        # here-strings он разрешён — их отсеиваем.
        for path in (BASE / "scripts_win").glob("*.ps1"):
            text = path.read_text(encoding="utf-8")
            # вырезаем here-strings @' ... '@
            cleaned = []
            inside = False
            for line in text.splitlines():
                stripped = line.strip()
                if not inside and stripped.endswith("@'"):
                    inside = True
                    if stripped == "@'":
                        continue
                    # присваивание вида «$x = @'» — содержимое со следующей
                if inside:
                    if stripped.startswith("'@"):
                        inside = False
                    continue
                cleaned.append(line)
            lines = [l for l in cleaned if l.strip() and not l.strip().startswith("#")]
            if any(l.startswith("param(") for l in lines[1:]):
                self.fail(f"{path.name}: param() не первой инструкцией")


if __name__ == "__main__":
    unittest.main(verbosity=2)
