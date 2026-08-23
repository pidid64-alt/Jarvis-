#!/bin/bash
# Контрольный прогон LLM-маршрута Jarvis против живого API.
# Проверяет 5 категорий фраз из спеки и замеряет задержку.
# Использование: scripts/test_llm.sh  (код 0 = успех)
set -u
cd "$(dirname "$0")/.."
ENV_FILE="$HOME/.config/jarvis/env"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
exec ./venv/bin/python3 - <<'PY'
import json, logging, os, statistics, sys, time

logging.disable(logging.WARNING)  # шум llm_client в консоль не нужен
from llm_client import LLMClient, LLMError
from llm_parser import parse_intent

cfg = json.load(open("config.json"))["llm"]
commands = json.load(open("commands.json"))["commands"]
known_ids = {c.get("id") for c in commands}

api_key = os.environ.get(cfg.get("api_key_env", ""), "")
if not api_key:
    print(f"FAIL: env {cfg.get('api_key_env')} не задан (файл {os.path.expanduser('~/.config/jarvis/env')})")
    sys.exit(1)

client = LLMClient(
    base_url=cfg["base_url"], api_key=api_key, model=cfg["model"],
    timeout=cfg.get("timeout_seconds", 8), max_retries=cfg.get("max_retries", 1),
)

def is_command_with_known_id(a):
    return a.get("action") == "command" and a.get("id") in known_ids

def is_speak(a):
    return a.get("action") == "speak" and bool(a.get("text"))

CASES = [
    ("какая погода",       lambda a: is_command_with_known_id(a)),
    ("а включи ютуб",      lambda a: is_command_with_known_id(a)),
    ("как дела?",          lambda a: is_speak(a) or is_command_with_known_id(a)),
    ("выключи компьютер",  lambda a: a.get("action") == "command"
                              and a.get("needs_confirmation") is True),
    ("завари кофе",        lambda a: a.get("action") in ("speak", "ask")),
]

latencies, failed = [], []
for phrase, check in CASES:
    t0 = time.monotonic()
    try:
        action = parse_intent(client, phrase, commands)
        dt = time.monotonic() - t0
        ok = bool(check(action))
        latencies.append(dt)
        detail = f"{action.get('action')}:{action.get('id') or (action.get('text') or '')[:40]!r}"
    except LLMError as e:
        dt = time.monotonic() - t0
        ok, detail = False, f"LLMError: {e}"
    status = "ok  " if ok else "FAIL"
    print(f"[{status}] {dt:5.2f}s  {phrase!r} -> {detail}")
    if not ok:
        failed.append(phrase)

if latencies:
    med = statistics.median(latencies)
    print(f"\nМедиана задержки: {med:.2f}s (цель <=3s)")
    if med > 3.0:
        print("FAIL: задержка выше цели — стоп, доложить пользователю")
        failed.append("<latency>")

print(f"\nИтог: {len(CASES)-len(failed)}/{len(CASES)} пройдено")
sys.exit(1 if failed else 0)
PY
