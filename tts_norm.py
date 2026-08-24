#!/usr/bin/env python3
"""
tts_norm.py
-----------
Нормализация текста перед озвучкой через Piper TTS.

Piper (русский голос) плохо читает:
  - размеры вида "3.6Gi", "512M", "2G";
  - десятичные точки ("ноль целых..." не умеет) -> нужна запятая;
  - знак процента проглатывается -> "85 процентов";
  - время "09:12" и даты "2026-08-24" читает побожно;
  - английский uptime "up 43 minutes" вообще не по-русски.

Здесь всё это переводится в человеческую русскую речь. Модуль без
зависимостей, используется в jarvis.speak() — единой точке озвучки.
"""

import re

# --- русские множественные формы -------------------------------------------

def _plural(n: int, one: str, few: str, many: str) -> str:
    """1 процент / 2 процента / 5 процентов."""
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    d = n % 10
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many


_MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
               "июля", "августа", "сентября", "октября", "ноября", "декабря"]

# Байтовые суффиксы -> базовое слово ("гигабайт"): от длинных к коротким.
_SIZE_UNITS = [
    ("KiB", "килобайт"), ("MiB", "мегабайт"),
    ("GiB", "гигабайт"), ("TiB", "терабайт"),
    ("KB", "килобайт"), ("MB", "мегабайт"),
    ("GB", "гигабайт"), ("TB", "терабайт"),
    ("Ki", "килобайт"), ("Mi", "мегабайт"),
    ("Gi", "гигабайт"), ("Ti", "терабайт"),
    ("K", "килобайт"), ("M", "мегабайт"),
    ("G", "гигабайт"), ("T", "терабайт"),
]
_SIZE_MAP = dict(_SIZE_UNITS)

_SIZE_RE = re.compile(
    r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s?(KiB|MiB|GiB|TiB|KB|MB|GB|TB|Ki|Mi|Gi|Ti|K|M|G|T)(?![\w])")

_PERCENT_RE = re.compile(r"(\d+)\s*%")

_TIME_RE = re.compile(r"(?<![\d:.])([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?![\d:])")

_ISO_DATE_RE = re.compile(r"(?<![\d])(\d{4})-(\d{2})-(\d{2})(?![\d])")
_DOT_DATE_RE = re.compile(r"(?<![\d])(\d{1,2})\.(\d{2})\.(\d{4})(?![\d])")

# IPv4 — маскируем, чтобы точки внутри адреса не превратились в запятые.
_IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")

# Английский uptime: "up 43 minutes", "up 2 hours, 15 min", "up 3 days".
_UPTIME_EN_RE = re.compile(
    r"\bup\s+(\d+)\s*((?:week|day|hour|min(?:ute)?)s?)"
    r"(?:,?\s*(\d+)\s*((?:week|day|hour|min(?:ute)?)s?))?", re.IGNORECASE)

_UPTIME_RU = {
    "week": ("неделю", "недели", "недель"),
    "day": ("день", "дня", "дней"),
    "hour": ("час", "часа", "часов"),
    "min": ("минуту", "минуты", "минут"),
}


# --- замены -----------------------------------------------------------------

def _size_repl(m: re.Match) -> str:
    num_raw, unit = m.group(1), m.group(2)
    word = _SIZE_MAP.get(unit)
    if not word:
        return m.group(0)
    num = float(num_raw.replace(",", "."))
    whole = int(num)
    # формы: 1 гигабайт / 2-4 гигабайта / 5+ гигабайт; дробное -> 'гигабайта'
    one, few, many = word, word + "а", word
    if num == whole:
        return f"{whole} {_plural(whole, one, few, many)}"
    return f"{num_raw.replace('.', ',')} {few}"


def _percent_repl(m: re.Match) -> str:
    n = int(m.group(1))
    return f"{n} {_plural(n, 'процент', 'процента', 'процентов')}"


def _time_repl(m: re.Match) -> str:
    h, mi = int(m.group(1)), int(m.group(2))
    sec = m.group(3)
    out = f"{h} {_plural(h, 'час', 'часа', 'часов')}"
    if mi:
        out += f" {mi} {_plural(mi, 'минута', 'минуты', 'минут')}"
    else:
        out += " ровно"
    if sec:
        out += f" {int(sec)} {_plural(int(sec), 'секунда', 'секунды', 'секунд')}"
    return out


def _date_repl(d: int, mo: int, y: int) -> str:
    month = _MONTHS_GEN[mo - 1] if 1 <= mo <= 12 else "?"
    return f"{d} {month} {y} года"


def _uptime_repl(m: re.Match) -> str:
    def piece(n_s: str | None, kind: str | None) -> str:
        if not n_s or not kind:
            return ""
        k = kind.lower()
        if k.startswith("min"):
            base = "min"
        elif k.startswith("wee"):
            base = "week"
        else:
            base = k.rstrip("s")
        one, few, many = _UPTIME_RU.get(base, (kind, kind, kind))
        return f"{n_s} {_plural(int(n_s), one, few, many)}"

    parts = [piece(m.group(1), m.group(2)), piece(m.group(3), m.group(4))]
    return "up " + " ".join(p for p in parts if p)


def normalize_for_tts(text: str) -> str:
    """Переводит технический текст в удобочитаемую для Piper форму."""
    if not text:
        return text

    s = text

    # 0) Маскируем IPv4.
    masks: dict[str, str] = {}

    def _mask(m: re.Match) -> str:
        key = f"\x00IP{len(masks)}\x00"
        masks[key] = m.group(0)
        return key

    s = _IPV4_RE.sub(_mask, s)

    # 1) Даты (до времени — порядок не критичен, но пусть будет раньше).
    s = _ISO_DATE_RE.sub(lambda m: _date_repl(int(m.group(3)), int(m.group(2)), int(m.group(1))), s)
    s = _DOT_DATE_RE.sub(lambda m: _date_repl(int(m.group(1)), int(m.group(2)), int(m.group(3))), s)

    # 2) Время HH:MM[:SS] -> "9 часов 12 минут".
    s = _TIME_RE.sub(_time_repl, s)

    # 3) Английский uptime -> русскими словами.
    s = _UPTIME_EN_RE.sub(_uptime_repl, s)

    # 4) Размеры памяти/диска: 3.6Gi -> "3,6 гигабайта", 512M -> "512 мегабайт".
    s = _SIZE_RE.sub(_size_repl, s)

    # 5) Проценты: 85% -> "85 процентов".
    s = _PERCENT_RE.sub(_percent_repl, s)

    # 6) Десятичная точка -> запятая (только между цифрами).
    s = re.sub(r"(\d)\.(\d)", r"\1,\2", s)

    # 7) Температура "23°C"/"23°C" -> слова с правильной формой.
    s = re.sub(r"(\d+)\s*°\s*[CF]?",
               lambda m: f"{m.group(1)} {_plural(int(m.group(1)), 'градус', 'градуса', 'градусов')}",
               s)

    # 8) Убираем возможное задвоение после времени "14:00 ровно".
    s = re.sub(r"\bровно\s+ровно\b", "ровно", s)

    # Восстанавливаем IP-адреса
    for key, val in masks.items():
        s = s.replace(key, val)

    return s


if __name__ == "__main__":
    import sys
    for line in sys.stdin.read().splitlines():
        print(normalize_for_tts(line))
