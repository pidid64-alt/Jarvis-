"""Deterministic offline tests for scheduling, thresholds and concurrent inbox writes."""
import json
import multiprocessing
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, Mock

import autonomy
import health_checks
import inbox_store
import jarvis


def metric(value, **extra):
    return json.dumps({"text": f"Значение: {value}", "metrics": {"value": value, **extra}})


def writer(path, prefix):
    for i in range(10):
        inbox_store.append(Path(path), f"{prefix}-{i}", prefix)


class LocalDecisionTests(unittest.TestCase):
    def test_thresholds(self):
        for key, alert, healthy in [("if_above", 80, 75), ("if_above_percent", 90, 75),
                                    ("if_below_percent", 10, 75)]:
            rule = {key: 75, "metric": "value"}
            self.assertTrue(autonomy.local_decision(rule, metric(alert)))
            self.assertFalse(autonomy.local_decision(rule, metric(healthy)))

    def test_missing_malformed_metrics_do_not_alert(self):
        rule = {"if_below_percent": 15, "metric": "value"}
        for raw in ("/dev/sda1: 10G из 100G, 90%", metric("10"), metric(True), metric(float("nan")),
                    '{"text":"нет датчика", "metrics":{}}', "{}", ""):
            with self.subTest(raw=raw):
                self.assertFalse(autonomy.local_decision(rule, raw))

    def test_battery_charging_not_low_battery_alert(self):
        rule = {"if_below_percent": 20, "metric": "value", "only_if_on_battery": True}
        self.assertFalse(autonomy.local_decision(rule, metric(10, on_battery=False)))
        self.assertTrue(autonomy.local_decision(rule, metric(10, on_battery=True)))

    def test_disk_uses_free_not_used(self):
        rule = {"if_below_percent": 15, "metric": "free_percent"}
        raw = json.dumps({"text": "/dev/sda1 занято 90% из 500G", "metrics": {"free_percent": 10}})
        self.assertTrue(autonomy.local_decision(rule, raw))

    def test_default_rules_work_without_llm(self):
        rules = autonomy.load_autonomy_config()["rules"]
        cases = {
            "check_updates": ("Доступно обновлений: 12.", "Обновлений нет."),
            "failed_services": ("Упавших сервисов: 2. test.service", "Сломанных сервисов нет, всё работает штатно."),
            "journal_errors": ("В логе этой загрузки одна критичная запись: test", "Критичных ошибок в логе этой загрузки нет."),
            "internet_check": ("Интернета нет.", "Интернет есть."),
        }
        for rule in rules:
            if rule["check"] in cases:
                alert, healthy = cases[rule["check"]]
                with self.subTest(rule=rule["id"]):
                    self.assertTrue(autonomy.local_decision(rule, alert))
                    self.assertFalse(autonomy.local_decision(rule, healthy))

    def test_unknown_custom_rule_needs_llm(self):
        self.assertIsNone(autonomy.local_decision({"prompt": "Проверь"}, "Что-то произошло"))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "schedule.json"
        self.inbox_path = Path(self.tmp.name) / "inbox.json"
        for name, value in [("SCHEDULE_FILE", self.state_path), ("INBOX_FILE", self.inbox_path)]:
            p = patch.object(autonomy, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.now = datetime(2026, 9, 8, 12).timestamp()
        self.clock = patch.object(autonomy.time, "time", return_value=self.now).start()
        self.addCleanup(patch.stopall)
        self.rule = {"id": "test", "check": "cpu_temp", "every_minutes": 1,
                     "metric": "value", "if_above": 75, "repeat_seconds": 3600}
        self.state = {"rules": {}, "last_notify": 0}
        self.cfg = {"rules": [self.rule], "min_notify_interval_seconds": 300}

    def run_rule(self, raw, rule=None, force=False):
        with patch.object(autonomy, "run_check", return_value=raw):
            autonomy.process_rule(rule or self.rule, None, None, self.state, force=force)

    def test_offline_notify_and_persistent_dedup(self):
        self.run_rule(metric(80))
        self.assertEqual(autonomy.flush_pending(self.state, self.cfg), 1)
        inbox_store.atomic_write(self.state_path, self.state)
        self.state = autonomy.load_schedule()
        self.clock.return_value += 600
        self.run_rule(metric(81))  # readings can fluctuate without spamming
        self.assertEqual(autonomy.flush_pending(self.state, self.cfg), 0)
        self.assertEqual(len(json.loads(self.inbox_path.read_text())), 1)

    def test_recovery_rearms_if_changed_rule(self):
        self.rule["if_changed"] = True
        self.run_rule(metric(80))
        autonomy.flush_pending(self.state, self.cfg)
        self.clock.return_value += 600
        self.run_rule(metric(60))
        self.clock.return_value += 600
        self.run_rule(metric(80))
        self.assertEqual(autonomy.flush_pending(self.state, self.cfg), 1)

    def test_all_checks_run_during_notification_cooldown(self):
        second = {**self.rule, "id": "second"}
        self.cfg["rules"].append(second)
        self.state["last_notify"] = self.now
        with patch.object(autonomy, "run_check", return_value=metric(90)) as run:
            autonomy.run_tick(self.cfg, None, None, self.state)
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(v.get("pending") for v in self.state["rules"].values()))
        self.clock.return_value += 300
        self.assertEqual(autonomy.flush_pending(self.state, self.cfg), 1)
        self.clock.return_value += 300
        self.assertEqual(autonomy.flush_pending(self.state, self.cfg), 1)
        self.assertEqual(len(json.loads(self.inbox_path.read_text())), 2)

    def test_quiet_hours_queue_but_do_not_deliver(self):
        with patch.object(autonomy, "run_check", return_value=metric(90)), \
             patch.object(autonomy, "in_quiet_hours", return_value=True):
            autonomy.run_tick(self.cfg, None, None, self.state)
        self.assertTrue(self.state["rules"]["test"].get("pending"))
        self.assertFalse(self.inbox_path.exists())
        self.run_rule(metric(60), force=True)  # recovered overnight
        self.assertEqual(autonomy.flush_pending(self.state, self.cfg), 0)

    def test_daily_catches_up_and_runs_once_per_date(self):
        rule = {"time": "09:00"}
        self.assertTrue(autonomy.rule_due(rule, {}, self.now))
        self.assertFalse(autonomy.rule_due(rule, {"last_run": self.now - 60}, self.now))
        self.assertFalse(autonomy.rule_due(rule, {}, self.now - 4 * 3600))
        self.assertTrue(autonomy.rule_due(rule, {"last_run": self.now - 86400}, self.now))

    def test_once_forces_daily_rules_but_respects_disabled(self):
        self.rule["time"] = "23:00"
        self.rule.pop("every_minutes")
        self.run_rule(metric(90))
        self.assertFalse(self.state["rules"]["test"].get("pending"))
        self.run_rule(metric(90), force=True)
        self.assertTrue(self.state["rules"]["test"].get("pending"))
        with patch.object(autonomy, "run_check") as run:
            autonomy.process_rule({**self.rule, "enabled": False}, None, None, self.state, force=True)
        run.assert_not_called()

    def test_llm_failure_does_not_disable_local_alerts(self):
        with patch.object(autonomy, "run_check", return_value=metric(90)), \
             patch.object(autonomy, "decide_via_llm", side_effect=RuntimeError) as llm:
            autonomy.process_rule(self.rule, Mock(), None, self.state)
        llm.assert_not_called()
        self.assertEqual(autonomy.flush_pending(self.state, self.cfg), 1)

    def test_corrupt_state_recovers(self):
        self.state_path.write_text("invalid")
        with self.assertLogs(level="ERROR"):
            self.assertEqual(autonomy.load_schedule(), {"rules": {}, "last_notify": 0})

    def test_failed_check_drops_stale_pending(self):
        self.run_rule(metric(90))
        self.run_rule("", force=True)
        self.assertEqual(autonomy.flush_pending(self.state, self.cfg), 0)


