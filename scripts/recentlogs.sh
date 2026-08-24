#!/bin/bash
# Последние критичные (err и выше) записи журнала текущей загрузки —
# с указанием времени, источника и сути ошибки. Формат для озвучки:
# цифры/даты нормализует tts_norm.py в jarvis.speak().

mapfile -t lines < <(journalctl -p 3 -b --no-pager --output=short 2>/dev/null | grep -v '^-- ' | tail -5)

n=${#lines[@]}
total=$(journalctl -p 3 -b --no-pager 2>/dev/null | grep -vc '^-- ')

if [ "$total" -eq 0 ]; then
  echo "Критичных ошибок в логе этой загрузки нет."
  exit 0
fi

if [ "$total" -gt "$n" ]; then
  echo "Критичных ошибок в логе: ${total}. Вот последние ${n}:"
elif [ "$n" -eq 1 ]; then
  echo "В логе этой загрузки одна критичная запись:"
else
  echo "Критичных ошибок в логе: ${n}:"
fi

for l in "${lines[@]}"; do
  # "авг 24 16:40:03 archlinux (python3)[22244]: jarvis.service: Failed..." ->
  # "авг 24 16:40:03: jarvis.service: Failed..."
  msg=$(echo "$l" | sed -E 's/^([^ ]+ [^ ]+ [^ ]+) [^ ]+\[[0-9]+\]: /\1: /' | cut -c1-160)
  echo "$msg"
done
