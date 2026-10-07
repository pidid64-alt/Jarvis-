# Подключение LLM Jarvis к OpenRouter — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Включить существующий LLM-маршрут Jarvis: свободная речь → OpenRouter (`stealth/ox-alpha`) → выбор команды из whitelist / голосовой ответ, с fallback на старый match_command.

**Architecture:** Меняется только конфигурация и доставка ключа; код `jarvis.py`/`llm_parser.py`/`llm_client.py` не трогается. Ключ живёт в `~/.config/jarvis/env` вне репозитория и попадает в сервисы через `EnvironmentFile`.

**Tech Stack:** Python 3 (venv проекта), systemd user units, bash, OpenRouter API (OpenAI-совместимый `/v1/chat/completions`).

## Global Constraints

- Модель строго `stealth/ox-alpha`; endpoint строго `https://openrouter.ai/api/v1`
- Имя переменной с ключом: `OPENROUTER_API_KEY`; файл ключа: `$HOME/.config/jarvis/env`, права 600
- Ключ НИКОГДА не печатается в логи/вывод/коммиты и не попадает в git (сейчас он есть только в `~/.claude/settings.json`)
- `fallback_to_match: true` сохраняется; юниты используют `EnvironmentFile=-…` (со знаком минус — отсутствие файла не роняет сервис)
- Запрещено менять код `jarvis.py`, `llm_parser.py`, `llm_client.py` в этом плане
- Целевая задержка: медиана по контрольным фразам ≤3с (при превышении — стоп и отчёт пользователю)
- Python-скрипты запускаются через `./venv/bin/python3` из корня репо `/home/ironcarrier/jarvis`

---

### Task 1: Файл ключей вне репозитория

**Files:**
- Create: `$HOME/.config/jarvis/env` (вне git)

**Interfaces:**
- Produces: переменная окружения `OPENROUTER_API_KEY`, читаемая Task 3 (test_llm.sh) и Task 4 (systemd units)

- [ ] **Step 1: Создать каталог и скопировать ключ из settings.json**

```bash
mkdir -p "$HOME/.config/jarvis"
KEY="$(jq -r '.env.ANTHROPIC_API_KEY' "$HOME/.claude/settings.json")"
case "$KEY" in
  sk-or-v1-*) : ;;
  *) echo "ОШИБКА: ключ не похож на OpenRouter (sk-or-v1-…)"; exit 1 ;;
esac
umask 177
printf '# Ключи Jarvis — вне репозитория, не коммитить\nOPENROUTER_API_KEY=%s\n' "$KEY" \
  > "$HOME/.config/jarvis/env"
unset KEY
chmod 600 "$HOME/.config/jarvis/env"
```

- [ ] **Step 2: Проверить файл, не печатая значение**

Run: `grep -c '^OPENROUTER_API_KEY=sk-or-v1-' "$HOME/.config/jarvis/env" && stat -c '%a' "$HOME/.config/jarvis/env"`
Expected: `1` и `600`. Если выводится что-то длиннее — значение ключа утекло в вывод, пересоздать файл.

---

### Task 2: Тестовый стенд контрольных фраз (ожидаемо падает до переключения)

**Files:**
- Create: `scripts/test_llm.sh`

**Interfaces:**
- Consumes: `config.json` → блок `llm` (base_url, api_key_env, model), env из Task 1
- Produces: команда `./scripts/test_llm.sh`; код возврата 0 = все проверки пройдены; печатает таблицу фраз и медианную задержку

- [ ] **Step 1: Написать скрипт**

```bash
#!/bin/bash
# Контрольный прогон LLM-маршрута Jarvis против живого API.
# Проверяет 5 категорий фраз из спеки и замеряет задержку.
# Использование: scripts/test_llm.sh  (код 0 = успех)
set -u
cd "$(dirname "$0")/.."
ENV_FILE="$HOME/.config/jarvis/env"
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
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
    ("как дела?",          lambda a: is_speak(a)),
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
```

- [ ] **Step 2: Сделать исполняемым и запустить ДО переключения конфига**

Run: `chmod +x scripts/test_llm.sh && ./scripts/test_llm.sh`
Expected: FAIL — либо «env OMNIROUTE_API_KEY не задан», либо LLMError connection refused к localhost:20128. Это подтверждает, что стенд честно ловит нерабочее состояние.

- [ ] **Step 3: Commit**

```bash
git add scripts/test_llm.sh
git commit -m "feat: тестовый стенд контрольных фраз для LLM-маршрута"
```

