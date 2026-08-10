#!/usr/bin/env python3
"""
llm_parser.py
-------------
Превращает распознанный голосовой текст в структурированное действие через
локальный OmniRoute (OpenAI-совместимый API).

Действие строго одного из трёх типов:
  {"action": "command", "id": "<id команды>", "needs_confirmation": bool}
  {"action": "speak",   "text": "<что сказать голосом>"}
  {"action": "ask",     "text": "<уточняющий вопрос>"}

Никаких shell-команд, путей, имён файлов от LLM — только id из белого
списка команд или короткий текст для TTS. Это фундаментальное правило
безопасности: LLM не имеет выхода в shell, она — parser, не agent.

Все ошибки (сеть, таймаут, битый JSON, неожиданный формат) превращаются
в LLMError — jarvis.py ловит и падает на старый match_command как
fallback. Пользователь ничего не замечает.
"""

import json
import logging
import re
from typing import Any

from llm_client import LLMClient, LLMError

# Максимальная длина текста, который LLM может попросить озвучить. Piper
# нормально говорит до 500 символов, длинное — обрезаем с многоточием.
MAX_SPEAK_CHARS = 500

# Жёсткая схема ответа, объясняемая LLM в системном промпте.
ACTION_SCHEMA = """{
  "action": "command" | "speak" | "ask",
  ...
}

# command — выполнить заготовку из белого списка:
{"action": "command", "id": "<id команды>", "needs_confirmation": true|false}

# speak — просто ответить голосом (не выполняя никаких действий):
{"action": "speak", "text": "<короткий ответ, до 500 символов>"}

# ask — уточняющий вопрос для продолжения диалога:
{"action": "ask", "text": "<вопрос>"}
"""


SYSTEM_PROMPT_TEMPLATE = """Ты — Jarvis, локальный голосовой ассистент для Linux.
Распознанный голос пользователя уже прошёл через STT — он может содержать
опечатки, шумовые артефакты и неформальный стиль. Твоя задача: понять
НАМЕРЕНИЕ и вернуть строго JSON с действием.

# Правила

1. Никогда не возвращай shell-команды, пути, имена файлов, аргументы или
   любые другие исполняемые конструкции. Ты — parser, не agent.

2. Доступные действия (whitelist):
{action_schema}

3. Если намерение пользователя совпадает с одной из доступных команд —
   верни action=command с её id.

4. Если намерение — общий вопрос, диалог, благодарность, болтовня (не
   требует выполнения команды) — верни action=speak с коротким ответом
   на русском, в стиле Джарвиса (деловой, короткий, до 500 символов).

5. Если намерение неясно или неоднозначно — верни action=ask с
   уточняющим вопросом (например, "Какие именно обновления — системные
   или AUR?").

6. Для команд с тегом "dangerous" (poweroff, reboot, logout, system_update)
   всегда ставь needs_confirmation=true.

# Доступные команды

{commands_block}

# Текущая тема разговора

{context_block}
"""


def build_commands_block(commands: list[dict]) -> str:
    """Собирает описание команд для системного промпта.

    Безопасность: в промпт идут только id, description и tags. Поля
    `command` (shell) сюда НЕ попадают — LLM их никогда не видит и не
    может процитировать обратно.
    """
    lines = []
    for c in commands:
        cid = c.get("id", "")
        desc = c.get("description") or _auto_description(c)
        tags = ",".join(c.get("tags") or [])
        needs_confirm = "true" if c.get("confirm") or "dangerous" in (c.get("tags") or []) else "false"
        lines.append(f'- id="{cid}" tags=[{tags}] confirm={needs_confirm}: {desc}')
    return "\n".join(lines) if lines else "(нет зарегистрированных команд)"


def _auto_description(cmd: dict) -> str:
    """Если у команды нет description — собираем из phrases + id."""
    phrases = cmd.get("phrases") or []
    if phrases:
        return f"Вызывается фразами: {' / '.join(phrases[:3])}."
    return f"Команда {cmd.get('id', '?')}."


def build_context_block(history_tail: list[dict], pending: dict | None,
                        now_str: str) -> str:
    """Краткое описание текущего контекста диалога для LLM."""
    parts = [f"Текущее время: {now_str}."]
    if history_tail:
        parts.append("Последние реплики:")
        for h in history_tail[-4:]:
            role = "USER" if h.get("role") == "user" else "JARVIS"
            text = (h.get("text") or "").strip().replace("\n", " ")
            if len(text) > 120:
                text = text[:117] + "..."
            parts.append(f"  [{role}] {text}")
    if pending:
        parts.append(f"Ожидается уточнение от пользователя по теме: {pending.get('topic', '?')}")
    return "\n".join(parts)


