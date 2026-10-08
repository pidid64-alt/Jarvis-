#!/bin/bash
# Hang up Discord call

# Find Discord window
discord_window=$(xdotool search --class "discord" 2>/dev/null | head -1)
if [ -z "$discord_window" ]; then
    discord_window=$(xdotool search --name "Discord" 2>/dev/null | head -1)
fi

if [ -z "$discord_window" ]; then
    echo "Discord window not found"
    exit 1
fi

# Activate Discord window
xdotool windowactivate "$discord_window"
sleep 0.5

# Hang up (Escape key ends call in Discord)
xdotool key --window "$discord_window" Escape

notify-send "Discord" "Звонок завершён"
exit 0
