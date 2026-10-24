#!/bin/bash
# Короткая, удобная для озвучки сводка о системе.
uptime_str=$(uptime -p 2>/dev/null | sed 's/^up //')
mem_used=$(free -h | awk 'NR==2{print $3}')
mem_total=$(free -h | awk 'NR==2{print $2}')
load=$(cut -d' ' -f1 /proc/loadavg)

echo "Время работы: ${uptime_str:-неизвестно}. Память занята: ${mem_used} из ${mem_total}. Средняя нагрузка: ${load}."
