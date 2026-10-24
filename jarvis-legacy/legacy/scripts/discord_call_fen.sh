#!/bin/bash
# Script to call Fen in Discord
# Opens Discord, navigates to user, and initiates call

# Check if Discord is running
discord_running=false
if pgrep -x "discord" > /dev/null; then
    discord_running=true
fi

# If Discord not running, start it
if [ "$discord_running" = false ]; then
    discord > /dev/null 2>&1 &
    sleep 8
else
    # Discord already running, minimal wait
    sleep 0.5
fi

# Find main Discord window with retries
discord_window=""
for i in {1..10}; do
    discord_window=$(xdotool search --class "discord" 2>/dev/null | head -1)
    if [ -z "$discord_window" ]; then
        discord_window=$(xdotool search --name "Discord" 2>/dev/null | head -1)
    fi

    if [ -n "$discord_window" ]; then
        break
    fi
    sleep 0.5
done

if [ -z "$discord_window" ]; then
    notify-send "Discord" "Не могу найти окно Discord" -u critical
    exit 1
fi

# Activate the Discord window
xdotool windowactivate "$discord_window"
sleep 0.5

# If Discord just started, wait a bit more
if [ "$discord_running" = false ]; then
    sleep 2
fi

# Open quick switcher (Ctrl+K)
xdotool key --window "$discord_window" --clearmodifiers ctrl+k
sleep 0.5

# Type 'fen' to search
xdotool type --window "$discord_window" --delay 50 "fen"
sleep 0.7

# Press Enter to select first result (opens DM)
xdotool key --window "$discord_window" Return
sleep 1.5

# Now find the DM window (has @ in name)
dm_window=""
for i in {1..10}; do
    dm_window=$(xdotool search --class discord 2>/dev/null | while read win; do
        name=$(xdotool getwindowname $win 2>/dev/null)
        geom=$(xdotool getwindowgeometry $win 2>/dev/null | grep Geometry | awk '{print $2}')
        width=$(echo $geom | cut -d'x' -f1)

        if [[ "$name" == *"@"*"fen"* ]] && [ "$width" -gt 500 ]; then
            echo "$win"
            break
        fi
    done)

    if [ -n "$dm_window" ]; then
        break
    fi
    sleep 0.3
done

# If DM window found, click call button
if [ -n "$dm_window" ]; then
    # Get window geometry
    eval $(xdotool getwindowgeometry --shell "$dm_window")

    # Call button coordinates (user provided exact position)
    call_x=1476
    call_y=72

    # Click call button
    xdotool mousemove --window "$dm_window" $call_x $call_y
    sleep 0.2
    xdotool click 1
else
    # Fallback: try hotkey on main window
    xdotool key --window "$discord_window" --clearmodifiers ctrl+apostrophe
fi

notify-send "Discord" "Звоню Фену"
exit 0