def parse_intent(client: LLMClient, user_text: str,
                 commands: list[dict],
                 history_tail: list[dict] | None = None,
                 pending: dict | None = None,
                 now_str: str = "") -> dict[str, Any]:
    """Возвращает dict с полями action, плюс id/text/needs_confirmation.

    Бросает LLMError при любой ошибке — jarvis.py ловит и падает на fallback.
    """
    history_tail = history_tail or []
    sys_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        action_schema=ACTION_SCHEMA,
        commands_block=build_commands_block(commands),
        context_block=build_context_block(history_tail, pending, now_str),
    )

    messages = [{"role": "system", "content": sys_prompt}]
    # history_tail — короткая сводка, она уже включена в системный промпт
    # через build_context_block, дублировать её в messages не нужно.
    messages.append({"role": "user", "content": user_text.strip()})

    raw = client.chat(messages, max_tokens=300, temperature=0.2)
    action = _extract_json(raw)

    return _validate_action(action, commands)


# ---------- внутренняя механика ----------

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_FIRST_OBJ = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict:
    """LLM может вернуть JSON несколькими способами:
      - чистый JSON-объект
      - обёрнутый в ```json ... ```
      - с пояснительным текстом вокруг (например: "Вот ответ: {...}")
    Ищем самый первый валидный JSON-объект.
    """
    if not raw or not raw.strip():
        raise LLMError("LLM вернул пустой ответ")

    # 1) пробуем как есть
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2) пробуем вытащить из ```json ... ``` блока
    m = _JSON_FENCE.search(raw)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3) первый {...} в тексте
    m = _JSON_FIRST_OBJ.search(raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise LLMError(f"не удалось распарсить JSON: {e}") from e

    raise LLMError(f"JSON не найден в ответе LLM: {raw[:200]}")


def _validate_action(action: dict, commands: list[dict]) -> dict:
    """Проверяет форму и домен (id ∈ whitelist). Возвращает очищенный dict."""
    if not isinstance(action, dict):
        raise LLMError(f"ответ не dict: {type(action).__name__}")

    act = action.get("action")
    if act not in ("command", "speak", "ask"):
        raise LLMError(f"неизвестное action: {act!r}")

    if act == "command":
        cid = action.get("id")
        if not isinstance(cid, str) or not cid:
            raise LLMError("command: пустой id")
        # whitelist-проверка — id должен быть в commands.json
        if not any(c.get("id") == cid for c in commands):
            raise LLMError(f"command: id {cid!r} не в whitelist")
        # needs_confirmation — bool, по умолчанию False
        nc = action.get("needs_confirmation")
        if not isinstance(nc, bool):
            nc = False
        # Защита: если команда с тегом dangerous или confirm=true — форсим True
        cmd_obj = next((c for c in commands if c.get("id") == cid), {})
        if cmd_obj.get("confirm") or "dangerous" in (cmd_obj.get("tags") or []):
            nc = True
        return {"action": "command", "id": cid, "needs_confirmation": nc}

    # speak / ask — нужен text, обрезаем до MAX_SPEAK_CHARS
    text = action.get("text")
    if not isinstance(text, str):
        raise LLMError(f"{act}: text не строка")
    text = text.strip()
    if not text:
        raise LLMError(f"{act}: пустой text")
    if len(text) > MAX_SPEAK_CHARS:
        text = text[:MAX_SPEAK_CHARS - 1].rstrip() + "…"
    return {"action": act, "text": text}


# ---------- утилита для автономки (фаза 3) ----------

def parse_notification_decision(client: LLMClient, rule_prompt: str,
                                check_result: str) -> dict:
    """Отдельная функция для автономки: LLM решает, стоит ли сообщать.

    Возвращает {"action": "skip"} или {"action": "notify", "text": "..."}.
    """
    sys_prompt = (
        "Ты — модуль принятия решения для фонового ассистента Jarvis.\n"
        "Тебе дают: правило проверки, и результат проверки.\n"
        "Ответь строго одним JSON:\n"
        '  {"action": "skip"} — если сообщать не стоит (норма, шум)\n'
        '  {"action": "notify", "text": "короткое сообщение для пользователя"} — '
        "если стоит сообщить\n"
        "Сообщение — на русском, до 300 символов, по делу, без воды."
    )
    user_prompt = f"Правило: {rule_prompt}\nРезультат: {check_result}"
    raw = client.chat(
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": user_prompt}],
        max_tokens=200, temperature=0.1
    )
    action = _extract_json(raw)
    if action.get("action") not in ("skip", "notify"):
        raise LLMError(f"неожиданный action от автономки: {action}")
    if action["action"] == "notify":
        text = action.get("text", "").strip()
        if not text:
            raise LLMError("notify без text")
        if len(text) > 300:
            text = text[:299] + "…"
        action["text"] = text
    return action