---

### Task 3: Переключение config.json на OpenRouter

**Files:**
- Modify: `config.json` (блок `"llm"`, строки 14–25)

**Interfaces:**
- Consumes: env `OPENROUTER_API_KEY` (Task 1)
- Produces: конфиг, который читают `jarvis.py` и `autonomy.py`; вход для повторного прогона `scripts/test_llm.sh`

- [ ] **Step 1: Заменить блок llm в config.json на точный финальный вид**

```json
  "llm": {
    "_comment": "LLM-парсер через OpenRouter. Ключ в ~/.config/jarvis/env (OPENROUTER_API_KEY). Если env не задан — fallback на старый match_command.",
    "enabled": true,
    "base_url": "https://openrouter.ai/api/v1",
    "api_key_env": "OPENROUTER_API_KEY",
    "model": "stealth/ox-alpha",
    "max_tokens": 300,
    "timeout_seconds": 8,
    "max_retries": 1,
    "fallback_to_match": true,
    "conversation_window_seconds": 30
  }
```

(заодно уходит опечатка с ведущим пробелом в base_url)

- [ ] **Step 2: Прогнать тестовый стенд**

Run: `./scripts/test_llm.sh`
Expected: все 5 строк `ok`, медиана ≤3с, `Итог: 5/5 пройдено`.
Если модель `stealth/ox-alpha` недоступна через chat/completions (HTTP 404/400) — запросить `GET https://openrouter.ai/api/v1/models` и согласовать с пользователем замену; дальше по спеке не идти.

- [ ] **Step 3: Commit**

```bash
git add config.json
git commit -m "feat: LLM-парсер переведён на OpenRouter (stealth/ox-alpha)"
```

---

### Task 4: Юниты systemd — ключ через EnvironmentFile

**Files:**
- Modify: `systemd/jarvis.service` ([Service]-секция)
- Modify: `systemd/jarvis-autonomy.service` ([Service]-секция)

**Interfaces:**
- Consumes: `$HOME/.config/jarvis/env` (Task 1)
- Produces: работающие сервисы `jarvis.service`, `jarvis-autonomy.service` с env `OPENROUTER_API_KEY`; ожившая autonomy (уведомления в inbox.json)

- [ ] **Step 1: Привести обе секции [Service] к виду (без хардкода ключа)**

`systemd/jarvis.service`:
```ini
[Service]
Type=simple
ExecStart=%h/jarvis/venv/bin/python3 %h/jarvis/jarvis.py
Restart=on-failure
RestartSec=3
EnvironmentFile=-%h/.config/jarvis/env
```

`systemd/jarvis-autonomy.service`:
```ini
[Service]
Type=simple
ExecStart=%h/jarvis/venv/bin/python3 %h/jarvis/autonomy.py
Restart=on-failure
RestartSec=10
EnvironmentFile=-%h/.config/jarvis/env
```

Минус перед путём обязателен: без файла ключа сервис всё равно стартует (fallback на match_command), а не падает.

- [ ] **Step 2: Задеплоить юниты и перезапустить сервисы**

```bash
cp systemd/jarvis.service systemd/jarvis-autonomy.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user restart jarvis.service jarvis-autonomy.service
sleep 2
systemctl --user is-active jarvis.service jarvis-autonomy.service
```
Expected: `active` / `active`.

- [ ] **Step 3: Убедиться, что ключ дошёл до процессов (без печати значения)**

```bash
for svc in jarvis jarvis-autonomy; do
  PID=$(systemctl --user show -p MainPID --value "$svc.service")
  printf '%s: %s\n' "$svc" "$(tr '\0' '\n' < "/proc/$PID/environ" | grep -c '^OPENROUTER_API_KEY=')"
done
```
Expected: `jarvis: 1` и `jarvis-autonomy: 1` (0 означал бы, что env не подхвачен).

- [ ] **Step 4: Убедиться, что autonomy перестал сыпать connection refused**

```bash
sleep 70; tail -n 30 "$HOME/.local/share/jarvis/autonomy.log" | grep -c "Connection refused"
```
Expected: `0`. Дополнительно допустимо появление `autonomy: … → notify:` — правила начали работать.

- [ ] **Step 5: Commit**

```bash
git add systemd/jarvis.service systemd/jarvis-autonomy.service
git commit -m "feat: ключ LLM передаётся юнитам через EnvironmentFile, хардкод удалён"
```

