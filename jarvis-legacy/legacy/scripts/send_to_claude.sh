#!/bin/bash
# send_to_claude.sh — отправляет текст в вкладку claude терминала кодинга
# Использование: send_to_claude.sh "текст для отправки"

TEXT="$1"
if [ -z "$TEXT" ]; then
    echo "Usage: $0 \"text to send\""
    exit 1
fi

WINDOW_FILE="$HOME/.local/share/jarvis/coding_terminal_window.id"
if [ ! -f "$WINDOW_FILE" ]; then
    echo "Нет сохранённого окна терминала. Сначала скажи 'давай кодить'."
    exit 1
fi

WINDOW_ID=$(cat "$WINDOW_FILE")

# Проверяем, что окно ещё существует
if ! xdotool getwindowname "$WINDOW_ID" >/dev/null 2>&1; then
    echo "Окно терминала больше не существует"
    rm -f "$WINDOW_FILE"
    exit 1
fi

# Активируем окно
xdotool windowactivate --sync "$WINDOW_ID"

# Переключаемся на вкладку claude (ctrl+Page_Down — следующая вкладка)
# Если мы в omniroute (первая вкладка), один раз переключит на claude
xdotool key --clearmodifiers ctrl+Page_Down

# Небольшая пауза перед вводом
sleep 0.3

# Вводим текст через xdotool type
xdotool type --clearmodifiers --delay 10 "$TEXT"

# Нажимаем Enter для отправки
xdotool key --clearmodifiers Return

echo "Отправлено в claude: $TEXT"