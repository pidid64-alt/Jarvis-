#!/bin/bash
# start_coding_session.sh — открывает терминал с 2 вкладками:
#  1. omniroute
#  2. claude
# Возвращает ID окна терминала для последующего ввода через xdotool.

cd "$HOME" || exit 1

# Запускаем xfce4-terminal в фоне и сразу получаем его PID
xfce4-terminal \
  --tab -T "omniroute" -e "bash -c 'omniroute; exec bash'" \
  --tab -T "claude" -e "bash -c 'claude; exec bash'" &

TERM_PID=$!

# Ждём, пока окно появится (макс 10 секунд)
for i in {1..50}; do
    WINDOW_ID=$(xdotool search --pid "$TERM_PID" --class "xfce4-terminal" 2>/dev/null | head -1)
    if [ -n "$WINDOW_ID" ]; then
        break
    fi
    sleep 0.2
done

if [ -z "$WINDOW_ID" ]; then
    echo "Не удалось найти окно терминала (PID: $TERM_PID)"
    exit 1
fi

# Сохраняем ID окна в файл для следующей команды
echo "$WINDOW_ID" > "$HOME/.local/share/jarvis/coding_terminal_window.id"

# Сохраняем состояние сессии кодинга (JSON)
cat > "$HOME/.local/share/jarvis/coding_session.json" <<EOF
{"active": true, "window_id": $WINDOW_ID}
EOF

# Ждём немного, чтобы вкладки успели загрузиться
sleep 1.0

# Переключаемся на вкладку claude (ctrl+Page_Down — следующая вкладка)
xdotool windowactivate --sync "$WINDOW_ID"
xdotool key --clearmodifiers ctrl+Page_Down

echo "Терминал готов. Окно: $WINDOW_ID"