#!/usr/bin/env python3
"""Проверка контраста палитр окна Jarvis.

Правило простое: весь текст — не ниже 4.5:1 к своему фону (WCAG AA), состояния
и фокус — не ниже 3:1, декоративные границы — лишь различимы. Инструмент берёт
палитры **из самой темы** (``jarvis.interfaces.theme``), поэтому таблица не
может разойтись с окном: правишь цвет в теме — проверка сразу это видит.

Запуск::

    python tools/contrast_check.py            # кратко: только проблемные пары
    python tools/contrast_check.py --all      # показать все пары
    python tools/contrast_check.py --quiet    # только итог
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from jarvis.interfaces import theme  # noqa: E402

#: порог WCAG AA для обычного текста — оставлено для совместимости с прежними вызовами
MIN_RATIO = theme.TEXT_CONTRAST_MIN

#: функцию контраста тоже берём из темы: одна реализация на всё
contrast = theme.contrast
luminance = theme.luminance
audit = theme.audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверить контраст палитр окна")
    parser.add_argument("--theme", default="", help="только одна тема (dark/light)")
    parser.add_argument("--all", action="store_true", help="показывать и удачные пары")
    parser.add_argument("--quiet", action="store_true", help="только итог")
    args = parser.parse_args(argv)

    bad = 0
    for name in theme.PALETTES:
        if args.theme and name != args.theme:
            continue
        rows = theme.audit(name)
        if not args.quiet:
            print(f"\nТема «{name}» (текст {theme.TEXT_CONTRAST_MIN}:1, "
                  f"состояния {theme.GRAPHIC_CONTRAST_MIN}:1)")
        for title, ratio, threshold, ok in rows:
            if ok and not args.all:
                continue
            if not args.quiet:
                print(f"  {'ок  ' if ok else 'МАЛО'} {ratio:>6}:1  при пороге {threshold}:1  {title}")
            if not ok:
                bad += 1
        if not args.quiet:
            below = sum(1 for row in rows if not row[3])
            print(f"  проверено пар: {len(rows)}, ниже порога: {below}")

    print("\nИтог: " + ("всё в порядке" if not bad else f"пар ниже порога — {bad}"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
