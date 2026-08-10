#!/usr/bin/env python3
"""
autonomy.py
-----------
Фоновый процесс, который по расписанию (autonomy.json) опрашивает
read-only скрипты и через LLM решает, стоит ли оповестить пользователя.
Если да — пишет запись в STATE_DIR/inbox.json, откуда её заберёт
jarvis.py на idle-цикле.

Безопасность: autonomy.py НЕ выполняет произвольные shell-команды.
Имена check — это id команд из commands.json, и для каждого id он
достаёт статическую shell-команду whitelist. Никаких инъекций.

Использование:
  autonomy.py            демон (тик раз в минуту)
  autonomy.py --once     прогнать все правила один раз и выйти
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = Path.home() / ".local" / "share" / "jarvis"
LOG_FILE = STATE_DIR / "autonomy.log"
INBOX_FILE = STATE_DIR / "inbox.json"
AUTONOMY_CONFIG = BASE_DIR / "autonomy.json"
MAIN_CONFIG = BASE_DIR / "config.json"
COMMANDS_FILE = BASE_DIR / "commands.json"

# Глобальный кэш: id команды -> shell-команда. Заполняется при старте.
_COMMAND_LOOKUP: dict[str, str] = {}


def load_command_lookup() -> dict[str, str]:
    """Возвращает {id: command} только для команд с speak_output или
    чисто read-only скриптов. autonomy.py использует это для выполнения
    решений check_* — никогда не для произвольных действий."""
    if _COMMAND_LOOKUP:
        return _COMMAND_LOOKUP
    try:
        with open(COMMANDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        logging.exception("autonomy: не удалось прочитать commands.json")
        return {}

    for cmd in data.get("commands", []):
        cid = cmd.get("id", "")
        shell = cmd.get("command", "")
        if not cid or not shell or shell == "true":
            continue
        # ВАЖНО: autonomy.py выполняет только эти whitelist shell-команды.
        # Никаких пользовательских вставок сюда не попадает.
        _COMMAND_LOOKUP[cid] = shell
    return _COMMAND_LOOKUP


def expand_vars(text: str) -> str:
    """Только $HOME и $USER — остальные переменные оставляем для shell."""
    return text.replace("$HOME", str(Path.home())).replace("$USER", os.environ.get("USER", ""))


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


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
    try:
        result = subprocess.run(
            shell, shell=True, capture_output=True, text=True, timeout=timeout
        )
        out = (result.stdout or result.stderr or "").strip()
        return out[:2000]  # ограничиваем, чтоб LLM не получил портянку
    except subprocess.TimeoutExpired:
        logging.warning("autonomy: %s превысил таймаут %ds", check_id, timeout)
        return ""
    except Exception:
        logging.exception("autonomy: ошибка выполнения %s", check_id)
        return ""


def should_filter_locally(rule: dict, raw_output: str) -> bool:
    """Простые локальные фильтры до LLM — экономим обращения."""
    if "if_above" in rule:
        try:
            # вытаскиваем первое число из вывода
            num = float("".join(c for c in raw_output if c.isdigit() or c == ".").strip(".") or "0")
            if num <= rule["if_above"]:
                return True
        except (ValueError, TypeError):
            pass
    if "if_below_percent" in rule:
        try:
            num = float("".join(c for c in raw_output if c.isdigit() or c == ".").strip(".") or "100")
            if num >= rule["if_below_percent"]:
                return True
        except (ValueError, TypeError):
            pass
    if "if_equals" in rule:
        if rule["if_equals"] not in raw_output:
            return True
    if rule.get("if_changed"):
        # пустой результат = состояние не изменилось
        if not raw_output:
            return True
    if "has_keywords" in rule:
        if not any(kw.lower() in raw_output.lower() for kw in rule["has_keywords"]):
            return True
    return False


def decide_via_llm(rule: dict, raw_output: str, llm_client, parser) -> dict:
    """Отдаёт результат в LLM, получает решение skip/notify."""
    from llm_parser import parse_notification_decision
    return parse_notification_decision(llm_client, rule["prompt"], raw_output)


def append_inbox(text: str, source: str = "autonomy"):
    """Атомарно добавляет запись в inbox.json."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    inbox = []
    if INBOX_FILE.exists():
        try:
            inbox = json.loads(INBOX_FILE.read_text(encoding="utf-8"))
        except Exception:
            inbox = []
    inbox.append({
        "ts": time.time(),
        "source": source,
        "text": text,
    })
    # не держим больше 50 записей
    if len(inbox) > 50:
        inbox = inbox[-50:]
    tmp = INBOX_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(inbox, ensure_ascii=False), encoding="utf-8")
    tmp.replace(INBOX_FILE)


