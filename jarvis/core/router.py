"""Маршрутизация: какой навык отвечает за фразу.

Порядок (быстрое и предсказуемое — раньше медленного):

1. точное совпадение фразы с действием навыка — без сети, мгновенно;
2. LLM-планировщик — свободная речь, несколько просьб, вопросы;
3. нечёткое локальное совпадение — последний шанс, если LLM недоступен.

Если ни один шаг не дал ответа — честно сообщаем, что не поняли.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .errors import JarvisError
from .i18n import get_translator
from .logging_setup import get_logger
from .matcher import find_exact, find_fuzzy, plan_fuzzy_multi
from .planner import PlannedAction, build_messages, parse_actions
from .types import Intent

log = get_logger("core.router")

CONFIRM_WORDS = {"да", "ага", "угу", "давай", "подтверждаю", "выполняй", "точно", "конечно", "ок", "окей",
                 "yes", "y", "ok", "sure"}
DENY_WORDS = {"нет", "не", "неа", "отмена", "отмени", "стоп", "не надо", "не нужно", "no", "n", "cancel"}


def is_confirmation(text: str) -> bool:
    """Считаем согласием только явное «да», а не «да нет» или «не надо»."""
    from .matcher import normalize_text

    words = normalize_text(text).split()
    if not words:
        return False
    joined = " ".join(words)
    if any(phrase in joined for phrase in ("да нет", "не надо", "не нужно", "давай не")):
        return False
    return any(word in CONFIRM_WORDS for word in words)


def is_denial(text: str) -> bool:
    from .matcher import normalize_text

    words = set(normalize_text(text).split())
    return bool(words & DENY_WORDS)


@dataclass
class RouteResult:
    intents: list[Intent] = field(default_factory=list)
    planned: list[PlannedAction] = field(default_factory=list)
    handled_by: str = "none"
    llm_error: str = ""
    elapsed_ms: float = 0.0

    @property
    def empty(self) -> bool:
        return not self.intents and not self.planned


class Router:
    """Выбирает действия по тексту."""

    def __init__(self, registry, providers, config, *, journal=None):
        self.registry = registry
        self.providers = providers
        self.config = config
        self.journal = journal

    def route(self, text: str, *, platform: str, allow_llm: bool = True,
              history_tail: list[dict] | None = None, pending: dict | None = None) -> RouteResult:
        started = time.monotonic()
        skills = self.registry.enabled(include_hidden=False)

        exact = find_exact(text, skills, platform,
                           max_extra_words=int(self.config.get("assistant.max_extra_words", 6)))
        if exact:
            return RouteResult(intents=exact, handled_by="exact",
                               elapsed_ms=(time.monotonic() - started) * 1000)

        llm_error = ""
        if allow_llm:
            planned = self._llm_route(text, skills, history_tail or [], pending)
            if planned is not None:
                if planned:
                    intents = self._planned_to_intents(planned)
                    if intents or any(item.kind in {"speak", "ask", "search"} for item in planned):
                        return RouteResult(intents=intents, planned=planned, handled_by="llm",
                                           elapsed_ms=(time.monotonic() - started) * 1000)
                else:
                    llm_error = "модель не предложила ничего подходящего"
            else:
                llm_error = "LLM недоступен"

        fuzzy = find_fuzzy(text, skills, platform,
                           threshold=float(self.config.get("assistant.match_threshold", 0.72)),
                           ambiguity_margin=float(self.config.get("assistant.ambiguity_margin", 0.06)))
        if fuzzy is not None:
            return RouteResult(intents=[fuzzy], handled_by="fuzzy", llm_error=llm_error,
                               elapsed_ms=(time.monotonic() - started) * 1000)

        multi = plan_fuzzy_multi(text, skills, platform,
                                 threshold=float(self.config.get("assistant.match_threshold", 0.72)),
                                 ambiguity_margin=float(self.config.get("assistant.ambiguity_margin", 0.06)))
        if multi:
            return RouteResult(intents=multi, handled_by="fuzzy", llm_error=llm_error,
                               elapsed_ms=(time.monotonic() - started) * 1000)

        return RouteResult(handled_by="none", llm_error=llm_error,
                           elapsed_ms=(time.monotonic() - started) * 1000)

    # ------------------------------------------------------------------ llm
    def _llm_route(self, text: str, skills, history_tail: list[dict],
                   pending: dict | None) -> list[PlannedAction] | None:
        """Список действий от модели или None, если LLM недоступен."""
        llm = self.providers.llm
        ready, reason = llm.available()
        if not ready:
            log.debug("LLM недоступен: %s", reason)
            return None
        from datetime import datetime

        from .i18n import get_translator

        messages = build_messages(
            skills, text,
            history_tail=history_tail,
            pending=pending,
            now_str=datetime.now().strftime("%Y-%m-%d %H:%M"),
            assistant_name=str(self.config.get("assistant.name", "Jarvis")),
            language=str(self.config.get("assistant.language", "ru")),
            extra_rules=str(self.config.get("llm.extra_rules", "")),
        )
        try:
            raw = llm.chat(messages)
        except JarvisError as exc:
            log.warning("LLM не ответил: %s", exc)
            if self.journal:
                self.journal.warning("core.router", f"LLM недоступен: {exc}")
            return None
        valid = self.registry.flat_action_ids()
        try:
            return parse_actions(raw, valid)
        except JarvisError as exc:
            log.warning("ответ модели не разобран: %s", exc)
            if self.journal:
                self.journal.warning("core.router", f"модель ответила не по схеме: {exc}")
            return []

    def _planned_to_intents(self, planned: list[PlannedAction]) -> list[Intent]:
        intents: list[Intent] = []
        for item in planned:
            if item.kind != "run" or not item.action_id:
                continue
            found = self.registry.find_action(item.action_id)
            if not found:
                log.warning("действие %s не найдено (модель выдумала id?)", item.action_id)
                continue
            skill, action = found
            if item.confirmation and not action.confirm:
                action = type(action)(**{**action.__dict__, "confirm": True})
            intents.append(Intent(action=action, skill=skill, score=1.0, source="llm"))
        return intents

    def translator(self, language: str):
        return get_translator(language)
