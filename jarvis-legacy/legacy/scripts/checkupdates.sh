#!/bin/bash
if command -v checkupdates >/dev/null 2>&1; then
  n=$(checkupdates 2>/dev/null | wc -l)
else
  n=$(pacman -Qu 2>/dev/null | wc -l)
fi
if [ "$n" -eq 0 ]; then
  echo "Обновлений нет."
else
  echo "Доступно обновлений: ${n}."
fi
