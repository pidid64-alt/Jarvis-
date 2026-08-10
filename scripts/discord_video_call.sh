#!/bin/bash
# Discord video call script
# Usage: discord_video_call.sh <username>

username="${1}"

if [ -z "$username" ]; then
    echo "Ошибка: не указано имя пользователя"
    exit 1
fi

# Check if Discord is running
if ! pgrep -x "discord" > /dev/null; then
    notify-send "Discord" "Запускаю Discord..."
    discord &
    sleep 6
fi

sleep 2

# Find Discord window with retries
for i in {1..5}; do
    discord_window=$(xdotool search --class "discord" 2>/dev/null | head -1)
    if [ -z "$discord_window" ]; then
        discord_window=$(xdotool search --name "Discord" 2>/dev/null | head -1)
    fi

    if [ -n "$discord_window" ]; then
        break
    fi
    sleep 1
done

if [ -z "$discord_window" ]; then
    notify-send "Discord" "Не могу найти окно Discord" -u critical
    exit 1
fi

# Activate Discord window
xdotool windowactivate "$discord_window"
sleep 1

sleep 1

# Open quick switcher
xdotool key --window "$discord_window" --clearmodifiers ctrl+k
sleep 0.8

# Type username
xdotool type --window "$discord_window" --delay 100 "$username"
sleep 1

# Select first result
xdotool key --window "$discord_window" Return
sleep 1.5

# Start video call
xdotool key --window "$discord_window" --clearmodifiers ctrl+shift+v

notify-send "Discord" "Видеозвонок пользователю: $username"
exit 0
