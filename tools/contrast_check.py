#!/usr/bin/env python3
"""Проверка контраста текста в палитрах окна Jarvis.

Правило простое: у любого текста отношение контраста к его фону должно быть не
ниже 4.5:1 (WCAG AA для обычного текста). Инструмент берёт палитры из темы окна
и печатает таблицу: пара «текст на фоне», коэффициент и вердикт. Это же правило
проверяется тестом, поэтому палитру нельзя испортить случайной правкой.

Запуск::

    python tools/contrast_check.py            # все темы, краткий отчёт
    python tools/contrast_check.py --all      # показать все пары, включая удачные
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: порог WCAG AA для обычного текста
MIN_RATIO = 4.5


def _channel(value: int) -> float:
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    """Относительная яркость цвета ``#rrggbb``."""
    color = color.lstrip("#")
    red, green, blue = (int(color[index:index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(foreground: str, background: str) -> float:
    """Отношение контраста двух цветов, от 1.0 до 21.0."""
    first, second = luminance(foreground), luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return round((lighter + 0.05) / (darker + 0.05), 2)


#: что на чём лежит: (подпись, ключ текста, ключ фона)
PAIRS = [
    ("основной текст на фоне окна", "text", "bg"),
    ("основной текст на панели", "text", ("surface", "panel")),
    ("приглушённый текст на фоне", "muted", "bg"),
    ("приглушённый текст на панели", "muted", ("surface", "panel")),
    ("текст на поле ввода", "text", ("input_bg", "entry")),
    ("подпись поля ввода", "muted", ("input_bg", "entry")),
    ("текст на акцентной кнопке", ("on_accent", "btn_text"), "accent"),
    ("сообщение пользователя", ("user_text", "user"), ("user_bg", "entry")),
    ("ответ ассистента", ("assistant_text", "jarvis"), ("assistant_bg", "entry")),
    ("подпись на «таблетке»", "muted", "chip_bg"),
]


def pick(palette: dict[str, str], key) -> str | None:
    """Взять цвет по имени, поддерживая и новый, и прежний набор ключей."""
    for name in (key if isinstance(key, tuple) else (key,)):
        if palette.get(name):
            return palette[name]
    return None


def audit(palette: dict[str, str], *, show_all: bool = True) -> list[tuple[str, str, float, bool]]:
    rows = []
    for title, fg_key, bg_key in PAIRS:
        fg, bg = pick(palette, fg_key), pick(palette, bg_key)
        if not fg or not bg:
            continue
        ratio = contrast(fg, bg)
        rows.append((title, f"{fg} на {bg}", ratio, ratio >= MIN_RATIO))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверить контраст палитр окна")
    parser.add_argument("--theme", default="", help="только одна тема (dark/light)")
    parser.add_argument("--all", action="store_true", help="показывать и удачные пары")
    parser.add_argument("--quiet", action="store_true", help="только итог")
    args = parser.parse_args(argv)

    try:
        from jarvis.interfaces.theme import THEMES
    except ImportError:
        from jarvis.interfaces.gui import PALETTES as THEMES  # старый источник палитр

    bad = 0
    empty = 0
    for name, palette in THEMES.items():
        if args.theme and name != args.theme:
            continue
        if not args.quiet:
            print(f"\nТема «{name}» (порог {MIN_RATIO}:1)")
        for title, pair, ratio, ok in audit(palette, show_all=args.all):
            if ok and not (args.all and not args.quiet):
                continue
            mark = "ок  " if ok else "МАЛО"
            if not args.quiet:
                print(f"  {mark} {ratio:>5}:1  {title}  ({pair})")
            if not ok:
                bad += 1
        checked = len(audit(palette))
        if not checked:
            empty += 1
            print(f"  нечего проверять: в палитре нет ожидаемых ключей ({', '.join(sorted(palette))})")
        elif not args.quiet:
            print(f"  проверено пар: {checked}, ниже порога: {sum(1 for row in audit(palette) if not row[3])}")

    if empty:
        print("\nИтог: не удалось проверить все темы — в палитрах нет ожидаемых ключей")
        return 2
    print("\nИтог: " + ("всё в порядке" if not bad else f"пар ниже порога — {bad}"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