class SafetyTests(unittest.TestCase):
    def test_whitelist_requires_explicit_safety_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "commands.json"
            path.write_text(json.dumps({"commands": [
                {"id": "safe", "command": "echo ok", "autonomy_safe": True},
                {"id": "unsafe", "command": "echo mutating", "speak_output": True},
                {"id": "danger", "command": "echo danger", "autonomy_safe": True, "tags": ["dangerous"]},
                {"id": "confirm", "command": "echo confirm", "autonomy_safe": True, "confirm": True},
            ]}))
            with patch.object(autonomy, "COMMANDS_FILE", path):
                self.assertEqual(autonomy.load_command_lookup(), {"safe": "echo ok"})
                with patch.object(autonomy.subprocess, "run") as run:
                    self.assertEqual(autonomy.run_check("unsafe"), "")
                    run.assert_not_called()

    def test_default_rules_all_have_audited_checks(self):
        lookup = autonomy.load_command_lookup()
        for rule in autonomy.load_autonomy_config()["rules"]:
            self.assertIn(rule["check"], lookup)
        self.assertNotIn("reboot", lookup)

    def test_check_failure_not_treated_as_data(self):
        with patch.object(autonomy, "load_command_lookup", return_value={"x": "echo x"}), \
             patch.object(autonomy.subprocess, "run", return_value=Mock(returncode=1, stdout="90", stderr="error")):
            self.assertEqual(autonomy.run_check("x"), "")


