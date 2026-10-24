"""Секреты: чтение, запись и маскирование.

Правила проекта:

* секреты живут только в ``.env`` (права 600) или в переменных окружения;
* в ``config.toml`` — исключительно ссылки ``${ИМЯ}``;
* значение секрета не попадает ни в лог, ни в консоль, ни в GUI,
  ни в «скопировать для отчёта» — вместо него маска вида ``sk-…f3a2``.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from pathlib import Path

from . import paths

#: Значения, которые однажды были прочитаны из .env/конфига и подлежат маскированию
_REGISTERED: set[str] = set()

#: Страховка для секретов, которые мы не видели (пользователь вставил в лог сам)
_TOKEN_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}\b"),
    re.compile(r"\bsk-or-v1-[A-Za-z0-9_\-]{6,}\b"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{12,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b\s*[=:]\s*[\"']?([^\s\"',]{8,})"),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),
]

_MIN_SECRET_LEN = 8


def is_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(part in upper for part in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL"))


def register(value: str | None) -> None:
    """Запоминает значение, чтобы оно никогда не попало в вывод."""
    if value and len(value) >= _MIN_SECRET_LEN:
        _REGISTERED.add(value)


def mask_secret(value: str) -> str:
    """``sk-or-v1-abcdef123456`` → ``sk-or-v1-…3456``."""
    if not value:
        return ""
    if len(value) <= 8:
        return "…"
    head = value[:4]
    tail = value[-4:]
    return f"{head}…{tail}"


def mask_text(text: str) -> str:
    """Убирает из текста все известные и похожие на секрет значения."""
    if not text:
        return text
    result = text
    for secret in sorted(_REGISTERED, key=len, reverse=True):
        if secret and secret in result:
            result = result.replace(secret, mask_secret(secret))
    for pattern in _TOKEN_PATTERNS:
        if pattern.groups >= 2:
            result = pattern.sub(lambda m: f"{m.group(1)}={mask_secret(m.group(2))}", result)
        elif pattern.groups == 1:
            result = pattern.sub(lambda m: f"{m.group(1)} {mask_secret(m.group(0).split()[-1])}", result)
        else:
            result = pattern.sub(lambda m: mask_secret(m.group(0)), result)
    return result


def parse_env(text: str) -> dict[str, str]:
    """Простой KEY=VALUE: комментарии, кавычки, экспорт."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name:
            values[name] = value
    return values


def load_env_file(path: Path | None = None, *, apply: bool = True, override: bool = False) -> dict[str, str]:
    """Читает ``.env`` и (по умолчанию) кладёт значения в окружение процесса."""
    env_path = Path(path) if path else paths.env_file_path()
    if not env_path.exists():
        return {}
    try:
        values = parse_env(env_path.read_text(encoding="utf-8"))
    except OSError as exc:
        logging.getLogger("jarvis.secrets").warning("не удалось прочитать %s: %s", env_path, exc)
        return {}
    for name, value in values.items():
        if is_secret_name(name):
            register(value)
        if apply and (override or not os.environ.get(name)):
            os.environ[name] = value
    return values


def save_env_var(name: str, value: str, path: Path | None = None) -> Path:
    """Записывает/обновляет переменную в ``.env``, сохраняя остальные строки."""
    env_path = Path(path) if path else paths.env_file_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(r"^\s*(export\s+)?" + re.escape(name) + r"\s*=")
    replaced = False
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f"{name}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{name}={value}")
    body = "\n".join(lines).rstrip("\n") + "\n"
    env_path.write_text(body, encoding="utf-8")
    try:
        env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    except OSError:  # pragma: no cover - Windows без поддержки chmod
        pass
    register(value)
    os.environ[name] = value
    return env_path


def env_file_is_private(path: Path | None = None) -> bool:
    """Проверка прав 600 для doctor (на Windows проверка всегда успешна)."""
    env_path = Path(path) if path else paths.env_file_path()
    if os.name == "nt" or not env_path.exists():
        return True
    mode = stat.S_IMODE(env_path.stat().st_mode)
    return mode & 0o077 == 0


def reset_registry() -> None:
    """Только для тестов."""
    _REGISTERED.clear()
