#!/bin/bash
if ! command -v sensors >/dev/null 2>&1; then
  echo "Датчики не установлены."
  exit 0
fi
rpm=$(sensors 2>/dev/null | grep -m1 -oP '\d+(?=\s*RPM)')
if [ -z "$rpm" ]; then
  echo "Датчик оборотов вентилятора не найден."
else
  echo "Вентилятор: ${rpm} оборотов в минуту."
fi
