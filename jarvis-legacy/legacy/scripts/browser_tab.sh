#!/bin/bash
# Browser tab control via xdotool
action="${1:-next}"

case "$action" in
  next)
    xdotool search --class "firefox" windowactivate key --clearmodifiers ctrl+Tab
    echo "Следующая вкладка"
    ;;
  prev)
    xdotool search --class "firefox" windowactivate key --clearmodifiers ctrl+shift+Tab
    echo "Предыдущая вкладка"
    ;;
  close)
    xdotool search --class "firefox" windowactivate key --clearmodifiers ctrl+w
    echo "Вкладка закрыта"
    ;;
  new)
    xdotool search --class "firefox" windowactivate key --clearmodifiers ctrl+t
    echo "Новая вкладка"
    ;;
  reopen)
    xdotool search --class "firefox" windowactivate key --clearmodifiers ctrl+shift+t
    echo "Восстановлена вкладка"
    ;;
  refresh)
    xdotool search --class "firefox" windowactivate key --clearmodifiers F5
    echo "Обновлено"
    ;;
esac
