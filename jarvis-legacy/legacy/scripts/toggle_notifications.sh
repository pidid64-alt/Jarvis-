#!/bin/bash
# Переключение Do Not Disturb режима в xfce4-notifyd
CONFIG="$HOME/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-notifyd.xml"

if [ ! -f "$CONFIG" ]; then
    echo "Конфигурация уведомлений не найдена."
    exit 0
fi

current=$(xfconf-query -c xfce4-notifyd -p /do-not-disturb 2>/dev/null)

if [ "$current" = "true" ]; then
    xfconf-query -c xfce4-notifyd -p /do-not-disturb -s false
    echo "Уведомления включены."
else
    xfconf-query -c xfce4-notifyd -p /do-not-disturb -s true
    echo "Уведомления выключены."
fi