---

### Task 5: Учения по fallback (ключ пропал — Jarvis выживает)

**Files:** нет изменений — только проверка поведения, предусмотренного спекой.

**Interfaces:**
- Consumes: `fallback_to_match: true` в config.json, `EnvironmentFile=-` из Task 4

- [ ] **Step 1: Спрятать файл ключа и перезапустить jarvis**

```bash
mv "$HOME/.config/jarvis/env" "$HOME/.config/jarvis/env.bak"
systemctl --user restart jarvis.service && sleep 2
systemctl --user is-active jarvis.service
PID=$(systemctl --user show -p MainPID --value jarvis.service)
tr '\0' '\n' < "/proc/$PID/environ" | grep -c '^OPENROUTER_API_KEY=' || true
```
Expected: `active`, счётчик `0` — сервис стартовал без ключа и не упал.

- [ ] **Step 2: Вернуть ключ и перезапустить**

```bash
mv "$HOME/.config/jarvis/env.bak" "$HOME/.config/jarvis/env"
systemctl --user restart jarvis.service && sleep 2
systemctl --user is-active jarvis.service
```
Expected: `active`. Повторная проверка environ (Step 3 из Task 4) должна дать `jarvis: 1`.

---

### Task 6: Обновить CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (разделы «Project Overview» — абзац про принцип безопасности остаётся; раздел «Resources»; добавить подраздел про LLM-слой после «Matcher Logic»)

**Interfaces:** только документация.

- [ ] **Step 1: Добавить подраздел после секции «Matcher Logic»**

```markdown
## LLM Route (свободная речь, 2026-08)

Перед match_command текст идёт в LLM-парсер (`try_llm_route` → `parse_intent`,
OpenRouter `https://openrouter.ai/api/v1`, модель `stealth/ox-alpha`).
LLM возвращает строгий JSON `{command|speak|ask}`:

- видит ТОЛЬКО id/tags/descriptions команд — поле `command` (shell) в промпт не попадает;
- id проверяется по whitelist (`_validate_action`), dangerous-командам форсируется подтверждение;
- любая ошибка (нет сети/ключа, битый JSON) → молча fallback на `match_command`;
- контекст диалога 30с: `conversation_state.py` (STATE_DIR/conversation_state.json).

Ключ: env `OPENROUTER_API_KEY` из `~/.config/jarvis/env`
(`EnvironmentFile=-` в юнитах; отсутствие файла = работа без LLM).
Контрольный прогон: `scripts/test_llm.sh` (5 фраз + замер задержки, цель ≤3с).
Автономка `autonomy.py` использует тот же конфиг: LLM решает skip/notify,
результат пишется в STATE_DIR/inbox.json и озвучивается демоном раз в ~120с.
```

- [ ] **Step 2: Поправить устаревшую строку в разделе «Resources on i3-10110U»**

Заменить буллет:
```markdown
- No Ollama/LLM required for voice commands — separate from NOXY
```
на:
```markdown
- LLM-парсер внешний (OpenRouter, cloud): локальную память не занимает;
  при недоступности сети/ключа голосовые команды работают через whitelist-matcher
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md описывает LLM-маршрут OpenRouter и автономку"
```

---

### Task 7: Живой e2e (пользователь у микрофона)

**Files:** нет изменений.

- [ ] **Step 1: Попросить пользователя сказать 3 фразы** (после Super+J или «Джарвис»):
  1. «какая погода» → должен выполнить команду и озвучить результат;
  2. свободная формулировка, например «а открой spotify» → команда;
  3. «как дела?» → живой короткий ответ голосом (не «Такой команды не знаю»).
- [ ] **Step 2: Проверить след в логе**

```bash
tail -n 15 "$HOME/.local/share/jarvis/jarvis.log" | grep -E "LLM action|Услышал"
```
Expected: строки `LLM action: {...}` — маршрут пошёл через LLM, а не только matcher.

---

## Самопроверка плана

- Спека → задачи: env-файл (T1), config (T3), юниты (T4), test_llm.sh (T2), CLAUDE.md (T6), замер задержки (T2/T3), fallback-дрилл (T5), e2e (T7), autonomy-проверка (T4 Step 4) — покрыто всё.
- Заглушек нет; каждый шаг имеет конкретную команду/код и ожидаемый результат.
- Имена согласованы: `OPENROUTER_API_KEY` везде одинаково; `~/.config/jarvis/env` совпадает в T1/T2/T4/T5.
