#!/bin/bash
n=$(journalctl -p 3 -b --no-pager 2>/dev/null | wc -l)
if [ "$n" -le 1 ]; then
  echo "Критичных ошибок в логе не вижу."
else
  echo "В логе этой загрузки: ${n} критичных записей."
fi
