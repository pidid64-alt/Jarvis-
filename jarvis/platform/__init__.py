"""Выбор платформенной реализации (Linux или Windows)."""

from __future__ import annotations

import os
from functools import lru_cache

from .base import CommandResult, Platform, decode_output

__all__ = ["CommandResult", "Platform", "decode_output", "get_platform", "platform_name", "reset_platform"]


@lru_cache(maxsize=1)
def get_platform() -> Platform:
    """Возвращает реализацию платформы для текущей ОС."""
    if os.name == "nt":
        from .windows import WindowsPlatform

        return WindowsPlatform()  # type: ignore[return-value]
    from .linux import LinuxPlatform

    return LinuxPlatform()  # type: ignore[return-value]


def platform_name() -> str:
    return get_platform().name


def reset_platform() -> None:
    """Только для тестов."""
    get_platform.cache_clear()
