"""Конфигурация: секреты из .env (pydantic-settings) + YAML-файлы настроек.

Секреты — только через переменные окружения/.env, никогда не в коде и не в YAML.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("jarvis.config")


class Settings(BaseSettings):
    """Секреты и окружение (префикс JARVIS_, файл .env в корне проекта)."""

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    llm_provider: str = "openai_compat"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: float = 120.0

    telegram_api_id: int | None = None
    telegram_api_hash: str = ""
    telegram_phone: str = ""

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def _empty_int_to_none(cls, value):
        """Пустая строка в .env (ключ ещё не заполнен) — это «нет значения», а не ошибка."""
        if value in ("", None):
            return None
        return value

    data_dir: Path = Path("data")
    config_dir: Path = Path("config")


# ---------- assistant.yaml ----------

class AgentConfig(BaseModel):
    max_steps: int = 12
    temperature: float = 0.3
    max_tokens: int = 2048
    recent_messages: int = 12
    recall_top_k: int = 5


class WebConfig(BaseModel):
    user_agent: str = "Mozilla/5.0 (compatible; JarvisAssistant/0.1; +personal)"
    respect_robots: bool = True
    rate_limit_seconds: float = 1.5
    cache_ttl_seconds: int = 86400
    max_content_chars: int = 12000
    request_timeout: float = 20.0


class MemoryConfig(BaseModel):
    vectors_enabled: bool = True


class AutoRule(BaseModel):
    """Разрешение на исходящее действие без подтверждения."""

    tool: str
    chat_id: str | None = None


class ApprovalsConfig(BaseModel):
    auto: list[AutoRule] = Field(default_factory=list)


class AssistantConfig(BaseModel):
    agent: AgentConfig = AgentConfig()
    web: WebConfig = WebConfig()
    memory: MemoryConfig = MemoryConfig()
    approvals: ApprovalsConfig = ApprovalsConfig()


# ---------- connectors.yaml ----------

class TelegramConnectorConfig(BaseModel):
    enabled: bool = True
    session_file: str = "data/sessions/telegram.session"
    mode: str = "read_only"  # read_only | draft | auto (этап 2)
    allowed_chats: list[str] = Field(default_factory=list)


class ConnectorsConfig(BaseModel):
    telegram: TelegramConnectorConfig = TelegramConnectorConfig()


# ---------- mcp_servers.yaml ----------

class MCPServerConfig(BaseModel):
    name: str
    transport: str = "stdio"  # stdio | http | sse
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    allowed_tools: list[str] | None = None
    read_only: bool = False
    # Точечные переопределения класса действия инструментов.
    tool_side_effects: dict[str, str] = Field(default_factory=dict)


class MCPConfig(BaseModel):
    servers: list[MCPServerConfig] = Field(default_factory=list)


# ---------- загрузчики ----------

@lru_cache
def get_settings() -> Settings:
    return Settings()


def _load_yaml_model(path: Path, model_cls: type, default: BaseModel) -> BaseModel:
    """Читает YAML; при отсутствии/ошибке возвращает значения по умолчанию."""
    if not path.exists():
        log.warning("Конфиг %s не найден — используются значения по умолчанию", path)
        return default
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return model_cls.model_validate(data)
    except Exception:
        log.exception("Не удалось прочитать %s — используются значения по умолчанию", path)
        return default


def load_assistant_config(path: Path) -> AssistantConfig:
    cfg = _load_yaml_model(path, AssistantConfig, AssistantConfig())
    return cfg  # type: ignore[return-value]


def load_connectors_config(path: Path) -> ConnectorsConfig:
    cfg = _load_yaml_model(path, ConnectorsConfig, ConnectorsConfig())
    return cfg  # type: ignore[return-value]


def load_mcp_config(path: Path) -> MCPConfig:
    cfg = _load_yaml_model(path, MCPConfig, MCPConfig())
    return cfg  # type: ignore[return-value]
