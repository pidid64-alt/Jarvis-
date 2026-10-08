"""Ошибки Jarvis: одна база, понятные пользователю тексты.

Правило проекта: любая ошибка либо превращается в понятный ответ
пользователю (``user_message_key``), либо явно помечается как внутренняя.
Молчаливых ``except: pass`` в ядре нет.
"""

from __future__ import annotations


class JarvisError(Exception):
    """Базовая ошибка. ``user_message_key`` — ключ текста для пользователя."""

    user_message_key = "error.generic"

    def __init__(self, message: str = "", *, details: str | None = None):
        super().__init__(message or self.user_message_key)
        self.details = details


class ConfigError(JarvisError):
    """Конфиг отсутствует, битый или содержит недопустимое значение."""

    user_message_key = "error.config"


class SecretMissingError(JarvisError):
    """Секрет (ключ API) не задан в окружении/.env."""

    user_message_key = "error.secret_missing"


class PermissionDeniedError(JarvisError):
    """Навыку не разрешено то, что он пытается сделать."""

    user_message_key = "error.permission_denied"


class ConfirmationDeclined(JarvisError):
    """Пользователь не подтвердил опасное действие."""

    user_message_key = "error.cancelled"


class SkillError(JarvisError):
    """Ошибка внутри навыка."""

    user_message_key = "error.skill"


class ProviderError(JarvisError):
    """Провайдер (STT/TTS/LLM/поиск) недоступен или ответил мусором."""

    user_message_key = "error.provider"


class ProviderTimeoutError(ProviderError):
    """Провайдер не уложился в таймаут."""

    user_message_key = "error.timeout"


class ProviderUnavailableError(ProviderError):
    """Провайдер отключён автоматически после серии сбоев."""

    user_message_key = "error.provider_disabled"


class CommandFailedError(JarvisError):
    """Статическая команда навыка завершилась с ошибкой."""

    user_message_key = "error.command_failed"
