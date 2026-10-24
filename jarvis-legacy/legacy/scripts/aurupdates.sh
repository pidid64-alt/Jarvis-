#!/bin/bash
if command -v yay >/dev/null 2>&1; then
  n=$(yay -Qua 2>/dev/null | wc -l)
elif command -v paru >/dev/null 2>&1; then
  n=$(paru -Qua 2>/dev/null | wc -l)
else
  echo "AUR-хелпер не найден."
  exit 0
fi
if [ "$n" -eq 0 ]; then
  echo "Обновлений AUR нет."
else
  echo "Обновлений AUR: ${n}."
fi
