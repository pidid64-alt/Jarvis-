#!/bin/bash
# Extended system information
info_type="${1}"

case "$info_type" in
  cpu_model)
    lscpu | grep "Имя модели" | cut -d: -f2 | xargs
    ;;
  total_memory)
    free -h | awk 'NR==2{print $2}'
    ;;
  installed_date)
    ls -lct /etc | tail -1 | awk '{print $6, $7, $8}'
    ;;
  arch)
    uname -m
    ;;
  desktop)
    echo $XDG_CURRENT_DESKTOP
    ;;
  session_type)
    echo $XDG_SESSION_TYPE
    ;;
  default_browser)
    xdg-settings get default-web-browser
    ;;
esac
