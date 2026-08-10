#!/bin/bash
# Полная сводка системы для озвучки

echo "=== Системная сводка ==="
echo "Хост: $(hostname)"
echo "Аптайм: $(uptime -p | sed 's/^up //')"
echo "Ядро: $(uname -r)"
echo "Архитектура: $(uname -m)"
echo ""
echo "=== CPU ==="
cpu_model=$(lscpu | grep "Model name" | cut -d: -f2 | xargs)
cpu_cores=$(nproc)
cpu_load=$(cut -d' ' -f1 /proc/loadavg)
echo "Модель: ${cpu_model:-неизвестно}"
echo "Ядер: $cpu_cores"
echo "Нагрузка: $cpu_load"
echo ""
echo "=== Память ==="
free -h | awk 'NR==2{printf "Использовано: %s из %s (%.1f%%)\n", $3, $2, $3/$2*100}'
echo ""
echo "=== Диск ==="
df -h / | awk 'NR==2{printf "Корневой раздел: %s из %s (%s)\n", $3, $2, $5}'
echo ""
echo "=== Сеть ==="
ip -br a | grep -v "lo\|DOWN" | awk '{print $1 ": " $3}'