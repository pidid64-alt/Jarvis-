#!/bin/bash
# stop_coding_session.sh — завершает сессию кодинга

STATE_DIR="$HOME/.local/share/jarvis"

# Очищаем файлы состояния
rm -f "$STATE_DIR/coding_session.json"
rm -f "$STATE_DIR/coding_terminal_window.id"

echo "Сессия кодинга завершена."