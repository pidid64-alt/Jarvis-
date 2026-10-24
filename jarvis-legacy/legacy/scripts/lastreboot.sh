#!/bin/bash
# Когда загрузили систему + сколько уже работает.
boot=$(who -b 2>/dev/null | awk '{print $3, $4}')
up=$(uptime -p 2>/dev/null | sed 's/^up //')
if [ -z "$boot" ]; then
  echo "Не удалось узнать время последней загрузки."
  exit 0
fi
echo "Последняя загрузка: ${boot}. Работаем уже ${up:-неизвестно сколько}."
