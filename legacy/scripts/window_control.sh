#!/bin/bash
# Advanced window control
action="${1}"

case "$action" in
  tile_left)
    wmctrl -r :ACTIVE: -b remove,maximized_vert,maximized_horz
    xdotool getactivewindow windowmove 0 0 windowsize $(xdpyinfo | awk '/dimensions/{print $2}' | cut -d'x' -f1 | awk '{print int($1/2)}') 100%
    echo "Прижато влево"
    ;;
  tile_right)
    width=$(xdpyinfo | awk '/dimensions/{print $2}' | cut -d'x' -f1)
    half=$((width / 2))
    wmctrl -r :ACTIVE: -b remove,maximized_vert,maximized_horz
    xdotool getactivewindow windowmove $half 0 windowsize $half 100%
    echo "Прижато вправо"
    ;;
  center)
    wmctrl -r :ACTIVE: -b remove,maximized_vert,maximized_horz
    screen_w=$(xdpyinfo | awk '/dimensions/{print $2}' | cut -d'x' -f1)
    screen_h=$(xdpyinfo | awk '/dimensions/{print $2}' | cut -d'x' -f2)
    win_w=$((screen_w * 70 / 100))
    win_h=$((screen_h * 80 / 100))
    x=$(( (screen_w - win_w) / 2 ))
    y=$(( (screen_h - win_h) / 2 ))
    xdotool getactivewindow windowsize $win_w $win_h windowmove $x $y
    echo "Отцентровано"
    ;;
  always_on_top)
    wmctrl -r :ACTIVE: -b toggle,above
    echo "Переключено поверх всех окон"
    ;;
  fullscreen)
    wmctrl -r :ACTIVE: -b toggle,fullscreen
    echo "Переключён полноэкранный режим"
    ;;
  move_workspace)
    workspace="${2:-1}"
    wmctrl -r :ACTIVE: -t $((workspace - 1))
    echo "Перемещено на рабочий стол $workspace"
    ;;
esac
