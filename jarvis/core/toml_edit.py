"""Точечная правка TOML с сохранением комментариев и форматирования.

Зачем свой писатель вместо зависимости: файл настроек правит GUI, и терять
комментарии при каждом сохранении нельзя. Мы не переписываем файл целиком —
меняем только нужную строку, остальное остаётся как было.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any

_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(#.*)?$")


class TomlEditError(ValueError):
    """Значение нельзя записать в TOML или структура файла неожиданна."""


def format_key(key: str) -> str:
    return key if _BARE_KEY_RE.match(key) else format_string(key)


def format_string(value: str) -> str:
    out = ['"']
    for char in value:
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif char == "\r":
            out.append("\\r")
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def format_value(value: Any) -> str:
    """TOML-представление скаляра или массива скаляров."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return format_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(format_value(item) for item in value) + "]"
    raise TomlEditError(f"нельзя записать в TOML значение типа {type(value).__name__}")


def parse(text: str) -> dict[str, Any]:
    return tomllib.loads(text) if text.strip() else {}


def _split_path(dotted: str | tuple[str, ...]) -> list[str]:
    parts = list(dotted) if isinstance(dotted, (tuple, list)) else dotted.split(".")
    if not parts or any(not part for part in parts):
        raise TomlEditError(f"пустой путь настройки: {dotted!r}")
    return parts


def _find_section(lines: list[str], section: list[str]) -> tuple[int, int]:
    """Возвращает (начало, конец) блока секции; для корня — (0, первый заголовок)."""
    wanted = ".".join(section)
    start = None
    header_index = None
    for index, line in enumerate(lines):
        match = _HEADER_RE.match(line)
        if not match:
            continue
        name = match.group(1).strip()
        if start is None:
            if name == wanted:
                start = index + 1
                header_index = index
                continue
            if header_index is None:
                continue
        if start is not None:
            return start, index
    if start is None:
        return header_index if header_index is not None else -1, -1
    return start, len(lines)


def set_value(text: str, dotted: str | tuple[str, ...], value: Any) -> str:
    """Возвращает новый текст файла со значением ``dotted`` = ``value``."""
    parts = _split_path(dotted)
    *section, key = parts
    literal = format_value(value)
    lines = text.splitlines()

    start, end = _find_section(lines, section) if section else (0, _root_end(lines))
    if start < 0:  # секции нет — создаём её в конце файла
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{'.'.join(section)}]")
        lines.append(f"{format_key(key)} = {literal}")
        return "\n".join(lines) + "\n"

    key_re = re.compile(r"^\s*" + re.escape(format_key(key)) + r"\s*=")
    for index in range(start, end):
        if key_re.match(lines[index]):
            comment = ""
            _, _, tail = lines[index].partition("#")
            if "#" in lines[index]:
                comment = "  #" + tail.rstrip()
            lines[index] = f"{format_key(key)} = {literal}{comment}"
            return "\n".join(lines) + "\n"

    insert_at = end
    while insert_at > start and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, f"{format_key(key)} = {literal}")
    return "\n".join(lines) + "\n"


def remove_key(text: str, dotted: str | tuple[str, ...]) -> str:
    """Удаляет ключ из файла вместе с его строкой.

    Нужно там, где значение меняет форму: например, пустой массив ``rules = []``
    превращается в массив таблиц ``[[autonomy.rules]]`` — TOML не разрешает
    объявить одно и то же имя двумя способами.
    """
    parts = _split_path(dotted)
    *section, key = parts
    lines = text.splitlines()
    start, end = _find_section(lines, section) if section else (0, _root_end(lines))
    if start < 0:
        return text
    key_re = re.compile(r"^\s*" + re.escape(format_key(key)) + r"\s*=")
    for index in range(start, end):
        if key_re.match(lines[index]):
            del lines[index]
            break
    return "\n".join(lines) + "\n"


def _root_end(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if _HEADER_RE.match(line):
            return index
    return len(lines)
