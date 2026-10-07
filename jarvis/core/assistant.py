"""Ассистент: связывает всё вместе.

Единственная точка входа для любого интерфейса:

    reply = assistant.handle_text("какая погода")
    reply = assistant.handle_voice()          # запись → распознавание → ответ

Интерфейсы (CLI, API, окно, трей) логики не содержат — только вызовы.
"""

from __future__ import annotations

from typing import Any, Callable

from ..platform import get_platform
from . import paths
from .context import SkillContext
from .errors import JarvisError, ProviderError
from .events import EventBus
from .executor import Executor
from .history import ConversationState, DialogLog
from .i18n import get_translator
from .journal import Journal
from .logging_setup import get_logger, setup_logging
from .permissions import PermissionPolicy
from .planner import PlannedAction
from .registry import SkillRegistry
from .router import Router
from .types import Reply, State

log = get_logger("core.assistant")


class Assistant:
    """Ядро ассистента без привязки к интерфейсу."""

    def __init__(self, config: Any, *, providers: Any = None, journal: Journal | None = None,
                 events: EventBus | None = None, dialog: DialogLog | None = None,
                 conversation: ConversationState | None = None, policy: PermissionPolicy | None = None):
        self.config = config
        self.journal = journal or Journal()
        self.events = events or EventBus()
        self.dialog = dialog or DialogLog()
        self.conversation = conversation or ConversationState()
        self.policy = policy or PermissionPolicy(str(config.get("permissions.mode", "restricted")))

        if providers is None:
            from ..providers import Providers

            providers = Providers(config, journal=self.journal)
        self.providers = providers

        self.registry = SkillRegistry(self.policy, disabled=config.get("skills.disabled", []),
                                      journal=self.journal)
        self.registry.scan()
        self.router = Router(self.registry, self.providers, config, journal=self.journal)
        self.executor = Executor(config, self.policy, self.registry, self.journal)
        self._state = State.IDLE
        self._language = str(config.get("assistant.language", "ru"))

    # ------------------------------------------------------------------ setup
    @property
    def language(self) -> str:
        return self._language

    def t(self, key: str, **values: Any) -> str:
        return get_translator(self._language).t(key, **values)

    def refresh(self, *, reload_config: bool = False) -> None:
        """Перечитывает навыки (и, при желании, настройки)."""
        if reload_config:
            from .config import Config

            self.config = Config.load()
            self.policy = PermissionPolicy(str(self.config.get("permissions.mode", "restricted")))
            self.policy.mode = str(self.config.get("permissions.mode", "restricted"))
            self._language = str(self.config.get("assistant.language", self._language))
            from ..providers import Providers

            self.providers = Providers(self.config, journal=self.journal)
            self.router.providers = self.providers
            self.router.config = self.config
        self.registry.scan()
        self.events.publish("skills_changed", count=len(self.registry.all()))

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> State:
        return self._state

    def _set_state(self, state: State, **payload: Any) -> None:
        self._state = state
        self.events.publish("state", state=state.value, **payload)

    # ------------------------------------------------------------------- main
    def handle_text(self, text: str, *, source: str = "cli",
                    confirm_callback: Callable[[str], bool] | None = None,
                    say_callback: Callable[[str], None] | None = None,
                    speak: bool | None = None, allow_llm: bool | None = None) -> Reply:
        """Обрабатывает одну реплику пользователя."""
        text = (text or "").strip()
        if not text:
            return Reply(text=self.t("reply.empty_request"), ok=False, error="empty", state=State.ERROR)

        if speak is None:
            speak = source in {"voice", "daemon"} and self.providers.voice_enabled

        self.journal.info("core", f"[{source}] {text}")
        self.dialog.append("user", text, source=source)
        self.conversation.push_turn("user", text)
        self._set_state(State.THINKING, text=text)

        try:
            reply = self._process(text, source=source, confirm_callback=confirm_callback,
                                  say_callback=say_callback, allow_llm=allow_llm)
        except JarvisError as exc:
            self.journal.error("core", f"ошибка обработки: {exc}")
            reply = Reply(text=get_translator(self._language).t(exc.user_message_key), ok=False,
                          error=str(exc), state=State.ERROR)
        except Exception as exc:  # noqa: BLE001 - ядро не падает от одного сбоя
            log.exception("неожиданная ошибка обработки запроса")
            self.journal.error("core", f"внутренняя ошибка: {exc!r}")
            reply = Reply(text=self.t("error.generic"), ok=False, error=repr(exc), state=State.ERROR)

        if reply.text or reply.speech:
            self.dialog.append("jarvis", reply.text or reply.speech or "", skill=reply.skill,
                               action=reply.action, ok=reply.ok)
            self.conversation.push_turn("assistant", reply.text or reply.speech or "")
        if speak and not reply.await_confirmation:
            self._set_state(State.SPEAKING, text=reply.text)
            self.providers.speak(reply.spoken_text(), language=self._language)

        final_state = State.ERROR if not reply.ok and reply.error else State.IDLE
        self._set_state(final_state, text=reply.text)
        self.events.publish("reply", **reply.to_dict())
        return reply

    # ---------------------------------------------------------------- internals
    def _process(self, text: str, *, source: str, confirm_callback, say_callback,
                 allow_llm: bool | None) -> Reply:
        pending = self.conversation.consume_pending()
        if allow_llm is None:
            allow_llm = bool(self.config.get("llm.enabled", True))
        route = self.router.route(
            text,
            platform=get_platform().name,
            allow_llm=allow_llm,
            history_tail=self.conversation.history_tail(),
            pending=pending,
        )
        if route.elapsed_ms:
            log.debug("маршрут %s за %.0f мс", route.handled_by, route.elapsed_ms)

        if route.empty:
            self.journal.info("core", f"не понял: {text}")
            return Reply(text=self.t("reply.not_understood"), ok=False, error="not_understood",
                         state=State.IDLE)

        replies: list[Reply] = []
        for intent in route.intents:
            ctx = self._make_context(source=source, confirm_callback=confirm_callback,
                                     say_callback=say_callback, skill_id=intent.skill.id)
            replies.append(self.executor.execute(intent, ctx))

        for planned in route.planned:
            if planned.kind in {"speak", "ask"}:
                reply = Reply(text=planned.text, continue_dialog=planned.kind == "ask")
                if planned.kind == "ask":
                    self.conversation.set_pending(text, planned.text)
                replies.append(reply)
            elif planned.kind == "search":
                replies.append(self._search(planned, source=source, confirm_callback=confirm_callback,
                                            say_callback=say_callback))

        if not replies:
            return Reply(text=self.t("reply.not_understood"), ok=False, error="not_understood")

        combined = self._combine(replies)
        return combined

    def _make_context(self, *, source: str, confirm_callback, say_callback, skill_id: str) -> SkillContext:
        return SkillContext(
            config=self.config,
            permissions=self._skill_permissions(skill_id),
            providers=self.providers,
            registry=self.registry,
            journal=self.journal,
            language=self._language,
            source=source,
            confirm_callback=confirm_callback,
            say_callback=say_callback,
            extra={"skill_id": skill_id},
        )

    def _skill_permissions(self, skill_id: str):
        skill = self.registry.get(skill_id)
        return skill.permissions if skill else self.policy

    def _combine(self, replies: list[Reply]) -> Reply:
        if len(replies) == 1:
            return replies[0]
        texts = [reply.text for reply in replies if reply.text]
        combined = Reply(
            text=" ".join(texts),
            ok=all(reply.ok for reply in replies),
            error=next((reply.error for reply in replies if reply.error), None),
            state=State.IDLE,
            continue_dialog=any(reply.continue_dialog for reply in replies),
        )
        combined.skill = replies[-1].skill
        combined.action = replies[-1].action
        combined.data["parts"] = [reply.to_dict() for reply in replies]
        return combined

    # ------------------------------------------------------------------ search
    def _search(self, planned: PlannedAction, *, source: str, confirm_callback, say_callback) -> Reply:
        platform = get_platform()
        if planned.open_browser:
            import urllib.parse

            url = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(planned.query)
            if platform.open_url(url):
                return Reply(text=self.t("search.opened", query=planned.query))
            return Reply(text=self.t("search.browser_failed"), ok=False, error="browser")

        try:
            snippets = self.providers.search.search(planned.query)
        except ProviderError as exc:
            self.journal.warning("core.search", f"{planned.query}: {exc}")
            return Reply(text=self.t("search.unavailable"), ok=False, error=str(exc), state=State.ERROR)

        answer = ""
        llm = self.providers.llm
        ready, _ = llm.available()
        if ready:
            context = "\n".join(f"- {item['title']}: {item['body']}" for item in snippets[:5])[:4000]
            try:
                answer = llm.chat([
                    {"role": "system",
                     "content": self.t("search.summarize_prompt")},
                    {"role": "user", "content": f"{self.t('search.query_label')}: {planned.query}\n\n{context}"},
                ], max_tokens=250, temperature=0.3).strip()[:500]
            except JarvisError:
                answer = ""
        if not answer:
            titles = [item["title"] for item in snippets[:3] if item.get("title")]
            answer = self.t("search.fallback", results=". ".join(titles)) if titles \
                else self.t("search.nothing_found")
        return Reply(text=answer, data={"snippets": snippets[:3]})

    # ------------------------------------------------------------------- voice
    def handle_voice(self, *, source: str = "voice", confirm_callback=None,
                     say_callback=None) -> Reply:
        """Записывает фразу, распознаёт и обрабатывает как обычный текст."""
        self._set_state(State.LISTENING)
        try:
            wav_path = self.providers.record()
        except JarvisError as exc:
            return Reply(text=get_translator(self._language).t(exc.user_message_key), ok=False,
                         error=str(exc), state=State.ERROR)
        if wav_path is None:
            self._set_state(State.IDLE)
            return Reply(text=self.t("voice.no_speech"), ok=False, error="no_speech")
        try:
            text = self.providers.transcribe(wav_path)
        except ProviderError as exc:
            self.journal.error("core.voice", f"распознавание недоступно: {exc}")
            return Reply(text=self.t("voice.stt_failed"), ok=False, error=str(exc), state=State.ERROR)
        if not text:
            return Reply(text=self.t("voice.not_recognized"), ok=False, error="empty_stt")
        return self.handle_text(text, source=source, confirm_callback=confirm_callback,
                                say_callback=say_callback, speak=True)

    # ------------------------------------------------------------------- skills
    def skills_public(self) -> list[dict[str, Any]]:
        return [skill.to_public_dict() for skill in self.registry.all()]

    def set_skill_enabled(self, skill_id: str, enabled: bool, *, persist: bool = True) -> dict[str, Any]:
        skill = self.registry.set_enabled(skill_id, enabled)
        if persist:
            self._persist_disabled()
        self.journal.info("core", f"навык {skill_id}: {'включён' if enabled else 'выключен'}")
        self.events.publish("skills_changed", skill=skill_id, enabled=enabled)
        return skill.to_public_dict()

    def _persist_disabled(self) -> None:
        """Пишет список выключенных навыков в config.toml, сохраняя комментарии."""
        from . import toml_edit

        path = paths.config_path()
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        updated = toml_edit.set_value(text, ("skills", "disabled"), self.registry.disabled_ids)
        if updated != text:
            path.write_text(updated, encoding="utf-8")

    # -------------------------------------------------------------------- data
    def status(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "language": self._language,
            "permissions_mode": self.policy.mode,
            "skills": {
                "total": len(self.registry.all()),
                "enabled": len(self.registry.enabled()),
                "problems": list(self.registry.problems),
            },
            "providers": self.providers.state(),
            "config_path": str(paths.config_path()),
            "state_dir": str(paths.state_dir()),
            "voice_enabled": self.providers.voice_enabled,
        }

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.dialog.read(limit=limit)

    def logs(self, limit: int = 200, level: str | None = None, search: str = "") -> list[dict[str, Any]]:
        return self.journal.read(limit=limit, level=level, search=search)

    def report(self, limit: int = 300) -> str:
        return self.journal.report(limit=limit, extra_lines=[
            f"Навыков: {len(self.registry.all())} (включено {len(self.registry.enabled())})",
            f"Состояние: {self._state.value}, режим прав: {self.policy.mode}",
        ])

    def shutdown(self) -> None:
        """Останавливает серверы, поднятые по требованию."""
        self.providers.stop()


def create_assistant(config: Any = None, *, debug: bool = False, **kwargs: Any) -> Assistant:
    """Удобная сборка: логи, секреты, конфиг, ассистент."""
    from . import secrets
    from .config import Config

    setup_logging(debug=debug)
    secrets.load_env_file()
    if config is None:
        config = Config.load()
    return Assistant(config, **kwargs)
