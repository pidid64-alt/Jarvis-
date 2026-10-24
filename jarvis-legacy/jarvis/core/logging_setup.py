"""Логи: файл с ротацией, консоль по требованию, маскирование секретов.

Отдельно от журнала (``journal.jsonl``): лог — для разработчика, журнал —
для пользователя и GUI.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from . import paths, secrets

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class SecretMaskingFilter(logging.Filter):
    """Ни одно значение секрета не должно попасть в лог."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - защита от битых аргументов
            return True
        masked = secrets.mask_text(message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


_configured = False


def setup_logging(debug: bool = False, log_path: Path | None = None, console: bool | None = None) -> Path:
    """Настраивает корневой логгер. Идемпотентно."""
    global _configured
    target = Path(log_path) if log_path else paths.log_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("jarvis")
    if _configured:
        return target

    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.propagate = False

    file_handler = logging.handlers.RotatingFileHandler(
        target, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(FORMAT))
    file_handler.addFilter(SecretMaskingFilter())
    root.addHandler(file_handler)

    if console is None:
        console = debug
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter(FORMAT))
        stream.addFilter(SecretMaskingFilter())
        root.addHandler(stream)

    _configured = True
    return target


def get_logger(name: str) -> logging.Logger:
    """``get_logger("skill.volume")`` → логгер с маскированием секретов."""
    logger = logging.getLogger(name if name.startswith("jarvis") else f"jarvis.{name}")
    if not logging.getLogger("jarvis").handlers:
        setup_logging()
    return logger


def reset_for_tests() -> None:
    """Только для тестов: разрешает переконфигурацию логов."""
    global _configured
    _configured = False
    logger = logging.getLogger("jarvis")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
