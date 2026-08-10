#!/bin/bash
# Считает упавшие systemd-юниты, и пользовательские, и системные.
user_failed=$(systemctl --user --failed --no-legend 2>/dev/null | wc -l)
sys_failed=$(systemctl --failed --no-legend 2>/dev/null | wc -l)
total=$((user_failed + sys_failed))
if [ "$total" -eq 0 ]; then
  echo "Сломанных сервисов нет."
else
  echo "Сломанных сервисов: ${total}. Пользовательских: ${user_failed}, системных: ${sys_failed}."
fi
