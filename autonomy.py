#!/usr/bin/env python3
"""
autonomy.py
-----------
Фоновый процесс, который по расписанию (autonomy.json) опрашивает
read-only проверки. Пороги оцениваются локально; LLM опционален для
пользовательских правил без явного условия.
При обнаружении проблемы пишет запись в STATE_DIR/inbox.json, откуда её заберёт
jarvis.py на idle-цикле.

Безопасность: autonomy.py НЕ выполняет произвольные shell-команды.
Имена check — это id команд из commands.json, и для каждого id он
достаёт статическую shell-команду с autonomy_safe: true. Опасные команды
и команды с подтверждением запрещены даже при наличии этого флага.

Использование:
  autonomy.py            демон (тик раз в минуту)
  autonomy.py --once     прогнать все правила один раз и выйти
"""

import argparse
import hashlib
import json
import logging
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import inbox_store
from platform_support import (commands_file_name, file_lock,
                              substitute_placeholders)

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = Path.home() / ".local" / "share" / "jarvis"
LOG_FILE = STATE_DIR / "autonomy.log"
INBOX_FILE = STATE_DIR / "inbox.json"
AUTONOMY_CONFIG = BASE_DIR / "autonomy.json"
MAIN_CONFIG = BASE_DIR / "config.json"
COMMANDS_FILE = BASE_DIR / commands_file_name()
SCHEDULE_FILE = STATE_DIR / "autonomy_state.json"

