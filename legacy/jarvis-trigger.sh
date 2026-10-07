#!/bin/bash
# Привязывается к хоткею (по умолчанию Super+J). Будит демон jarvis.py,
# который затем сам запишет и обработает голосовую команду.
PIDFILE="$HOME/.local/share/jarvis/jarvis.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill -USR1 "$(cat "$PIDFILE")"
else
    notify-send -u critical "Jarvis" "Демон не запущен: systemctl --user status jarvis.service"
fi
