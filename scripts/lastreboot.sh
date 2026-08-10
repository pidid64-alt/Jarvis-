#!/bin/bash
info=$(who -b 2>/dev/null | awk '{print $3, $4}')
if [ -z "$info" ]; then
  echo "Не удалось узнать время последней загрузки."
else
  echo "Последняя загрузка: ${info}."
fi
