#!/bin/bash
gov=$(cpupower frequency-info -p 2>/dev/null | grep -oP '(?<=governor ")[a-z]+' | head -1)
if [ -z "$gov" ]; then
  gov=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)
fi
case "$gov" in
  performance) echo "Режим: производительность." ;;
  powersave) echo "Режим: энергосбережение." ;;
  "" ) echo "Не удалось определить режим." ;;
  *) echo "Режим: ${gov}." ;;
esac
