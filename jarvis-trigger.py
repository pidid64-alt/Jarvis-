#!/usr/bin/env python3
"""
jarvis-trigger.py
-----------------
Кроссплатформенный триггер пробуждения демона jarvis.py (аналог
jarvis-trigger.sh, привязываемый к хоткею).

На любой платформе пишет trigger-файл (демон подхватывает его за <=0.3с);
дополнительно на POSIX шлёт SIGUSR1 — мгновенно, как раньше.

Запуск с хоткея Windows (Win+J) выполняет hotkey-win.py, который зовёт
эту же логику; скрипт нужен для ручного запуска/отладки и Linux-овского
перехода на единый механизм.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import platform_support as plat  # noqa: E402


def main() -> int:
    plat.fire_trigger()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