# Only explicitly audited commands may run unattended. speak_output is NOT a
# safety property: mutating commands can print output too.
def load_command_lookup() -> dict[str, str]:
    try:
        data = json.loads(COMMANDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logging.exception("autonomy: не удалось прочитать commands.json")
        return {}
    return {cmd["id"]: cmd.get("autonomy_command", cmd["command"])
            for cmd in data.get("commands", [])
            if cmd.get("autonomy_safe") is True and cmd.get("command")
            and not cmd.get("confirm") and "dangerous" not in cmd.get("tags", [])}


def load_autonomy_config() -> dict:
    if not AUTONOMY_CONFIG.exists():
        return {"rules": [], "quiet_hours": None}
    with open(AUTONOMY_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def load_main_config() -> dict:
    if not MAIN_CONFIG.exists():
        return {}
    with open(MAIN_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def in_quiet_hours(quiet: dict | None) -> bool:
    """Простейшая проверка: если current_time между start и end (с переходом
    через полночь). Если quiet_hours=None — никогда не тихо."""
    if not quiet:
        return False
    try:
        start = datetime.strptime(quiet["start"], "%H:%M").time()
        end = datetime.strptime(quiet["end"], "%H:%M").time()
    except (KeyError, ValueError):
        return False
    now = datetime.now().time()
    if start <= end:
        return start <= now <= end
    # переход через полночь: например 23:00-07:00
    return now >= start or now <= end


def run_check(check_id: str, timeout: int = 30) -> str:
    """Выполняет whitelist-скрипт команды check_id, возвращает stdout (trimmed)."""
    lookup = load_command_lookup()
    shell = lookup.get(check_id)
    if not shell:
        logging.warning("autonomy: правило ссылается на неизвестную команду %s", check_id)
        return ""
    shell = substitute_placeholders(shell, BASE_DIR, STATE_DIR)
    try:
        result = subprocess.run(
            shell, shell=True, capture_output=True, timeout=timeout
        )
        if result.returncode != 0:
            logging.warning("autonomy: %s завершился с кодом %s", check_id, result.returncode)
            return ""
        from platform_support import decode_output
        out = decode_output(result.stdout or b"").strip()
        return out[:2000]  # ограничиваем, чтоб LLM не получил портянку
    except subprocess.TimeoutExpired:
        logging.warning("autonomy: %s превысил таймаут %ds", check_id, timeout)
        return ""
    except Exception:
        logging.exception("autonomy: ошибка выполнения %s", check_id)
        return ""


def parse_check_output(raw_output: str) -> tuple[str, dict]:
    """Structured metrics are unambiguous; legacy scripts still return prose."""
    try:
        data = json.loads(raw_output)
    except ValueError:
        return raw_output.strip(), {}
    if not isinstance(data, dict) or data.get("error"):
        return "", {}
    metrics = data.get("metrics", {})
    text = data.get("text", "")
    return (text if isinstance(text, str) else "",
            metrics if isinstance(metrics, dict) else {})


def local_decision(rule: dict, raw_output: str) -> bool | None:
    """True=known alert, False=healthy/unknown metric, None=needs interpretation.

    Never join all numbers in prose (disk sizes, dates and percentages differ).
    Threshold rules require a named structured metric and fail closed if absent.
    """
    text, metrics = parse_check_output(raw_output)
    if not text:
        return False
    if rule.get("only_if_on_battery") and metrics.get("on_battery") is not True:
        return False
    for pattern in rule.get("skip_patterns", []):
        if re.search(pattern, text, re.IGNORECASE):
            return False
    tests = []
    for key, compare in (("if_above", lambda a, b: a > b),
                         ("if_above_percent", lambda a, b: a > b),
                         ("if_below_percent", lambda a, b: a < b)):
        if key not in rule:
            continue
        value = metrics.get(rule.get("metric"))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return False
        tests.append(compare(value, float(rule[key])))
    if "if_equals" in rule:
        tests.append(text.strip() == rule["if_equals"])
    if "has_keywords" in rule:
        tests.append(any(kw.casefold() in text.casefold() for kw in rule["has_keywords"]))
    if "notify_pattern" in rule:
        tests.append(bool(re.search(rule["notify_pattern"], text, re.IGNORECASE)))
    return all(tests) if tests else None


def should_filter_locally(rule: dict, raw_output: str) -> bool:
    return local_decision(rule, raw_output) is False


def decide_via_llm(rule: dict, raw_output: str, llm_client, parser=None) -> dict:
    from llm_parser import parse_notification_decision
    return parse_notification_decision(llm_client, rule.get("prompt", ""), raw_output)


def append_inbox(text: str, source: str = "autonomy"):
    inbox_store.append(INBOX_FILE, text, source)


def load_schedule() -> dict:
    try:
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("rules"), dict):
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logging.exception("autonomy: повреждено состояние расписания")
    return {"rules": {}, "last_notify": 0}


def rule_due(rule: dict, previous: dict, now: float) -> bool:
    if rule.get("enabled", True) is False:
        return False
    last = previous.get("last_run", 0)
    if "every_minutes" in rule:
        if now - last >= max(1, float(rule["every_minutes"])) * 60 or now < last:
            return True
    if "time" in rule:
        today = datetime.fromtimestamp(now)
        scheduled = datetime.strptime(rule["time"], "%H:%M").time()
        # Catch up after sleep/restart instead of requiring an exact minute.
        last_date = datetime.fromtimestamp(last).date() if last else None
        if today.time() >= scheduled and last_date != today.date():
            return True
    return False


def process_rule(rule: dict, llm_client, parser, state: dict,
                 min_interval: float = 300, force: bool = False):
    """Run every due check regardless of notification cooldown; queue its alert.

    min_interval is retained for caller compatibility; delivery throttling belongs
    in flush_pending, never before the checks (which used to starve later rules).
    """
    if rule.get("enabled", True) is False:
        return
    rid = rule["id"]
    previous = state.setdefault("rules", {}).setdefault(rid, {})
    now = time.time()
    if not force and not rule_due(rule, previous, now):
        return
    previous["last_run"] = now
    raw = run_check(rule["check"], timeout=rule.get("timeout_seconds", 30))
    text, _ = parse_check_output(raw)
    if not text:
        # An unavailable sensor must not leave an old pending warning queued.
        previous.pop("pending", None)
        return
    local = local_decision(rule, raw)
    if local is False:
        previous.pop("pending", None)
        previous.pop("notified_digest", None)  # recovery rearms the alert
        return
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    unchanged = previous.get("notified_digest") == digest
    repeat = max(0, float(rule.get("repeat_seconds", 21600)))
    if unchanged and (rule.get("if_changed") or now - previous.get("last_notified", 0) < repeat):
        previous.pop("pending", None)
        return
    # Threshold alerts have a stable cooldown even when readings fluctuate.
    if not rule.get("if_changed") and now - previous.get("last_notified", 0) < repeat:
        previous.pop("pending", None)
        return
    if local is True:
        decision = {"action": "notify", "text": text}
    elif llm_client:
        try:
            decision = decide_via_llm(rule, raw, llm_client, parser)
        except Exception:
            logging.exception("autonomy: %s — LLM недоступен", rid)
            return
    else:
        return  # no reliable local condition: do not guess without LLM
    if decision.get("action") == "notify" and decision.get("text", "").strip():
        pending = previous.get("pending", {})
        previous["pending"] = {"text": decision["text"].strip()[:1000], "digest": digest,
                               "queued_at": pending.get("queued_at", now)}
    else:
        previous.pop("pending", None)


def flush_pending(state: dict, cfg: dict) -> int:
    """Deliver the oldest pending alert, respecting global cooldown and quiet hours."""
    if in_quiet_hours(cfg.get("quiet_hours")):
        return 0
    now = time.time()
    if now - state.get("last_notify", 0) < float(cfg.get("min_notify_interval_seconds", 300)):
        return 0
    enabled = {r["id"] for r in cfg.get("rules", []) if r.get("enabled", True)}
    pending = [(v["pending"]["queued_at"], rid, v) for rid, v in state.get("rules", {}).items()
               if rid in enabled and v.get("pending")]
    if not pending:
        return 0
    _, rid, previous = min(pending, key=lambda row: (row[0], row[1]))
    item = previous["pending"]
    append_inbox(item["text"], source=rid)
    previous["notified_digest"] = item["digest"]
    previous["last_notified"] = now
    previous.pop("pending")
    state["last_notify"] = now
    logging.info("autonomy: %s → notify", rid)
    return 1


def run_tick(cfg: dict, llm_client, parser, state: dict, force=False):
    for rule in cfg.get("rules", []):
        try:
            process_rule(rule, llm_client, parser, state, force=force)
        except Exception:
            logging.exception("autonomy: ошибка в правиле %s", rule.get("id", "?"))
    try:
        flush_pending(state, cfg)
    finally:
        # Preserve completed checks and pending alerts even if the inbox is
        # temporarily unwritable or corrupt. Retry delivery on the next tick.
        inbox_store.atomic_write(SCHEDULE_FILE, state)


def scheduled_tick(llm_client, parser=None, force=False):
    """Serialize daemon and --once runs; always reload persisted state under lock."""
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with file_lock(SCHEDULE_FILE.with_suffix(".lock"), blocking=False):
            run_tick(load_autonomy_config(), llm_client, parser,
                     load_schedule(), force=force)
    except BlockingIOError:
        logging.info("autonomy: другой процесс уже выполняет проверки")


def run_loop(llm_client, parser, debug: bool):
    logging.info("autonomy: запущен; локальные проверки доступны без LLM")
    while True:
        try:
            scheduled_tick(llm_client, parser)
        except Exception:
            logging.exception("autonomy: ошибка цикла, повтор через минуту")
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="Jarvis autonomy daemon")
    parser.add_argument("--once", action="store_true",
                        help="прогнать все правила один раз и выйти")
    parser.add_argument("--debug", action="store_true",
                        help="подробные логи")
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(LOG_FILE)]
    if args.debug:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )

    # Локальный env-файл (на Windows systemd-то нет — читаем сами).
    from platform_support import load_env_file
    load_env_file()

    # LLM only interprets custom rules without deterministic local conditions.
    llm_client = None
    parser_fn = None
    main_cfg = load_main_config()
    llm_cfg = main_cfg.get("llm", {})
    api_key = os.environ.get(llm_cfg.get("api_key_env", "OPENROUTER_API_KEY"), "")
    if llm_cfg.get("enabled") and llm_cfg.get("model") and api_key:
        try:
            from llm_client import LLMClient
            llm_client = LLMClient(
                base_url=llm_cfg["base_url"],
                api_key=api_key,
                model=llm_cfg["model"],
                timeout=llm_cfg.get("timeout_seconds", 8),
                max_retries=llm_cfg.get("max_retries", 1),
            )
        except Exception:
            logging.exception("autonomy: LLM-клиент не создан, работаю без LLM")

    if args.once:
        scheduled_tick(llm_client, parser_fn, force=True)
        return

    run_loop(llm_client, parser_fn, args.debug)


if __name__ == "__main__":
    main()
