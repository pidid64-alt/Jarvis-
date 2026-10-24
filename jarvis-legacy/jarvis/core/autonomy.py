"""Автономные проверки: ассистент сам замечает проблемы.

Правила описываются в ``config.toml`` (``[[autonomy.rules]]``) и в старой
версии переносятся ``jarvis migrate``. Каждое правило — это проверка
(метрика системы) плюс условие плюс текст, который нужно озвучить.

Никаких произвольных команд от правил не запускается: только встроенные
read-only проверки из ``jarvis.platform.metrics`` и действия навыков
с разрешением ``autonomy``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..platform import metrics
from .logging_setup import get_logger

log = get_logger("core.autonomy")

CHECK_ALIASES = {
    "memory_usage": "memory",
    "disk_space": "disk",
    "battery_status": "battery",
    "cpu_temp": "cpu_temp",
    "internet_check": "internet",
}


@dataclass
class RuleOutcome:
    rule_id: str
    fired: bool
    text: str = ""
    reason: str = ""


class Autonomy:
    """Планировщик проверок с тихими часами и защитой от повторов."""

    def __init__(self, config: Any, providers: Any, journal: Any = None, assistant: Any = None):
        self.config = config
        self.providers = providers
        self.journal = journal
        self.assistant = assistant
        self._last_run: dict[str, float] = {}
        self._last_text: dict[str, str] = {}

    # ------------------------------------------------------------------ timing
    def _in_quiet_hours(self) -> bool:
        start = str(self.config.get("autonomy.quiet_hours_start", "") or "")
        end = str(self.config.get("autonomy.quiet_hours_end", "") or "")
        if not start or not end:
            return False
        try:
            now = datetime.now().time()
            start_time = datetime.strptime(start, "%H:%M").time()
            end_time = datetime.strptime(end, "%H:%M").time()
        except ValueError:
            return False
        if start_time <= end_time:
            return start_time <= now <= end_time
        return now >= start_time or now <= end_time

    def _due(self, rule: dict[str, Any]) -> bool:
        rule_id = str(rule.get("id", "rule"))
        repeat = float(rule.get("repeat_seconds", 21600))
        last = self._last_run.get(rule_id, 0.0)
        return time.monotonic() - last >= repeat

    # ------------------------------------------------------------------- check
    def check_rule(self, rule: dict[str, Any]) -> RuleOutcome:
        rule_id = str(rule.get("id", "rule"))
        check = str(rule.get("check", ""))
        if check.startswith("action:"):
            return self._check_action(rule_id, check.split(":", 1)[1], rule)
        metric_name = CHECK_ALIASES.get(check, check)
        if metric_name not in {"memory", "disk", "battery", "cpu_temp", "internet"}:
            return RuleOutcome(rule_id, False, reason=f"неизвестная проверка {check}")
        result = metrics.collect(metric_name)
        fired, reason = self._evaluate(rule, result)
        if not fired:
            return RuleOutcome(rule_id, False, reason=reason)
        text = self._format(rule, result)
        return RuleOutcome(rule_id, True, text=text, reason=reason)

    def _check_action(self, rule_id: str, action_id: str, rule: dict[str, Any]) -> RuleOutcome:
        """Проверка командой перенесённого каталога (только помеченные autonomy_safe)."""
        if self.assistant is None:
            return RuleOutcome(rule_id, False, reason="нет доступа к навыкам")
        found = self.assistant.registry.find_action(action_id)
        if found is None:
            return RuleOutcome(rule_id, False, reason=f"действие {action_id} не найдено")
        skill, action = found
        if not action.autonomy_safe:
            return RuleOutcome(rule_id, False, reason=f"{action_id} не разрешено автономно")
        from ..platform import get_platform

        from ..providers import substitute

        result = get_platform().run(substitute(action.command or ""), timeout=float(action.timeout))
        text = result.output.strip()
        if not text:
            return RuleOutcome(rule_id, False, reason="проверка ничего не вернула")
        return RuleOutcome(rule_id, True, text=text[:400], reason=f"код {result.returncode}")

    def _evaluate(self, rule: dict[str, Any], result: metrics.Metrics) -> tuple[bool, str]:
        if "if_above" in rule:
            value = result.get(str(rule.get("metric", "temperature")))
            if value is None:
                return False, "нет данных"
            return value > float(rule["if_above"]), f"{value} > {rule['if_above']}"
        if "if_below_percent" in rule:
            value = result.get(str(rule.get("metric", "free_percent")))
            if value is None:
                return False, "нет данных"
            if rule.get("only_if_on_battery") and result.get("on_battery") != 1.0:
                return False, "не от батареи"
            return value < float(rule["if_below_percent"]), f"{value}% < {rule['if_below_percent']}%"
        if "if_above_percent" in rule:
            value = result.get(str(rule.get("metric", "used_percent")))
            if value is None:
                return False, "нет данных"
            return value > float(rule["if_above_percent"]), f"{value}% > {rule['if_above_percent']}%"
        if rule.get("if_not_empty"):
            return bool(result.text.strip()), "непустой вывод"
        if "if_equals" in rule:
            return result.text.strip() == str(rule["if_equals"]).strip(), "текст совпал"
        if "if_below" in rule:
            value = result.get(str(rule.get("metric", "charge_percent")))
            if value is None:
                return False, "нет данных"
            if rule.get("only_if_on_battery") and result.get("on_battery") != 1.0:
                return False, "не от батареи"
            return value < float(rule["if_below"]), f"{value} < {rule['if_below']}"
        return False, "в правиле нет условия"

    def _format(self, rule: dict[str, Any], result: metrics.Metrics) -> str:
        template = str(rule.get("message") or rule.get("prompt") or "{text}")
        try:
            return template.format(text=result.text, **result.values)
        except (KeyError, IndexError, ValueError):
            return result.text

    # -------------------------------------------------------------------- tick
    def tick(self, *, force: bool = False) -> list[RuleOutcome]:
        """Одна проверка всех правил. Возвращает сработавшие."""
        if not bool(self.config.get("autonomy.enabled", True)):
            return []
        if self._in_quiet_hours() and not force:
            return []
        rules = self.config.get("autonomy.rules", []) or []
        if not isinstance(rules, list):
            log.warning("autonomy.rules должен быть списком правил")
            return []
        outcomes: list[RuleOutcome] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            rule_id = str(rule.get("id", "rule"))
            if not force and not self._due(rule):
                continue
            try:
                outcome = self.check_rule(rule)
            except Exception as exc:  # noqa: BLE001 - правило не должно ломать демон
                log.exception("правило %s упало", rule_id)
                if self.journal:
                    self.journal.error("autonomy", f"{rule_id}: {exc!r}")
                continue
            if not outcome.fired:
                continue
            if not force and self._last_text.get(rule_id) == outcome.text and rule.get("if_changed", True):
                continue
            self._last_run[rule_id] = time.monotonic()
            self._last_text[rule_id] = outcome.text
            if self.journal:
                self.journal.info("autonomy", f"{rule_id}: {outcome.text}")
            self.providers.notify("Jarvis", outcome.text)
            if self.providers.voice_enabled:
                self.providers.speak(outcome.text)
            outcomes.append(outcome)
        return outcomes
