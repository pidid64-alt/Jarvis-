#!/bin/bash
# Требует lm_sensors: sudo pacman -S lm_sensors && sudo sensors-detect
if ! command -v sensors >/dev/null 2>&1; then
  echo "Датчики температуры не установлены."
  exit 0
fi
temp=$(sensors 2>/dev/null | grep -m1 -oP '(?<=\+)\d+\.\d+(?=°C)')
if [ -z "$temp" ]; then
  echo "Не удалось прочитать температуру."
else
  echo "Температура процессора: ${temp%.*} градусов."
fi
