#!/bin/bash
# Browser zoom control
action="${1:-in}"

case "$action" in
  in)
    xdotool search --class "firefox" windowactivate key --clearmodifiers ctrl+plus
    echo "Увеличено"
    ;;
  out)
    xdotool search --class "firefox" windowactivate key --clearmodifiers ctrl+minus
    echo "Уменьшено"
    ;;
  reset)
    xdotool search --class "firefox" windowactivate key --clearmodifiers ctrl+0
    echo "Сброшено"
    ;;
esac
