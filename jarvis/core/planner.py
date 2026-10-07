"""LLM-планировщик: превращает свободную речь в список действий.

Безопасность: модель НИКОГДА не получает возможности писать команды. В промпт
уходят только ``id`` действий, их описания и фразы; команды (shell) не уходят.
Модель может выбрать существующее действие, попросить уточнение, ответить
текстом или запустить поиск в интернете.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .errors import ProviderError
from .i18n import get_translator
from .logging_setup import get_logger
from .types import Skill

log = get_logger("core.planner")

MAX_ACTIONS = 4
MAX_SPEAK_CHARS = 500
MAX_SEARCH_CHARS = 300

SYSTEM_PROMPT = """Ты — {name}, локальный ассистент пользователя. Отвечай по-русски, коротко и по делу.
Пользователь говорит голосом, распознавание может ошибаться: важна суть, а не точные слова.

Верни СТРОГО JSON-объект вида {{"actions": [...]}} — от 1 до {max_actions} действий:

1. Выполнить действие из списка доступных:
   {{"action": "run", "id": "<id из списка>", "confirmation": false}}
2. Ответить словами (если это вопрос или беседа):
   {{"action": "speak", "text": "короткий ответ до {max_speak} символов"}}
3. Уточнить, если непонятно:
   {{"action": "ask", "text": "короткий вопрос"}}
4. Поиск в интернете:
   {{"action": "search", "query": "2-5 слов", "open": false}}

Правила:
- Никогда не выдумывай id: только из списка ниже.
- Никогда не возвращай команды, пути или код — только JSON по схеме.
- Если просьб несколько — верни несколько действий в порядке произнесения.
- Для действий, помеченных confirm=true, ставь "confirmation": true.
- Если данных не хватает — используй "ask", не угадывай.
{extra_rules}
# Доступные действия
{actions}

# Контекст диалога
{context}
"""


@dataclass
class PlannedAction:
    kind: str  # run | speak | ask | search
    action_id: str | None = None
    text: str = ""
    query: str = ""
    open_browser: bool = False
    confirmation: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def build_actions_block(skills: Iterable[Skill]) -> str:
    lines: list[str] = []
    for skill in skills:
        if not skill.enabled:
            continue
        for action in skill.actions:
            description = action.description or ", ".join(action.phrases[:3])
            confirm = "true" if (action.confirm or action.is_dangerous) else "false"
            lines.append(f'- id="{skill.id}.{action.id}" confirm={confirm}: {description}')
    return "\n".join(lines) if lines else "(нет доступных действий)"


def build_context_block(history_tail: list[dict[str, Any]], pending: dict[str, Any] | None,
                        now_str: str) -> str:
    lines = [f"Сейчас: {now_str}"]
    if pending:
        lines.append(f"Jarvis только что спросил: {pending.get('question', '')} (ждёт ответа)")
    for turn in history_tail[-6:]:
        role = "Пользователь" if turn.get("role") == "user" else "Jarvis"
        lines.append(f"{role}: {turn.get('text', '')}")
    return "\n".join(lines)


def build_messages(skills: list[Skill], text: str, *, history_tail: list[dict[str, Any]],
                   pending: dict[str, Any] | None, now_str: str, assistant_name: str = "Jarvis",
                   language: str = "ru", extra_rules: str = "") -> list[dict[str, str]]:
    translator = get_translator(language)
    system = SYSTEM_PROMPT.format(
        name=assistant_name,
        max_actions=MAX_ACTIONS,
        max_speak=MAX_SPEAK_CHARS,
        actions=build_actions_block(skills),
        context=build_context_block(history_tail, pending, now_str),
        extra_rules=(extra_rules.strip() + "\n") if extra_rules else "",
    )
    if language.startswith("en"):
        system = "Answer in English.\n" + system
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
        {"role": "user", "content": translator.t("llm.json_only")},
    ]


def _extract_json(raw: str) -> Any:
    raw = (raw or "").strip()
    if not raw:
        raise ProviderError("модель вернула пустой ответ")
    try:
        return json.loads(raw)
    except ValueError:
        pass
    # Модель часто оборачивает JSON в ```json ... ``` или добавляет текст вокруг
    match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", raw, re.DOTALL)
    if not match:
        match = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
    if not match:
        raise ProviderError("в ответе модели нет JSON")
    try:
        return json.loads(match.group(1))
    except ValueError as exc:
        raise ProviderError("не удалось разобрать JSON от модели") from exc


def parse_actions(raw: str, valid_ids: Iterable[str]) -> list[PlannedAction]:
    """Разбирает ответ модели и отбрасывает всё недопустимое."""
    data = _extract_json(raw)
    if isinstance(data, dict) and "actions" in data:
        items = data["actions"]
    elif isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        items = data
    else:
        raise ProviderError("неожиданный формат ответа модели")
    if not isinstance(items, list):
        raise ProviderError("actions должен быть списком")

    known = set(valid_ids)
    result: list[PlannedAction] = []
    for item in items[:MAX_ACTIONS]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("action", "")).strip().lower()
        if kind in {"command", "run"}:
            action_id = str(item.get("id", "")).strip()
            if action_id not in known:
                log.warning("модель предложила неизвестное действие %r — пропуск", action_id)
                continue
            result.append(PlannedAction(
                kind="run",
                action_id=action_id,
                confirmation=bool(item.get("confirmation") or item.get("needs_confirmation")),
            ))
        elif kind in {"speak", "ask"}:
            text = str(item.get("text", "")).strip()[:MAX_SPEAK_CHARS]
            if not text:
                continue
            result.append(PlannedAction(kind=kind, text=text))
        elif kind == "search":
            query = str(item.get("query", "")).strip()[:MAX_SEARCH_CHARS]
            if not query:
                continue
            result.append(PlannedAction(kind="search", query=query, open_browser=bool(item.get("open"))))
        else:
            log.warning("модель вернула неизвестное действие %r — пропуск", kind)
    if len(items) > MAX_ACTIONS:
        log.warning("модель вернула %d действий, лишние отброшены", len(items))
    return result
