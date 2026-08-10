#!/bin/bash
bat_path=$(ls -d /sys/class/power_supply/BAT* 2>/dev/null | head -1)
if [ -z "$bat_path" ]; then
  echo "Батарея не найдена."
  exit 0
fi
bat=$(cat "$bat_path/capacity" 2>/dev/null)
status=$(cat "$bat_path/status" 2>/dev/null)
case "$status" in
  Charging) status_ru="заряжается" ;;
  Discharging) status_ru="разряжается" ;;
  Full) status_ru="полностью заряжена" ;;
  *) status_ru="$status" ;;
esac
echo "Заряд батареи: ${bat} процентов, ${status_ru}."
