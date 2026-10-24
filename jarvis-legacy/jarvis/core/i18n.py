"""Тексты интерфейса и ответов на разных языках.

Русский — основной. Добавить язык = положить ``xx.json`` рядом с ``ru.json``
(в пакете) или в ``<каталог настроек>/i18n/xx.json`` — и он появится в настройках.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import paths

DEFAULT_LANGUAGE = "ru"


class Translator:
    """Ищет текст по ключу, умеет подставлять значения и откатываться на ru."""

    def __init__(self, language: str = DEFAULT_LANGUAGE, extra_dirs: list[Path] | None = None):
        self.language = language or DEFAULT_LANGUAGE
        self._dirs = [paths.PROJECT_ROOT / "jarvis" / "config" / "i18n"]
        if extra_dirs:
            self._dirs.extend(Path(folder) for folder in extra_dirs)
        user_dir = paths.config_dir() / "i18n"
        if user_dir not in self._dirs:
            self._dirs.append(user_dir)
        self._cache: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------ files
    def available_languages(self) -> list[str]:
        found: set[str] = set()
        for folder in self._dirs:
            if not folder.is_dir():
                continue
            found.update(item.stem for item in folder.glob("*.json"))
        return sorted(found) or [DEFAULT_LANGUAGE]

    def _load(self, language: str) -> dict[str, str]:
        if language in self._cache:
            return self._cache[language]
        data: dict[str, str] = {}
        for folder in self._dirs:  # пользовательские файлы перекрывают встроенные
            path = folder / f"{language}.json"
            if not path.is_file():
                continue
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(loaded, dict):
                data.update({str(key): str(value) for key, value in loaded.items()})
        self._cache[language] = data
        return data

    def reload(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------- text
    def t(self, key: str, **values: Any) -> str:
        for language in (self.language, DEFAULT_LANGUAGE):
            text = self._load(language).get(key)
            if text:
                try:
                    return text.format(**values)
                except (KeyError, IndexError):
                    return text
        return key

    def __call__(self, key: str, **values: Any) -> str:
        """Короткая форма: ``translator("gui.title")`` — то же, что ``.t(...)``."""
        return self.t(key, **values)

    def set_language(self, language: str) -> None:
        self.language = language or DEFAULT_LANGUAGE


_default: Translator | None = None


def get_translator(language: str | None = None) -> Translator:
    global _default
    if _default is None:
        _default = Translator(language or DEFAULT_LANGUAGE)
    elif language:
        _default.set_language(language)
    return _default


def t(key: str, **values: Any) -> str:
    return get_translator().t(key, **values)
