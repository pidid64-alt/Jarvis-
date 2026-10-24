#!/usr/bin/env python3
"""Печатает только человекочитаемый text проверки health_checks.

Нужен для speak_output-команд: autonomy требует JSON с metrics, а голосу
не нужен JSON. Дублирует выбор/исключение платформы из health_checks.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from health_checks import collect  # noqa: E402


def main():
    if len(sys.argv) != 2:
        print("usage: health_text.py <check>")
        raise SystemExit(2)
    try:
        result = collect(sys.argv[1])
    except Exception as e:  # noqa: BLE001 — голосом просто говорим «недоступно»
        print(f"Данные проверки недоступны: {e}")
        return
    print((result.get("text") or "Данные проверки недоступны.").strip())


if __name__ == "__main__":
    main()