class InboxTests(unittest.TestCase):
    def test_concurrent_writers_and_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.json"
            procs = [multiprocessing.Process(target=writer, args=(str(path), str(i))) for i in range(3)]
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join(10)
                self.assertEqual(proc.exitcode, 0)
            self.assertEqual(len(json.loads(path.read_text())), 30)
            item = inbox_store.peek(path)
            inbox_store.append(path, "written while TTS plays", "new")
            inbox_store.acknowledge(path, item["id"])
            data = json.loads(path.read_text())
            self.assertEqual(len(data), 30)
            self.assertEqual(data[-1]["source"], "new")

    def test_corrupt_inbox_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.json"
            path.write_text("broken")
            with self.assertRaises(ValueError):
                inbox_store.append(path, "test", "test")
            self.assertEqual(path.read_text(), "broken")

    def test_legacy_record_and_queue_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.json"
            path.write_text('[{"text": "legacy"}]')
            item = inbox_store.peek(path)
            self.assertTrue(item["id"])
            inbox_store.acknowledge(path, item["id"])
            for i in range(55):
                inbox_store.append(path, str(i), "test")
            self.assertEqual(len(json.loads(path.read_text())), 50)
            self.assertEqual(inbox_store.peek(path)["text"], "5")

    def test_jarvis_does_not_speak_in_quiet_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.json"
            inbox_store.append(path, "test", "test")
            with patch.object(jarvis, "INBOX_FILE", path), \
                 patch.object(autonomy, "in_quiet_hours", return_value=True), \
                 patch.object(jarvis, "speak") as speak:
                self.assertEqual(jarvis.drain_inbox({}), 0)
            speak.assert_not_called()
            self.assertEqual(len(json.loads(path.read_text())), 1)


class HealthMetricTests(unittest.TestCase):
    def test_memory_uses_available_including_reclaimable_cache(self):
        with patch.object(Path, "read_text", return_value="MemTotal: 1000 kB\nMemAvailable: 200 kB\nMemFree: 10 kB\n"):
            got = health_checks.collect("memory_usage")
        self.assertAlmostEqual(got["metrics"]["used_percent"], 80)

    def test_cpu_uses_hottest_cpu_not_gpu(self):
        sensors = {"coretemp-isa-0000": {"Core 0": {"temp2_input": 60}, "Core 1": {"temp3_input": 81}},
                   "amdgpu-pci-0100": {"edge": {"temp1_input": 95}}}
        with patch.object(health_checks.subprocess, "run", return_value=Mock(stdout=json.dumps(sensors))):
            self.assertEqual(health_checks.collect("cpu_temp")["metrics"]["temperature"], 81)


if __name__ == "__main__":
    unittest.main()
