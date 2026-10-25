"""Точка сборки: соединяет ядро, MCP, коннекторы, память в работающий ассистент."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import (
    AssistantConfig,
    Settings,
    get_settings,
    load_assistant_config,
    load_connectors_config,
)
from jarvis.connectors.gmail_imap import GmailImapConnector
from jarvis.connectors.google_oauth import GoogleOAuthConnector
from jarvis.connectors.telegram_user import TelegramUserConnector
from jarvis.core.agent import Agent
from jarvis.core.audit import AuditLog
from jarvis.core.llm.base import LLMProvider
from jarvis.core.tools.base import Confirmer
from jarvis.core.tools.builtin import apps as app_tools
from jarvis.core.tools.builtin import documents as document_tools
from jarvis.core.tools.builtin import gmail as gmail_tools
from jarvis.core.tools.builtin import google as google_tools
from jarvis.core.tools.builtin import memory as memory_tools
from jarvis.core.tools.builtin import reminders as reminder_tools
from jarvis.core.tools.builtin import style as style_tools
from jarvis.core.tools.builtin import telegram as telegram_tools
from jarvis.core.tools.builtin import web as web_tools
from jarvis.core.tools.policy import Policy
from jarvis.core.tools.registry import ToolRegistry
from jarvis.mcp.manager import MCPManager
from jarvis.memory.db import Database
from jarvis.memory.store import MemoryStore
from jarvis.memory.vector import EmbeddingEngine
from jarvis.scheduler.notify import create_notifier
from jarvis.scheduler.runner import Scheduler
from jarvis.web.browser import BrowserReader
from jarvis.web.fetcher import WebFetcher
from jarvis.web.sites import generic
from jarvis.web.sites.base import SiteRegistry

log = logging.getLogger("jarvis.runtime")


@dataclass
class Runtime:
    settings: Settings
    config: AssistantConfig
    db: Database
    audit: AuditLog
    memory: MemoryStore
    registry: ToolRegistry
    fetcher: WebFetcher
    browser: BrowserReader
    sites: SiteRegistry
    llm: LLMProvider | None
    mcp: MCPManager | None
    telegram: TelegramUserConnector | None
    gmail: GmailImapConnector | None
    google: GoogleOAuthConnector | None
    agent: Agent | None
    scheduler: Scheduler | None


async def build_runtime(
    *,
    with_llm: bool = True,
    with_mcp: bool = True,
    with_telegram: bool = True,
    with_scheduler: bool = True,
    confirmer: Confirmer | None = None,
) -> Runtime:
    settings = get_settings()
    acfg = load_assistant_config(Path(settings.config_dir) / "assistant.yaml")
    ccfg = load_connectors_config(Path(settings.config_dir) / "connectors.yaml")

    db = Database(Path(settings.data_dir) / "jarvis.db")
    await db.connect()

    audit = AuditLog(db)
    engine = EmbeddingEngine(acfg.memory.vectors_enabled)
    memory = MemoryStore(db, engine)

    policy = Policy(acfg.approvals)
    registry = ToolRegistry(policy, audit)

    fetcher = WebFetcher(db, acfg.web)
    browser = BrowserReader(fetcher)
    sites = SiteRegistry()
    sites.register(generic.GenericSiteParser(fetcher=fetcher))

    for tool in memory_tools.create_tools(memory):
        registry.register(tool)
    for tool in web_tools.create_tools(sites, acfg.web, browser):
        registry.register(tool)
    for tool in document_tools.create_tools(settings):
        registry.register(tool)
    for tool in app_tools.create_tools():
        registry.register(tool)
    for tool in reminder_tools.create_tools(memory):
        registry.register(tool)

    # Telegram: чтение всегда; отправка — только если коннектор не в read_only.
    telegram: TelegramUserConnector | None = None
    if with_telegram:
        connector = TelegramUserConnector(settings, ccfg.telegram)
        if await connector.start():
            telegram = connector
            allow_send = ccfg.telegram.mode != "read_only"
            for tool in telegram_tools.create_tools(connector, allow_send=allow_send):
                registry.register(tool)
        else:
            log.warning("Telegram недоступен: %s", connector.problem)

    # Google (OAuth): Gmail API + Docs + Sheets + Drive — как интеграции у Claude.
    google: GoogleOAuthConnector | None = None
    google = GoogleOAuthConnector(
        credentials_path=Path("credentials.json"),
        token_path=Path(settings.data_dir) / "google_token.json",
    )
    gmail_service = None
    if google.authorized:
        gmail_service = google  # Gmail через официальный API
        for tool in google_tools.create_tools(google):
            registry.register(tool)
    else:
        log.info("Google OAuth не активен: %s", google.problem_text())

    # Gmail по IMAP — фолбек, если OAuth не настроен.
    gmail: GmailImapConnector | None = None
    if gmail_service is None and with_telegram and ccfg.gmail.enabled:
        gmail_connector = GmailImapConnector(
            settings.gmail_user, settings.gmail_app_password, enabled=ccfg.gmail.enabled
        )
        if await gmail_connector.start():
            gmail = gmail_connector
            gmail_service = gmail_connector
        else:
            log.warning("Gmail недоступен: %s", gmail_connector.problem)
    if gmail_service is not None:
        # Отправка писем: только OAuth-бэкенд и режим, отличный от read_only.
        allow_gmail_send = gmail_service is google and ccfg.gmail.mode != "read_only"
        for tool in gmail_tools.create_tools(gmail_service, allow_send=allow_gmail_send):
            registry.register(tool)

    # LLM (может отсутствовать для служебных команд: tools/mcp/fetch).
    llm: LLMProvider | None = None
    if with_llm:
        from jarvis.core.llm.factory import create_provider

        llm = create_provider(settings)

    mcp: MCPManager | None = None
    if with_mcp:
        mcp = MCPManager(Path(settings.config_dir) / "mcp_servers.yaml", registry)
        await mcp.start_all()

    agent = Agent(llm, registry, memory, acfg.agent, confirmer=confirmer) if llm else None

    # Инструменты стиля/черновиков требуют llm — регистрируем после его создания.
    if llm is not None:
        for tool in style_tools.create_tools(memory, llm):
            registry.register(tool)

    # Планировщик: напоминания, брифинги, мониторинг (уведомления — в бота владельцу).
    scheduler: Scheduler | None = None
    if with_scheduler:
        notifier = create_notifier(settings, Path(settings.data_dir))
        scheduler = Scheduler(
            memory,
            notifier,
            fetcher=fetcher,
            sites=sites,
            llm=llm,
            telegram=telegram,
            config=acfg.scheduler,
            monitoring_path=Path(settings.config_dir) / "monitoring.yaml",
        )

    return Runtime(
        settings=settings,
        config=acfg,
        db=db,
        audit=audit,
        memory=memory,
        registry=registry,
        fetcher=fetcher,
        browser=browser,
        sites=sites,
        llm=llm,
        mcp=mcp,
        telegram=telegram,
        gmail=gmail,
        google=google,
        agent=agent,
        scheduler=scheduler,
    )


async def shutdown_runtime(rt: Runtime) -> None:
    if rt.scheduler is not None:
        await rt.scheduler.stop()
    if rt.mcp is not None:
        await rt.mcp.stop_all()
    if rt.telegram is not None:
        await rt.telegram.stop()
    await rt.browser.close()
    await rt.fetcher.close()
    if rt.llm is not None:
        await rt.llm.close()
    await rt.db.close()
    log.info("Ассистент остановлен")
