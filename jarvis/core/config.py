"""Настройки Jarvis: один файл ``config.toml``.

Особенности:

* ключи-секреты в конфиге записываются ссылками ``${ИМЯ_ПЕРЕМЕННОЙ}`` —
  значения подставляются из окружения/``.env`` в момент чтения;
* значения по умолчанию хранятся в ``jarvis/config/config.default.toml``,
  поэтому отсутствие пользовательского файла — не ошибка;
* точечный доступ через путь: ``cfg.get("llm.model")``;
* правка сохраняет комментарии и форматирование (см. ``toml_edit``).
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any, Iterable

from . import paths
from .errors import ConfigError

#: ${VAR} или ${VAR:значение-по-умолчанию}
_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")


def resolve_refs(value: Any) -> tuple[Any, list[str]]:
    """Подставляет ``${VAR}`` из окружения. Возвращает (значение, отсутствующие).

    Отсутствующие переменные не роняют программу: на их месте пустая строка,
    а имена собираются в список, чтобы GUI/doctor могли сказать
    «задайте ключ X», не показывая самого значения.
    """
    missing: list[str] = []

    def _one(text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            value = os.environ.get(name)
            if value is None or value == "":
                if default is not None:
                    return default
                missing.append(name)
                return ""
            return value

        return _REF_RE.sub(repl, text)

    if isinstance(value, str):
        return _one(value), missing
    if isinstance(value, list):
        out = []
        for item in value:
            item_value, item_missing = resolve_refs(item)
            out.append(item_value)
            missing.extend(item_missing)
        return out, missing
    if isinstance(value, dict):
        out_dict = {}
        for key, item in value.items():
            item_value, item_missing = resolve_refs(item)
            out_dict[key] = item_value
            missing.extend(item_missing)
        return out_dict, missing
    return value, missing


def referenced_env_names(value: Any) -> list[str]:
    """Имена переменных, на которые ссылается конфиг: нужно для doctor/GUI."""
    names: list[str] = []
    if isinstance(value, str):
        names.extend(match.group(1) for match in _REF_RE.finditer(value))
    elif isinstance(value, list):
        for item in value:
            names.extend(referenced_env_names(item))
    elif isinstance(value, dict):
        for item in value.values():
            names.extend(referenced_env_names(item))
    return names


def _substitute_paths(node: Any) -> Any:
    """Раскрывает ``{state}``, ``{legacy}`` и другие подстановки в значениях."""
    if isinstance(node, dict):
        return {key: _substitute_paths(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_substitute_paths(item) for item in node]
    if isinstance(node, str):
        return paths.substitute(node)
    return node


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def ensure_config_file() -> Path:
    """Создаёт ``config.toml`` из шаблона при первом запуске."""
    path = paths.config_path()
    if path.exists():
        return path
    paths.ensure_dirs()
    template = paths.bundled_config_path()
    if not template.exists():
        raise ConfigError(f"шаблон настроек не найден: {template}")
    path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    return path


class Config:
    """Читает ``config.toml`` и отдаёт значения по точечному пути."""

    def __init__(
        self,
        data: dict[str, Any],
        path: Path,
        missing_env: Iterable[str] = (),
        raw: dict[str, Any] | None = None,
    ):
        self._data = data
        self._raw = raw if raw is not None else data
        self.path = path
        #: имена переменных, которые конфиг ждёт, но которых нет в окружении
        self.missing_env = sorted(set(missing_env))
        #: все переменные, на которые ссылается конфиг (имена, без значений)
        self.expected_env = sorted(set(referenced_env_names(self._raw)))

    # ------------------------------------------------------------------ load
    @staticmethod
    def _fix_old_defaults(merged: dict[str, Any], cfg_path: Path) -> dict[str, Any]:
        """Разовая правка старых значений по умолчанию, которые мешали работать.

        Сейчас таких одно: ожидание ответа модели. В первых настройках стояло
        8 секунд — для локальной модели на слабой машине это гарантированный
        обрыв: Jarvis бросал запрос, а сервер модели отменял задачу. Меняем
        только точное старое значение, только в существующем файле и с записью
        в журнал, чтобы это не выглядело самовольством.
        """
        from .logging_setup import get_logger

        if not cfg_path.exists():
            return merged
        llm = merged.get("llm")
        if not isinstance(llm, dict) or float(llm.get("timeout_seconds", 0) or 0) != 8.0:
            return merged
        try:
            from . import toml_edit

            text = cfg_path.read_text(encoding="utf-8")
            cfg_path.write_text(toml_edit.set_value(text, "llm.timeout_seconds", 60),
                                encoding="utf-8")
        except Exception:  # noqa: BLE001 - настройки важнее правки, но и падать нельзя
            get_logger("core.config").warning("не удалось поправить ожидание ответа модели",
                                             exc_info=True)
            llm["timeout_seconds"] = 60
            return merged
        llm["timeout_seconds"] = 60
        get_logger("core.config").info(
            "ожидание ответа модели увеличено с 8 до 60 с: локальной модели нужно больше "
            "(изменить можно в настройках)")
        return merged

    @classmethod
    def load(cls, path: Path | None = None, *, create: bool = True) -> "Config":
        cfg_path = Path(path) if path else (ensure_config_file() if create else paths.config_path())
        defaults: dict[str, Any] = {}
        template = paths.bundled_config_path()
        if template.exists():
            try:
                defaults = tomllib.loads(template.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:  # pragma: no cover - защита от порчи шаблона
                raise ConfigError(f"шаблон настроек повреждён: {exc}") from exc
        raw: dict[str, Any] = {}
        if cfg_path.exists():
            try:
                raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"{cfg_path}: {exc}") from exc
        merged = _deep_merge(defaults, raw)
        merged = cls._fix_old_defaults(merged, cfg_path)
        resolved, missing = resolve_refs(merged)
        # Пути вида {state}/... раскрываем сразу: так ни один потребитель
        # настроек не получит в руки строку с нераскрытой подстановкой.
        resolved = _substitute_paths(resolved)
        return cls(resolved, cfg_path, missing, raw=merged)

    # ------------------------------------------------------------------ read
    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @property
    def raw_data(self) -> dict[str, Any]:
        """Сырые значения файла: видно, где стоит ссылка ``${ИМЯ}``, а не значение."""
        return self._raw

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> dict[str, Any]:
        value = self._data.get(name)
        return value if isinstance(value, dict) else {}

    def raw(self, dotted: str, default: Any = None) -> Any:
        """Значение до подстановки ``${...}`` — видно, ждали ли здесь секрет."""
        node: Any = self._raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def is_empty_secret(self, dotted: str) -> bool:
        """True, если поле ссылается на секрет, но переменная окружения пуста."""
        expected = referenced_env_names(self.raw(dotted))
        return bool(expected) and not self.get(dotted)
