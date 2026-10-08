"""Пути Jarvis: настройки, состояние, данные, логи.

Linux:   ~/.config/jarvis      (настройки)   ~/.local/share/jarvis (состояние)
Windows: %APPDATA%\\Jarvis      (настройки)   %LOCALAPPDATA%\\Jarvis (состояние)

Каталог можно переопределить переменной окружения ``JARVIS_HOME`` —
удобно для тестов и для переносимой установки на флешку.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIRNAME = "jarvis"
APP_DIRNAME_WIN = "Jarvis"

#: Корень репозитория/установки (там, где лежат пакет ``jarvis`` и ``legacy``)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env_override() -> Path | None:
    override = os.environ.get("JARVIS_HOME", "").strip()
    return Path(override).expanduser() if override else None


def is_windows() -> bool:
    return os.name == "nt"


def config_dir() -> Path:
    """Каталог настроек: config.toml, .env, пользовательские навыки."""
    override = _env_override()
    if override:
        return override / "config"
    if is_windows():
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_DIRNAME_WIN
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / APP_DIRNAME


def state_dir() -> Path:
    """Каталог состояния: логи, журнал, история, pid, временные файлы."""
    override = _env_override()
    if override:
        return override / "state"
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIRNAME_WIN
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_DIRNAME


def user_skills_dir() -> Path:
    """Пользовательские навыки: сюда кладётся папка навыка — и всё работает."""
    return config_dir() / "skills"


def bundled_skills_dir() -> Path:
    """Навыки, поставляемые с Jarvis."""
    return PROJECT_ROOT / "jarvis" / "skills"


def bundled_config_path() -> Path:
    """Шаблон настроек внутри пакета."""
    return PROJECT_ROOT / "jarvis" / "config" / "config.default.toml"


def config_path() -> Path:
    """Живой файл настроек (один на всю программу)."""
    return config_dir() / "config.toml"


def mcp_config_path() -> Path:
    """Настройки серверов MCP: отдельный файл, чтобы не мешать основному."""
    return config_dir() / "mcp.toml"


def bundled_mcp_example_path() -> Path:
    """Шаблон mcp.toml внутри пакета (с подсказками)."""
    return PROJECT_ROOT / "jarvis" / "config" / "mcp.example.toml"


def env_file_path() -> Path:
    """Файл секретов. Содержимое никогда не печатается."""
    return config_dir() / ".env"


def legacy_dir() -> Path:
    """Старая версия Jarvis, перенесённая в ``legacy/``."""
    return PROJECT_ROOT / "legacy"


def legacy_state_dir() -> Path:
    """Каталог состояния старой версии (для миграции)."""
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIRNAME_WIN
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_DIRNAME


def log_file() -> Path:
    return state_dir() / "jarvis.log"


def journal_file() -> Path:
    """Журнал событий в формате JSON Lines (его показывает GUI)."""
    return state_dir() / "journal.jsonl"


def history_file() -> Path:
    return state_dir() / "history.jsonl"


def token_file() -> Path:
    """Токен локального API (0600). Нужен интерфейсам, чтобы говорить с ядром."""
    return state_dir() / "api.token"


def api_file() -> Path:
    """Файл обнаружения API (0600): адрес и порт, чтобы интерфейсы нашли ядро."""
    return state_dir() / "api.json"


def pid_file() -> Path:
    return state_dir() / "jarvis.pid"


PLACEHOLDERS = ("base", "legacy", "state", "config", "home", "python")


def substitute(text: str) -> str:
    """Раскрывает подстановки путей в строке из настроек или навыка.

    ``{legacy}/scripts/x.sh`` → ``/home/пользователь/Jarvis-/legacy/scripts/x.sh``.
    Абсолютные пути не зависят от того, где лежит репозиторий, поэтому в файлах
    хранятся именно подстановки, а не готовые пути.
    """
    if not text:
        return text or ""
    import sys

    return (
        text.replace("{base}", str(PROJECT_ROOT))
        .replace("{legacy}", str(legacy_dir()))
        .replace("{state}", str(state_dir()))
        .replace("{config}", str(config_dir()))
        .replace("{home}", str(Path.home()))
        .replace("{python}", sys.executable)
    )


def ensure_dirs() -> None:
    """Создаёт каталоги настроек и состояния, если их нет."""
    config_dir().mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)
