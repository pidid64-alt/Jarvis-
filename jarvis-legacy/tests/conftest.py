"""Только для pytest: добавляет корень проекта в sys.path.

Тесты написаны на ``unittest`` и работают без pytest; этот файл нужен лишь
для того, чтобы ``python -m pytest`` из корня проекта тоже находил пакет.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