def process_rule(rule: dict, llm_client, parser, last_run: dict,
                 min_interval: float):
    """Один проход по правилу: проверить, пора ли, выполнить, решить."""
    rid = rule.get("id", "?")
    now = time.time()
    last = last_run.get(rid, 0)

    # Вычисляем, пора ли
    due = False
    if "every_minutes" in rule:
        if now - last >= rule["every_minutes"] * 60:
            due = True
    if "time" in rule:
        # раз в минуту проверка HH:MM
        if datetime.now().strftime("%H:%M") == rule["time"] and (now - last) > 60:
            due = True

    if not due:
        return

    # Ограничение частоты — между двумя policy.notify не меньше min_interval
    if (now - last_run.get("__last_notify", 0)) < min_interval:
        logging.debug("autonomy: %s — подавлено, недавно был notify", rid)
        last_run[rid] = now
        return

    last_run[rid] = now
    logging.info("autonomy: запуск правила %s", rid)

    raw = run_check(rule["check"])
    if not raw:
        logging.debug("autonomy: %s — пустой результат, skip", rid)
        return

    if should_filter_locally(rule, raw):
        logging.debug("autonomy: %s — отфильтровано локально (skip LLM)", rid)
        return

    if not llm_client:
        # LLM недоступен — не сообщаем (без подтверждения не пишем в inbox)
        logging.debug("autonomy: %s — LLM недоступен, skip notify", rid)
        return

    try:
        decision = decide_via_llm(rule, raw, llm_client, parser)
    except Exception as e:
        logging.warning("autonomy: %s — LLM decision failed: %s", rid, e)
        return

    if decision.get("action") == "notify":
        text = decision.get("text", "")
        if text:
            append_inbox(text, source=rid)
            logging.info("autonomy: %s → notify: %s", rid, text[:80])
            last_run["__last_notify"] = now


def run_loop(llm_client, parser, debug: bool):
    cfg = load_autonomy_config()
    rules = cfg.get("rules", [])
    quiet = cfg.get("quiet_hours")
    min_interval = float(cfg.get("min_notify_interval_seconds", 300))

    # Отметки последнего запуска (в памяти, не на диске — autonomy.py
    # стартует заново при рестарте сервиса, рестарт разрешён раз в минуту)
    last_run: dict = {}

    logging.info("autonomy: запущен, %d правил, quiet_hours=%s",
                 len(rules), bool(quiet))

    tick = 0
    while True:
        tick += 1
        if in_quiet_hours(quiet):
            logging.debug("autonomy: quiet hours, всё skip")
        else:
            for rule in rules:
                try:
                    process_rule(rule, llm_client, parser, last_run, min_interval)
                except Exception:
                    logging.exception("autonomy: ошибка в правиле %s",
                                      rule.get("id", "?"))
        # тик раз в 60 секунд
        if not debug and tick % 60 != 0:
            pass
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

    # LLM-клиент — опциональный. Без него autonomy.py работает, но
    # решения LLM не запрашивает (только локальные фильтры).
    llm_client = None
    parser_fn = None
    main_cfg = load_main_config()
    llm_cfg = main_cfg.get("llm", {})
    if llm_cfg.get("enabled") and llm_cfg.get("model"):
        try:
            from llm_client import LLMClient
            api_key = os.environ.get(llm_cfg.get("api_key_env", "OMNIROUTE_API_KEY"), "")
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
        cfg = load_autonomy_config()
        rules = cfg.get("rules", [])
        last_run: dict = {}
        for rule in rules:
            try:
                process_rule(rule, llm_client, parser_fn, last_run, 0)
            except Exception:
                logging.exception("autonomy: %s", rule.get("id"))
        return

    run_loop(llm_client, parser_fn, args.debug)


if __name__ == "__main__":
    main()