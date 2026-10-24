#!/bin/bash
# Статус systemd сервисов (failed, running count)

failed=$(systemctl --failed --no-legend 2>/dev/null | wc -l)
running=$(systemctl list-units --type=service --state=running --no-legend 2>/dev/null | wc -l)
total=$(systemctl list-units --type=service --no-legend 2>/dev/null | wc -l)

echo "Сервисов всего: $total, работает: $running, упало: $failed"

if [ "$failed" -gt 0 ]; then
    echo "Упавшие сервисы:"
    systemctl --failed --no-legend 2>/dev/null | awk '{print "  " $2}'
fi